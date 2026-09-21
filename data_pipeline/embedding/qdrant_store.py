"""
Module embedding: Vector hóa chunks và index vào Qdrant (embedded mode).

Embeddings đi qua Bifrost gateway (http://localhost:8080/openai/v1).
Qdrant embedded mode giữ file lock trên storage — dùng get_client() (singleton
dùng chung trong process) để tránh lỗi "Storage folder is already accessed".
"""
from llama_index.core import Document, StorageContext, VectorStoreIndex
from llama_index.core.settings import Settings
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

QDRANT_PATH = "data/qdrant"          # thư mục storage embedded mode
COLLECTION = "data_pipeline"

_client: QdrantClient | None = None


def get_client(path: str = QDRANT_PATH) -> QdrantClient:
    """Singleton client trong process — tránh 2 instance giành lock cùng storage."""
    global _client
    if _client is None:
        _client = QdrantClient(path=path)
    return _client


def get_embed_model() -> OpenAIEmbedding:
    """Embedding model qua Bifrost gateway."""
    return OpenAIEmbedding(
        api_base="http://localhost:8080/openai/v1",
        api_key="dummy-key",
        model="text-embedding-3-small",
    )


def build_index(documents: list[Document]) -> VectorStoreIndex:
    """Embed + index list[Document] vào Qdrant. documents là output của chunker."""
    Settings.embed_model = get_embed_model()
    vector_store = QdrantVectorStore(client=get_client(), collection_name=COLLECTION)
    storage = StorageContext.from_defaults(vector_store=vector_store)
    return VectorStoreIndex.from_documents(documents, storage_context=storage)
