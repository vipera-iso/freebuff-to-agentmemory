"""
Module ingestion: Load dữ liệu từ nhiều định dạng.

- PDF / Word / HTML / còn lại: Unstructured
- PPTX: pptx-tools MCP server (JSON-RPC 2.0 Streamable HTTP tại :3001/mcp),
  fallback python-pptx nếu MCP không liên lạc được (docstring `_load_pptx_local`).

  LƯU Ý: upstream jongalloway/pptx-tools CHỈ hỗ trợ stdio transport. Endpoint
  HTTP :3001/mcp do bridge `scripts/pptx_mcp_bridge.sh` (supergateway) cung cấp,
  bọc stdio → Streamable HTTP. Tên tham số tool là camelCase (`filePath`,
  `slideIndex`) và `slideIndex` là 0-based.

Output thống nhất: list[llama_index.core.schema.Document] — đầu vào cho parsing/cleaner.
"""
import json
import logging
import os
import re

import requests
from llama_index.core.schema import Document
from unstructured.partition.auto import partition
from unstructured.partition.docx import partition_docx
from unstructured.partition.html import partition_html
from unstructured.partition.pdf import partition_pdf

# Bật huge_tree cho HTML lồng sâu (depth > 256)
os.environ.setdefault("UNSTRUCTURED_HTML_HUGE_TREE", "1")

logger = logging.getLogger(__name__)


class McpClient:
    """MCP client tối giản: initialize handshake + tools/call qua Streamable HTTP."""

    def __init__(self, url: str):
        self.url = url
        self.session_id: str | None = None
        self._id = 0

    def _post(self, payload: dict) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        resp = requests.post(self.url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        body = resp.text.strip()
        if not body:
            return {}
        if body.startswith("{"):
            return json.loads(body)
        # Streamable HTTP có thể trả SSE ("event: message\ndata: {...}")
        for line in body.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise ValueError(f"Không parse được response MCP: {body[:200]}")

    def initialize(self) -> None:
        self._id += 1
        self._post({
            "jsonrpc": "2.0", "id": self._id, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "data-pipeline", "version": "0.1.0"},
            },
        })
        # notification — server có thể trả 202 rỗng
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def call_tool(self, name: str, arguments: dict) -> dict:
        self._id += 1
        data = self._post({
            "jsonrpc": "2.0", "id": self._id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        if "error" in data:
            raise RuntimeError(f"MCP tool {name} lỗi: {data['error']}")
        content = data.get("result", {}).get("content", [])
        text = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"raw": text}


def _element_to_document(el) -> Document:
    """Chuyển một Unstructured Element thành Document."""
    meta = getattr(el, "metadata", None)
    return Document(
        text=getattr(el, "text", "") or "",
        metadata={
            "source": getattr(meta, "filename", "unknown"),
            "page_number": getattr(meta, "page_number", None),
            "slide_number": getattr(meta, "slide_number", None),
            "element_type": type(el).__name__,
        },
    )


class DocumentLoader:
    """Loader thống nhất: mọi định dạng → list[Document]."""

    def __init__(self, pptx_mcp_url="http://localhost:3001/mcp"):
        self.pptx_mcp_url = pptx_mcp_url

    def load(self, file_path: str) -> list[Document]:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pptx":
            return self._load_pptx(file_path)
        if ext == ".pdf":
            elements = partition_pdf(file_path, strategy="hi_res")
        elif ext in (".docx", ".doc"):
            elements = partition_docx(file_path)
        elif ext in (".html", ".htm"):
            elements = partition_html(file_path, strategy="html")
        else:
            elements = partition(filename=file_path)
        return [_element_to_document(el) for el in elements]

    # ── PPTX qua pptx-tools MCP ──────────────────────────────────────────

    def _load_pptx(self, file_path: str) -> list[Document]:
        """Ưu tiên pptx-tools MCP (metadata chính xác nhất: shapes/tables).

        MCP không liên lạc được → fallback python-pptx local (ít metadata hơn,
        ghi `loader: python-pptx-fallback` vào metadata để truy vết).
        """
        try:
            return self._load_pptx_mcp(file_path)
        except (requests.ConnectionError, RuntimeError, ValueError) as exc:
            logger.warning("pptx-tools MCP thất bại (%s) — fallback python-pptx", exc)
            return self._load_pptx_local(file_path)

    def _load_pptx_mcp(self, file_path: str) -> list[Document]:
        client = McpClient(self.pptx_mcp_url)
        client.initialize()
        abs_path = os.path.abspath(file_path)

        slides_info = client.call_tool("pptx_list_slides", {"filePath": abs_path})
        count = self._slide_count(slides_info)
        if count <= 0:
            raise ValueError(f"Không xác định được số slide từ pptx_list_slides: {slides_info}")

        docs = []
        for i in range(count):  # pptx-tools dùng slideIndex 0-based
            content = client.call_tool(
                "pptx_get_slide_content", {"filePath": abs_path, "slideIndex": i}
            )
            docs.append(Document(
                text=self._slide_text(content),
                metadata={
                    "source": os.path.basename(file_path),
                    "slide_number": i + 1,  # pipeline dùng 1-based
                    "element_type": "PptxSlide",
                    "loader": "pptx-tools-mcp",
                    "raw": content,
                },
            ))
        return docs

    @staticmethod
    def _slide_count(info) -> int:
        # pptx_list_slides trả về JSON array [{Index, Title, ...}, ...] (0-based Index)
        if isinstance(info, list):
            return len(info)
        if isinstance(info, dict):
            if isinstance(info.get("slides"), list):
                return len(info["slides"])
            if isinstance(info.get("count"), int):
                return info["count"]
            raw = str(info.get("raw", info))
        else:
            raw = str(info)
        m = re.search(r"(\d+)\s*slides?", raw, flags=re.IGNORECASE)
        return int(m.group(1)) if m else 0

    @staticmethod
    def _slide_text(content) -> str:
        """Gom text từ mọi field lặp lại trong structured content (title/text/table cells)."""
        if isinstance(content, dict) and isinstance(content.get("text"), str) and content["text"].strip():
            return content["text"]

        parts: list[str] = []

        def walk(node) -> None:
            if isinstance(node, dict):
                # pptx-tools dùng khoá viết hoa: Text, Paragraphs, Title, Notes...
                for key in ("text", "Text", "title", "Title", "content", "value", "Paragraphs", "Notes"):
                    value = node.get(key)
                    if isinstance(value, str) and value.strip():
                        parts.append(value.strip())
                    elif isinstance(value, list):
                        for item in value:
                            if isinstance(item, str) and item.strip():
                                parts.append(item.strip())
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(content)
        seen, ordered = set(), []
        for part in parts:
            if part not in seen:
                seen.add(part)
                ordered.append(part)
        return "\n".join(ordered)

    # ── Fallback python-pptx ─────────────────────────────────────────────

    @staticmethod
    def _load_pptx_local(file_path: str) -> list[Document]:
        from pptx import Presentation

        prs = Presentation(file_path)
        docs = []
        for i, slide in enumerate(prs.slides, start=1):
            texts = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    t = shape.text_frame.text.strip()
                    if t:
                        texts.append(t)
                if getattr(shape, "has_table", False) and shape.has_table:
                    for row in shape.table.rows:
                        texts.append(" | ".join(c.text.strip() for c in row.cells))
            docs.append(Document(
                text="\n".join(texts),
                metadata={
                    "source": os.path.basename(file_path),
                    "slide_number": i,
                    "element_type": "PptxSlide",
                    "loader": "python-pptx-fallback",
                },
            ))
        return docs
