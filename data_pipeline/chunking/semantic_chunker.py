"""
Module chunking: Slide-aware chunking.

Giữ slide boundary: slide ngắn giữ nguyên 1 node, slide dài mới cắt.
Đơn vị chia theo ký tự (chunk_size) — đơn giản, deterministic, dễ test.
"""
from typing import Sequence

from llama_index.core.node_parser import NodeParser
from llama_index.core.schema import BaseNode, Document, TextNode


class SlideAwareChunker(NodeParser):
    def __init__(self, max_chars_per_slide=1500, chunk_size=512, **kwargs):
        super().__init__(**kwargs)
        self.max_chars_per_slide = max_chars_per_slide
        self.chunk_size = chunk_size

    def _parse_nodes(
        self, nodes: Sequence[BaseNode], show_progress: bool = False, **kwargs
    ) -> list[BaseNode]:
        result: list[BaseNode] = []
        for node in nodes:
            slide_num = node.metadata.get("slide_number")
            if slide_num is None:
                result.append(node)  # nội dung phi-slide: giữ nguyên
                continue

            if len(node.text) <= self.max_chars_per_slide:
                result.append(node)  # slide ngắn: giữ nguyên — không cắt giữa slide
                continue

            for i in range(0, len(node.text), self.chunk_size):
                result.append(TextNode(
                    text=node.text[i:i + self.chunk_size],
                    metadata={
                        **node.metadata,
                        "chunk_in_slide": i // self.chunk_size,
                    },
                ))
        return result

    def chunk(self, documents: Sequence[Document]) -> list[BaseNode]:
        """API tiện cho pipeline/tests: list[Document] → list[BaseNode]."""
        return self.get_nodes_from_documents(documents)
