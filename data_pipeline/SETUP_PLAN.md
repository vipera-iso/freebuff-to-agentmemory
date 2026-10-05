# SETUP_PLAN — Kế hoạch cài đặt pipeline xử lý dữ liệu thô

> **Mục đích:** Hướng dẫn từng bước cài đặt và tích hợp các repo thành một pipeline hoàn chỉnh.
> **Thực thi tuần tự từ Giai đoạn 1 → 6. Mỗi giai đoạn có lệnh kiểm tra riêng.**

---

## Tổng quan kiến trúc

```
Nguồn dữ liệu thô (pptx, pdf, word, html, email, ảnh)
        │
        ▼
[1] Ingestion — Unstructured + pptx-tools MCP
        │
        ▼
[2] Parsing & Cleaning — Unstructured
        │
        ▼
[3] Chunking — LlamaIndex (Slide-aware)
        │
        ▼
[4] Embedding & Indexing — Bifrost Embeddings + Qdrant (embedded mode)
        │
        ▼
[5] Retrieval & Agentic RAG — LangGraph + Qdrant
        │
        ▼
[6] Output & Action — html-to-pptx + frontend-slides
```

---

## Giai đoạn 1: Khởi tạo môi trường & Clone repos

### 1.1. Tạo cấu trúc dự án

```bash
mkdir -p data_pipeline/{repos,ingestion,parsing,chunking,embedding,retrieval,output,agent,config,tests,logs,scripts}
cd data_pipeline
```

### 1.2. Clone các repo

```bash
# Repo 1: pptx-tools (MCP server xử lý PPTX)
git clone https://github.com/jongalloway/pptx-tools.git repos/pptx-tools

# Repo 2: html-to-pptx (HTML → PPTX editable)
git clone https://github.com/Design-Arena/html-to-pptx.git repos/html-to-pptx

# Repo 3: frontend-slides (PPTX → HTML/WebDeck)
git clone https://github.com/zarazhangrui/frontend-slides.git repos/frontend-slides

# Không clone RAGFlow — đã chốt dùng Qdrant + LlamaIndex (máy 14 GB RAM < 16 GB của RAGFlow)
```

### 1.3. Cài đặt Python dependencies

Tạo `requirements.txt`:

```
unstructured[all-docs]>=0.16.0
llama-index>=0.12.0
llama-index-embeddings-openai>=0.3.0
llama-index-vector-stores-qdrant>=0.4.0
langgraph>=0.2.0
langchain-openai>=0.3.0
python-pptx>=1.0.0
qdrant-client>=1.12.0
requests>=2.32.0
openai>=1.50.0
codex-relay>=0.2.0
```

```bash
pip install -r requirements.txt
```

### 1.4. Kiểm tra Giai đoạn 1

```bash
# Kiểm tra cấu trúc
ls -R data_pipeline/

# Kiểm tra Python imports
python -c "import unstructured, llama_index, langgraph, pptx, qdrant_client; print('OK')"

# Kiểm tra codex-relay
codex-relay --help
```

---

## Giai đoạn 2: Cấu hình Bifrost Gateway & Codex Relay

> **Quyết định kiến trúc (routing song song) — CẬP NHẬT 10/2026:** Stack
> `Codex CLI → ns-shim :8081 → Bifrost :8080` **đã tháo gỡ**: Codex CLI không còn
> trên máy, `tools/codex-ns-shim/` + `tools/bifrost-stack.sh` đã xoá khỏi repo và
> unit `codex-ns-shim.service` / `bifrost.service` không còn. Vì vậy `codex-relay`
> trên **port 4444** giờ là đường routing duy nhất cho agent của pipeline —
> các mục bên dưới mô tả cách cấu hình riêng trong `config/codex-home/`
> (`CODEX_HOME` cô lập), không đụng config user.

### 2.1. Cài đặt codex-relay

Codex CLI sử dụng OpenAI Responses API (độc quyền), trong khi Bifrost expose Chat Completions API. `codex-relay` là Rust proxy dịch giữa hai API.

```bash
pip install codex-relay
```

### 2.2. Khởi động relay trỏ về Bifrost

```bash
CODEX_RELAY_UPSTREAM=http://localhost:8080/openai/v1 \
CODEX_RELAY_API_KEY=dummy-key \
CODEX_RELAY_PORT=4444 \
codex-relay
```

Relay mặc định listen trên port **4444**.

### 2.3. Generate Codex config

```bash
codex-relay --print-config \
  --upstream http://localhost:8080/openai/v1 \
  --api-key dummy-key \
  --model-catalog ~/.codex/codex-relay-models.json
```

