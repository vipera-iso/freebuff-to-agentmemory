"""
Module parsing: Làm sạch và chuẩn hóa dữ liệu.

Input: list[Document] (từ ingestion.loaders) hoặc list[Unstructured Elements].
Output: list[Document] đã làm sạch — đầu vào cho chunking.
"""
from llama_index.core.schema import Document


class DocumentCleaner:
    def clean(self, elements: list) -> list[Document]:
        """Lọc element rỗng/quá ngắn, strip text, chuẩn hóa metadata.

        Chấp nhận cả Document (từ loaders) và raw Unstructured elements
        (khi gọi partition_* trực tiếp).
        """
        cleaned: list[Document] = []
        for el in elements:
            doc = self._to_document(el)
            if doc is None:
                continue
            text = doc.text.strip()
            if len(text) < 2:
                continue
            cleaned.append(Document(text=text, metadata=doc.metadata))
        return cleaned

    @staticmethod
    def _to_document(el) -> Document | None:
        # Đã là Document (chuẩn từ ingestion.loaders)
        if isinstance(el, Document):
            return el
        # Unstructured Element
        if hasattr(el, "text"):
            meta = getattr(el, "metadata", None)
            return Document(
                text=el.text or "",
                metadata={
                    "source": getattr(meta, "filename", "unknown"),
                    "page_number": getattr(meta, "page_number", None),
                    "slide_number": getattr(meta, "slide_number", None),
                    "element_type": type(el).__name__,
                },
            )
        return None
