"""
Agentic RAG sử dụng LangGraph + Qdrant.

Flow: agent (LLM có tool `search_documents`) → nếu LLM gọi tool → `retrieve`
chạy truy vấn Qdrant và trả kết quả về LLM bằng ToolMessage → agent trả lời.
"""
import json
import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from retrieval.retriever import retrieve

RELAY_BASE_URL = "http://127.0.0.1:4444/v1"


class AgentState(TypedDict):
    messages: Annotated[list, operator.add]
    context: str


llm = ChatOpenAI(
    base_url=RELAY_BASE_URL,
    api_key="dummy-key",
    model="gpt-4o-mini",
    temperature=0,
).bind_tools([
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": "Tìm kiếm trong tài liệu đã index (PPTX, PDF, HTML...) bằng Qdrant.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Câu truy vấn tìm kiếm."},
                },
                "required": ["query"],
            },
        },
    },
])


def call_model(state: AgentState):
    response = llm.invoke(state["messages"])
    return {"messages": [response]}


def should_retrieve(state: AgentState) -> str:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "retrieve"
    return END


def retrieve_from_qdrant(state: AgentState):
    """Chạy tool `search_documents` và trả kết quả về LLM bằng ToolMessage."""
    last: BaseMessage = state["messages"][-1]
    new_messages = []
    for tool_call in last.tool_calls:
        args = tool_call.get("args", {})
        try:
            context = retrieve(args.get("query", ""), top_k=5)
            payload = json.dumps({"context": context}, ensure_ascii=False)
        except Exception as exc:  # retriever lỗi → báo lỗi cho LLM thay vì crash
            payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
        new_messages.append(ToolMessage(
            content=payload,
            tool_call_id=tool_call["id"],
        ))
    return {"messages": new_messages}


workflow = StateGraph(AgentState)
workflow.add_node("agent", call_model)
workflow.add_node("retrieve", retrieve_from_qdrant)
workflow.set_entry_point("agent")
workflow.add_conditional_edges("agent", should_retrieve, {"retrieve": "retrieve", END: END})
workflow.add_edge("retrieve", "agent")
agent = workflow.compile()