Lệnh này in ra snippet config sẵn dùng và ghi model catalog để tránh cảnh báo "Model metadata not found".

⚠️ **KHÔNG paste snippet vào `~/.codex/config.toml`** — file đó đang điều hướng Codex qua ns-shim (`model_provider = "bifrost"`). Snippet relay chỉ dùng trong môi trường cô lập (`CODEX_HOME=config/codex-home`) khi chạy agent của pipeline; lý do: `model_provider`/`model_providers` chỉ có hiệu lực ở user-level (xem `AGENTS.md` mục 4).

### 2.4. Cấu hình Bifrost MCP cho pptx-tools

Thêm vào `config/bifrost_config.json`:

```json
{
  "name": "pptx-tools",
  "connection_type": "http",
  "connection_string": "http://host.docker.internal:3001/mcp",
  "auth_type": "none",
  "needs_session_stickiness": true,
  "tools_to_execute": ["*"]
}
```

**Build pptx-tools** (cần .NET 10 SDK — xem `PREREQUISITES.md` mục 4):

```bash
cd repos/pptx-tools
dotnet build PptxTools.slnx --configuration Release
cd ../..
```

**Chạy bridge stdio → Streamable HTTP (:3001/mcp):**

Upstream pptx-tools **chỉ hỗ trợ stdio transport** (`WithStdioServerTransport()`, cờ `--stdio`) — nó không tự mở cổng HTTP như tài liệu cũ giả định. `scripts/pptx_mcp_bridge.sh` (supergateway) bọc stdio thành Streamable HTTP để Bifrost/pipeline kết nối:

```bash
./scripts/pptx_mcp_bridge.sh &          # hoặc: systemctl --user start pptx-mcp-bridge
curl -s http://localhost:3001/health     # readiness check
```

> **Param tool là camelCase:** `filePath`, `slideIndex` (0-based). `pptx_list_slides`
> trả về JSON array `[{Index, Title, LayoutName, ...}]`; `pptx_get_slide_content`
> trả về `{SlideIndex, Shapes:[{Name, ShapeType, Text, Paragraphs, ...}]}`.

### 2.5. Kiểm tra Giai đoạn 2

```bash
# Kiểm tra relay
curl -X POST http://127.0.0.1:4444/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "test"}]}'

# Kiểm tra Codex agent của pipeline qua relay (env cô lập — không đụng config user)
CODEX_HOME=$PWD/config/codex-home codex exec "hello"
```

---

## Giai đoạn 3: Module xử lý dữ liệu

> **⚠️ CODE ĐÃ CÓ SẴN TRONG REPO** (`ingestion/loaders.py`, `parsing/cleaner.py`, `chunking/semantic_chunker.py`, `embedding/qdrant_store.py`, `retrieval/retriever.py`, `agent/rag_agent.py`).
> **Code trong repo là bản canonical** — đã tinh chỉnh so với code minh họa dưới đây:
> loaders trả `Document` (không phải raw elements), MCP client JSON-RPC đầy đủ với fallback python-pptx, Qdrant dùng singleton client chống storage lock, agent dùng tool-binding + ToolMessage đúng chuẩn LangGraph.
> Đoạn code bên dưới chỉ mang tính tham khảo kiến trúc. Đừng ghi đè file trong repo bằng đoạn này.

### 3.1. Tạo `ingestion/loaders.py`

```python
"""
Module ingestion: Load dữ liệu từ nhiều định dạng.
Sử dụng Unstructured cho PDF/Word/HTML/Email.
Sử dụng pptx-tools MCP cho PPTX.
"""
import os
import requests
from unstructured.partition.auto import partition
from unstructured.partition.pdf import partition_pdf
from unstructured.partition.docx import partition_docx
from unstructured.partition.html import partition_html

# Bật huge_tree cho HTML lồng sâu (depth > 256)
os.environ["UNSTRUCTURED_HTML_HUGE_TREE"] = "1"


class DocumentLoader:
    def __init__(self, pptx_mcp_url="http://localhost:3001/mcp"):
        self.pptx_mcp_url = pptx_mcp_url

    def load(self, file_path: str) -> list:
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".pptx":
            return self._load_pptx_via_mcp(file_path)
        elif ext == ".pdf":
            return partition_pdf(file_path, strategy="hi_res")
        elif ext in [".docx", ".doc"]:
            return partition_docx(file_path)
        elif ext in [".html", ".htm"]:
            return partition_html(file_path, strategy="html")
        else:
            return partition(filename=file_path)

    def _load_pptx_via_mcp(self, file_path: str) -> list:
        """Gọi pptx-tools MCP server để đọc slide."""
        response = requests.post(
            f"{self.pptx_mcp_url}/tools/pptx_get_slide_content",
            json={"file_path": file_path}
        )
        return response.json().get("elements", [])
```

