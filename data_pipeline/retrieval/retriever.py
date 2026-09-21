"""
Module retrieval: Truy xuất top-k chunks từ Qdrant (embedded mode) cho Agentic RAG.

Dùng chung singleton client với embedding.qdrant_store — chỉ 1 process được mở
storage cùng lúc (Qdrant embedded giữ file lock).
"""
from llama_index.core import VectorStoreIndex
from llama_index.core.settings import Settings
from llama_index.vector_stores.qdrant import QdrantVectorStore

from embedding.qdrant_store import COLLECTION, get_client, get_embed_model


def get_retriever(top_k: int = 5):
    Settings.embed_model = get_embed_model()
    index = VectorStoreIndex.from_vector_store(
        QdrantVectorStore(client=get_client(), collection_name=COLLECTION)
    )
    return index.as_retriever(similarity_top_k=top_k)


def retrieve(query: str, top_k: int = 5) -> str:
    nodes = get_retriever(top_k).retrieve(query)
    return "\n\n".join(n.get_content() for n in nodes)