### 3.2. Tạo `parsing/cleaner.py`

```python
"""Module parsing: Làm sạch và chuẩn hóa dữ liệu."""


class DocumentCleaner:
    def clean(self, elements: list) -> list:
        cleaned = []
        for el in elements:
            if not hasattr(el, "text") or not el.text:
                continue
            text = el.text.strip()
            if len(text) < 2:
                continue

            metadata = {
                "source": getattr(el.metadata, "filename", "unknown"),
                "page_number": getattr(el.metadata, "page_number", None),
                "element_type": type(el).__name__,
                "slide_number": getattr(el.metadata, "slide_number", None),
            }
            cleaned.append({"text": text, "metadata": metadata})
        return cleaned
```

### 3.3. Tạo `chunking/semantic_chunker.py` với slide-aware logic

```python
"""
Module chunking: Slide-aware chunking.
Giữ slide boundary, không cắt giữa slide.
"""
from llama_index.core.node_parser import NodeParser
from llama_index.core.schema import TextNode
from llama_index.embeddings.openai import OpenAIEmbedding


class SlideAwareChunker(NodeParser):
    def __init__(self, max_chars_per_slide=1500, chunk_size=512):
        self.max_chars_per_slide = max_chars_per_slide
        self.chunk_size = chunk_size
        self.embed_model = OpenAIEmbedding(
            api_base="http://localhost:8080/openai/v1",
            api_key="dummy-key",
            model="text-embedding-3-small"
        )

    def _parse_nodes(self, nodes, show_progress=False, **kwargs):
        result = []
        for node in nodes:
            slide_num = node.metadata.get("slide_number")
            if slide_num is None:
                result.append(node)
                continue

            if len(node.text) <= self.max_chars_per_slide:
                result.append(node)
            else:
                for i in range(0, len(node.text), self.chunk_size):
                    chunk_text = node.text[i:i + self.chunk_size]
                    result.append(TextNode(
                        text=chunk_text,
                        metadata={
                            **node.metadata,
                            "chunk_in_slide": i // self.chunk_size
                        }
                    ))
        return result
```

### 3.4. Kiểm tra Giai đoạn 3

```bash
python -c "
from ingestion.loaders import DocumentLoader
loader = DocumentLoader()
docs = loader.load('tests/sample.pdf')
print(f'Loaded {len(docs)} elements')
"
```

---

## Giai đoạn 4: Qdrant + Embedding & Indexing

> **Quyết định kiến trúc:** Máy chỉ có 14 GB RAM (RAGFlow cần ≥ 16 GB), nên pipeline dùng
> **Qdrant + LlamaIndex** làm retrieval engine thay vì RAGFlow self-hosted. Qdrant chạy
> **embedded mode** (không cần Docker) qua `qdrant-client`, nhẹ và đủ cho pipeline này.

### 4.1. Tạo `embedding/qdrant_store.py`

```python
"""
Module embedding: Vector hóa chunks và index vào Qdrant (embedded mode).
Embeddings đi qua Bifrost gateway (http://localhost:8080/openai/v1).
"""
from llama_index.core import VectorStoreIndex, StorageContext, Document
from llama_index.core.settings import Settings
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

QDRANT_PATH = "data/qdrant"          # thư mục storage embedded mode
COLLECTION = "data_pipeline"


def build_index(documents: list[Document]) -> VectorStoreIndex:
    """Embed + index list[Document] vào Qdrant. documents là output của chunker."""
    Settings.embed_model = OpenAIEmbedding(
        api_base="http://localhost:8080/openai/v1",
        api_key="dummy-key",
        model="text-embedding-3-small",
    )
    client = QdrantClient(path=QDRANT_PATH)           # embedded — không cần Docker
    vector_store = QdrantVectorStore(client=client, collection_name=COLLECTION)
    storage = StorageContext.from_defaults(vector_store=vector_store)
    return VectorStoreIndex.from_documents(documents, storage_context=storage)
```

### 4.2. Tạo `retrieval/retriever.py`

```python
"""Module retrieval: Truy xuất top-k chunks từ Qdrant cho Agentic RAG."""
from llama_index.core import VectorStoreIndex
from llama_index.core.settings import Settings
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

QDRANT_PATH = "data/qdrant"
COLLECTION = "data_pipeline"


def get_retriever(top_k: int = 5):
    Settings.embed_model = OpenAIEmbedding(
        api_base="http://localhost:8080/openai/v1",
        api_key="dummy-key",
        model="text-embedding-3-small",
    )
    client = QdrantClient(path=QDRANT_PATH)
    index = VectorStoreIndex.from_vector_store(
        QdrantVectorStore(client=client, collection_name=COLLECTION)
    )
    return index.as_retriever(similarity_top_k=top_k)


def retrieve(query: str, top_k: int = 5) -> str:
    nodes = get_retriever(top_k).retrieve(query)
    return "\n\n".join(n.get_content() for n in nodes)
```

### 4.3. Kiểm tra Giai đoạn 4

```bash
# Khởi động Qdrant embedded lần đầu (tự tạo storage)
python -c "
from embedding.qdrant_store import build_index
from llama_index.core import Document
idx = build_index([Document(text='smoke test: Qdrant embedded mode hoạt động')])
print('Index OK')
"
ls data/qdrant/
```

---

## Giai đoạn 5: Agentic RAG với LangGraph

### 5.1. Tạo `agent/rag_agent.py`

```python
"""Agentic RAG sử dụng LangGraph + Qdrant."""
from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
import operator


class AgentState(TypedDict):
    messages: Annotated[list, operator.add]
    context: str


llm = ChatOpenAI(
    base_url="http://127.0.0.1:4444/v1",
    api_key="dummy-key",
    model="gpt-4o-mini",
    temperature=0
)


def call_model(state: AgentState):
    response = llm.invoke(state["messages"])
    return {"messages": [response]}


def should_retrieve(state: AgentState) -> str:
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "retrieve"
    return END


def retrieve_from_qdrant(state: AgentState):
    query = state["messages"][-1].content
    context = retrieve(query, top_k=5)   # retrieval/retriever.py — Giai đoạn 4
    return {"context": context, "messages": state["messages"]}


workflow = StateGraph(AgentState)
workflow.add_node("agent", call_model)
workflow.add_node("retrieve", retrieve_from_qdrant)
workflow.set_entry_point("agent")
workflow.add_conditional_edges("agent", should_retrieve, {"retrieve": "retrieve", END: END})
workflow.add_edge("retrieve", "agent")
agent = workflow.compile()
```

### 5.2. Kiểm tra Giai đoạn 5

```bash
python -c "
from agent.rag_agent import agent
result = agent.invoke({'messages': [{'role': 'user', 'content': 'Test'}], 'context': ''})
print(result['messages'][-1].content[:200])
"
```

---

## Giai đoạn 6: Output Pipeline

### 6.1. Cài đặt html-to-pptx

```bash
pip install html-to-pptx
python -m playwright install chromium
```

html-to-pptx sử dụng **browser-based DOM measurement** để chuyển HTML slide thành PPTX editable.

### 6.2. Cài đặt frontend-slides

```bash
mkdir -p ~/.codex/skills/frontend-slides/scripts
cp repos/frontend-slides/SKILL.md repos/frontend-slides/STYLE_PRESETS.md \
   repos/frontend-slides/viewport-base.css repos/frontend-slides/html-template.md \
   repos/frontend-slides/animation-patterns.md ~/.codex/skills/frontend-slides/
cp repos/frontend-slides/scripts/extract-pptx.py ~/.codex/skills/frontend-slides/scripts/
```

### 6.3. Kiểm tra Giai đoạn 6

```bash
# Test HTML → PPTX
html-to-pptx tests/sample.html tests/sample_output.pptx

# Kiểm tra file PPTX
python -c "
from pptx import Presentation
prs = Presentation('tests/sample_output.pptx')
print(f'Slides: {len(prs.slides)}')
"
```

---

## Bảng tóm tắt

| Giai đoạn | Repo chính | Vai trò | Kiểm tra |
|---|---|---|---|
| 1 | pptx-tools, html-to-pptx, frontend-slides | Clone + cài đặt | `ls repos/`, `python -c import` |
| 2 | codex-relay, Bifrost | Gateway (song song với ns-shim :8081) | `curl` relay, `codex exec` |
| 3 | Unstructured, LlamaIndex | Xử lý dữ liệu | Load + chunk test |
| 4 | Qdrant + LlamaIndex | Embedding & indexing (embedded mode) | `ls data/qdrant/` |
| 5 | LangGraph | Agent orchestration | Agent invoke test |
| 6 | html-to-pptx, frontend-slides | Output | Convert test |
