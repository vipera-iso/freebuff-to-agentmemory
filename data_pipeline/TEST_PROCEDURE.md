# TEST_PROCEDURE.md — Quy trình thực thi test pipeline

> **Mục đích:** Kiểm tra từng thành phần của pipeline từ giai đoạn cài đặt đến end-to-end, đảm bảo tất cả hoạt động đúng trước khi vận hành.
>
> **Thực thi tuần tự từ Test 1 → Test 11. Mỗi test có lệnh kiểm tra và tiêu chí PASS/FAIL rõ ràng.**

> **Đã adapt theo quyết định kiến trúc chốt (xem `PREREQUISITES.md` mục 0):**
> - **Test 8 dùng Qdrant embedded + LlamaIndex** thay RAGFlow (máy 14 GB RAM).
> - **Test 3 chạy codex-relay trong `CODEX_HOME` cô lập**, không đụng routing ns-shim :8081 hiện có.
> - RAGFlow không còn trong pipeline — không test.

---

## 0. Chuẩn bị môi trường test

```bash
mkdir -p tests/fixtures tests/outputs logs data/qdrant
cd data_pipeline

# Venv Python 3.12 (máy có 3.14 — ngoài vùng hỗ trợ của Unstructured)
source .venv/bin/activate   # tạo lần đầu: python3.12 -m venv .venv && pip install -r requirements.txt

# Set biến môi trường
export OPENAI_BASE_URL=http://localhost:8080/openai/v1
export OPENAI_API_KEY=dummy-key
export BIFROST_VIRTUAL_KEY=<your-virtual-key>
export UNSTRUCTURED_HTML_HUGE_TREE=1
```

**Dữ liệu mẫu:** tạo tự động bằng `python scripts/create_fixtures.py` (không commit fixtures — file nhị phân). Kết quả:

| File | Định dạng | Mục đích |
|---|---|---|
| `tests/fixtures/sample.pptx` | PPTX (5 slides, có text + bảng) | Test ingestion PPTX + pptx-tools |
| `tests/fixtures/sample.pdf` | PDF (≥ 3 trang, có bảng) | Test ingestion PDF |
| `tests/fixtures/sample.html` | HTML lồng sâu (depth > 10) | Test Unstructured HTML parsing |
| `tests/fixtures/slides.html` | HTML slide deck | Test html-to-pptx |

---

## TEST 1: Kiểm tra tiên quyết hệ thống

**Mục đích:** Xác minh máy đáp ứng đủ tài nguyên và phần mềm.

```bash
./scripts/check_prerequisites.sh
```

**Tiêu chí PASS:**
- `🎉 TẤT CẢ TIÊN QUYẾT ĐẠT — Sẵn sàng cài đặt.`
- Exit code = 0

**Nếu FAIL:** Sửa các mục ❌ rồi chạy lại. Docker/Chrome là cảnh báo có thể bỏ qua (xem `PREREQUISITES.md` mục 0).

---

## TEST 2: Kiểm tra Bifrost Gateway

**Mục đích:** Xác minh Bifrost gateway hoạt động và expose đúng endpoint.

### 2.1. Health check

```bash
curl -s http://localhost:8080/health | jq .
```

**Tiêu chí PASS:** Response có `"status": "healthy"` hoặc HTTP 200.

### 2.2. Kiểm tra OpenAI-compatible endpoint

```bash
curl -X POST http://localhost:8080/openai/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $BIFROST_VIRTUAL_KEY" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Say OK"}]
  }' | jq .
```

**Tiêu chí PASS:** Response có `choices[0].message.content` không rỗng.

### 2.3. Kiểm tra provider fallback

```bash
curl -X POST http://localhost:8080/openai/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $BIFROST_VIRTUAL_KEY" \
  -d '{
    "model": "openai/gpt-4o-mini",
    "messages": [{"role": "user", "content": "Test fallback"}],
    "fallbacks": [{"model": "anthropic/claude-3-haiku-20240307"}]
  }' | jq .
```

**Tiêu chí PASS:** Response trả về thành công (từ primary hoặc fallback model).

> **Lưu ý máy này:** stack thực tế là `Codex → ns-shim :8081 → Bifrost :8080`. Test 2 chỉ đụng trực tiếp Bifrost :8080 — không cần shim. Nếu Bifrost đang cấu hình các model Agnes thay vì `gpt-4o-mini`, thay model name cho khớp catalog thực tế.

---

## TEST 3: Kiểm tra Codex Relay (env cô lập)

**Mục đích:** Xác minh relay dịch Responses API ↔ Chat Completions API hoạt động, cho agent của pipeline.

### 3.1. Khởi động relay (nếu chưa chạy) và kiểm tra

```bash
CODEX_RELAY_UPSTREAM=http://localhost:8080/openai/v1 \
CODEX_RELAY_API_KEY=dummy-key \
CODEX_RELAY_PORT=4444 \
codex-relay &

curl -s http://127.0.0.1:4444/v1/models | jq .
```

**Tiêu chí PASS:** Response trả về danh sách models (không lỗi connection).

### 3.2. Kiểm tra Codex CLI kết nối qua relay — **bắt buộc dùng `CODEX_HOME` cô lập**

```bash
CODEX_HOME=$PWD/config/codex-home codex exec "Say OK" 2>&1 | head -20
```

**Tiêu chí PASS:** Codex CLI trả về response thành công, không có lỗi `wire_api` hoặc `Model metadata not found`.

**Nếu FAIL:** Chạy `codex-relay --print-config` để generate config vào `config/codex-home/` với model catalog đầy đủ.

> ⚠️ **KHÔNG chạy `codex exec` không có `CODEX_HOME`** — lệnh đó đi qua ns-shim :8081 (routing của Codex CLI hằng ngày), không phải relay, và kết quả sẽ không phản ánh đúng relay. Chi tiết: `AGENTS.md` mục 4.1.

---

## TEST 4: Kiểm tra pptx-tools MCP

**Mục đích:** Xác minh MCP server đọc và cập nhật PPTX qua Bifrost.

### 4.1. Kiểm tra pptx-tools server hoạt động

```bash
cd repos/pptx-tools
dotnet run --project src/PptxTools --urls http://0.0.0.0:3001 &
sleep 5
curl -s http://localhost:3001/mcp/health || echo "Server responding"
cd ../..
```

**Tiêu chí PASS:** Server khởi động không lỗi build.

### 4.2. Gọi tool `pptx_list_slides` qua Bifrost MCP

```bash
curl -X POST http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "pptx_list_slides",
      "arguments": {"file_path": "tests/fixtures/sample.pptx"}
    },
    "id": 1
  }' | jq .
```

**Tiêu chí PASS:** Response trả về danh sách slides với metadata.

### 4.3. Gọi tool `pptx_get_slide_content`

```bash
curl -X POST http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "pptx_get_slide_content",
      "arguments": {"file_path": "tests/fixtures/sample.pptx", "slide_number": 1}
    },
    "id": 2
  }' | jq '.result.content'
```

**Tiêu chí PASS:** Response trả về structured content (shapes, text, tables).

---

## TEST 5: Kiểm tra Unstructured Parsing

**Mục đích:** Xác minh Unstructured parse đúng các định dạng.

### 5.1. Test PDF parsing

```bash
python -c "
from unstructured.partition.pdf import partition_pdf
elements = partition_pdf('tests/fixtures/sample.pdf', strategy='hi_res')
print(f'PDF elements: {len(elements)}')
for el in elements[:3]:
    print(f'  - {type(el).__name__}: {el.text[:50]}...')
assert len(elements) > 0, 'PDF parsing failed'
"
```

**Tiêu chí PASS:** `PDF elements > 0`, không lỗi OCR.

### 5.2. Test HTML parsing với huge_tree

```bash
UNSTRUCTURED_HTML_HUGE_TREE=1 python -c "
from unstructured.partition.html import partition_html
elements = partition_html('tests/fixtures/sample.html', strategy='html')
print(f'HTML elements: {len(elements)}')
assert len(elements) > 0, 'HTML parsing failed'
"
```

**Tiêu chí PASS:** `HTML elements > 0`. Env var `UNSTRUCTURED_HTML_HUGE_TREE=1` cho phép parse HTML có DOM depth > 256.

### 5.3. Test PPTX parsing qua MCP

```bash
python -c "
from ingestion.loaders import DocumentLoader
loader = DocumentLoader()
docs = loader.load('tests/fixtures/sample.pptx')
print(f'PPTX elements: {len(docs)}')
assert len(docs) > 0, 'PPTX loading failed'
"
```

**Tiêu chí PASS:** `PPTX elements > 0`. Yêu cầu pptx-tools đang chạy ở :3001 (Test 4).

---

## TEST 6: Kiểm tra LlamaIndex Chunking

**Mục đích:** Xác minh slide-aware chunking hoạt động.

### 6.1. Test SlideAwareChunker

```bash
python -c "
from llama_index.core.schema import Document
from chunking.semantic_chunker import SlideAwareChunker

chunker = SlideAwareChunker(max_chars_per_slide=1500, chunk_size=512)
docs = [
    Document(text='Short slide content', metadata={'slide_number': 1}),
    Document(text='Very long content ' * 200, metadata={'slide_number': 2}),
    Document(text='Non-slide content', metadata={})
]
nodes = chunker.chunk(docs)
print(f'Nodes created: {len(nodes)}')
for node in nodes:
    slide = node.metadata.get('slide_number', 'N/A')
    print(f'  - Slide {slide}: {len(node.text)} chars')
assert len(nodes) >= 3, 'Chunking failed'
"
```

**Tiêu chí PASS:**
- Slide 1 (ngắn) → giữ nguyên 1 node.
- Slide 2 (dài) → chia thành nhiều chunks với metadata `chunk_in_slide`.
- Non-slide → giữ nguyên.

### 6.2. Test metadata preservation

```bash
python -c "
from llama_index.core.schema import Document
from chunking.semantic_chunker import SlideAwareChunker

chunker = SlideAwareChunker()
docs = [Document(text='Test', metadata={'slide_number': 5, 'source': 'test.pptx'})]
nodes = chunker.chunk(docs)
assert nodes[0].metadata['slide_number'] == 5, 'slide_number lost'
assert nodes[0].metadata['source'] == 'test.pptx', 'source lost'
print('Metadata preservation: PASS')
"
```

**Tiêu chí PASS:** `slide_number` và `source` được giữ nguyên.

---

## TEST 7: Kiểm tra LangGraph Agent

**Mục đích:** Xác minh agent khởi tạo, gọi tool, và retrieve từ Qdrant.

### 7.1. Test agent graph compilation

```bash
python -c "
from agent.rag_agent import agent
print(f'Agent compiled: {agent is not None}')
print(f'Nodes: {list(agent.get_graph().nodes.keys())}')
assert agent is not None, 'Agent compilation failed'
"
```

**Tiêu chí PASS:** Agent compiled thành công, có nodes `agent` và `retrieve`.

### 7.2. Test agent invocation (không cần retrieve)

```bash
python -c "
from agent.rag_agent import agent
result = agent.invoke({
    'messages': [{'role': 'user', 'content': 'Say OK'}],
    'context': ''
})
print(f'Response: {result[\"messages\"][-1].content[:100]}')
assert result['messages'][-1].content, 'Empty response'
"
```

**Tiêu chí PASS:** Agent trả về response không rỗng. Yêu cầu codex-relay đang chạy ở :4444 (Test 3).

### 7.3. Unit test với fake LLM (không gọi API)

```bash
cat > tests/test_agent_unit.py << 'EOF'
import pytest
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

class State(TypedDict):
    messages: list
    context: str

def create_test_graph():
    return (StateGraph(State)
        .add_node('agent', lambda s: {'messages': s['messages'] + ['agent_response']})
        .add_node('retrieve', lambda s: {'context': 'retrieved'})
        .add_edge(START, 'agent')
        .add_edge('agent', END))

def test_graph_compilation():
    graph = create_test_graph()
    checkpointer = MemorySaver()
    compiled = graph.compile(checkpointer=checkpointer)
    assert compiled is not None

def test_agent_node():
    graph = create_test_graph()
    compiled = graph.compile()
    result = compiled.invoke({'messages': [], 'context': ''})
    assert 'agent_response' in result['messages']
EOF
python -m pytest tests/test_agent_unit.py -v
```

**Tiêu chí PASS:** Tất cả unit test pass. LangGraph khuyến nghị tạo graph trước mỗi test và compile với checkpointer mới để cô lập state.

---

## TEST 8: Kiểm tra Qdrant Retrieval (embedded mode)

> **Đã thay thế Test 8 gốc (RAGFlow).** Không cần service nào — Qdrant chạy embedded qua `qdrant-client`, index nằm tại `data/qdrant/`. Chỉ 1 process được mở storage cùng lúc.

### 8.1. Kiểm tra index đã tồn tại

```bash
ls data/qdrant/ || echo "CHƯA CÓ INDEX — chạy bước 8.2 trước"
```

**Tiêu chí PASS:** Thư mục `data/qdrant/` tồn tại (đã từng chạy Giai đoạn 4).

### 8.2. Index smoke test + retrieval

```bash
python -c "
from embedding.qdrant_store import build_index
from retrieval.retriever import retrieve
from llama_index.core import Document

# Index một document nhỏ (chỉ khi storage chưa bị lock bởi process khác)
build_index([Document(text='Qdrant embedded mode smoke test cho data pipeline.')])
print('Index: OK')

# Retrieve
context = retrieve('smoke test', top_k=3)
assert context.strip(), 'No context retrieved'
print(f'Retrieval: OK ({len(context)} chars)')
"
```

**Tiêu chí PASS:** Index tạo được storage tại `data/qdrant/` và retrieval trả về context không rỗng.

### 8.3. Test similarity ranking qua retriever

```bash
python -c "
from retrieval.retriever import get_retriever
retriever = get_retriever(top_k=3)
nodes = retriever.retrieve('Qdrant embedded mode')
assert len(nodes) > 0, 'No nodes returned'
scores = [n.score for n in nodes if n.score is not None]
print(f'Returned {len(nodes)} nodes, scores: {scores}')
assert all(nodes[i].score >= nodes[i+1].score for i in range(len(scores)-1)), 'Scores not sorted'
print('Ranking: PASS (scores giảm dần)')
"
```

**Tiêu chí PASS:** Retrieval trả về nodes với score giảm dần (similarity ranking hoạt động).

---

## TEST 9: Kiểm tra Output Pipeline

**Mục đích:** Xác minh html-to-pptx và frontend-slides hoạt động.

### 9.1. Test html-to-pptx conversion

```bash
html-to-pptx tests/fixtures/slides.html tests/outputs/sample_output.pptx
ls -la tests/outputs/sample_output.pptx
```

**Tiêu chí PASS:** File `.pptx` được tạo với size > 0. Yêu cầu Playwright Chromium (Giai đoạn 6).

### 9.2. Kiểm tra PPTX editable

```bash
python -c "
from pptx import Presentation
prs = Presentation('tests/outputs/sample_output.pptx')
print(f'Slides: {len(prs.slides)}')
for i, slide in enumerate(prs.slides):
    if i >= 3: break
    shapes = [s for s in slide.shapes if s.has_text_frame]
    print(f'  Slide {i+1}: {len(shapes)} text shapes')
    for shape in shapes[:2]:
        print(f'    - {shape.text[:40]}')
assert len(prs.slides) > 0, 'No slides generated'
"
```

**Tiêu chí PASS:**
- Slides > 0.
- Text shapes là **native PPTX shapes** (có thể chỉnh sửa), không phải ảnh. `html-to-pptx` đo từng DOM element trong headless browser và map thành PPTX shape native.

### 9.3. Test frontend-slides validation

```bash
# Kiểm tra skill đã cài đúng
ls ~/.codex/skills/frontend-slides/SKILL.md
ls ~/.codex/skills/frontend-slides/scripts/extract-pptx.py

# Test extract PPTX
python ~/.codex/skills/frontend-slides/scripts/extract-pptx.py tests/fixtures/sample.pptx tests/outputs/extracted/
ls tests/outputs/extracted/
```

**Tiêu chí PASS:** SKILL.md và extract-pptx.py tồn tại. Extract tạo ra ít nhất 1 file output.

---

## TEST 10: End-to-End Pipeline Test

**Mục đích:** Chạy toàn bộ pipeline từ đầu đến cuối với dữ liệu thực tế.

### 10.1. Tạo test script

```bash
cat > tests/test_pipeline_e2e.py << 'PYEOF'
"""End-to-end test cho toàn bộ pipeline."""
import time
import os
import pytest

class TestPipelineE2E:
    def test_ingestion_pptx(self):
        from ingestion.loaders import DocumentLoader
        loader = DocumentLoader()
        docs = loader.load("tests/fixtures/sample.pptx")
        assert len(docs) > 0
        print(f"[PASS] Ingestion PPTX: {len(docs)} elements")

    def test_ingestion_pdf(self):
        from unstructured.partition.pdf import partition_pdf
        elements = partition_pdf("tests/fixtures/sample.pdf", strategy="hi_res")
        assert len(elements) > 0
        print(f"[PASS] Ingestion PDF: {len(elements)} elements")

    def test_parsing_cleaning(self):
        from ingestion.loaders import DocumentLoader
        from parsing.cleaner import DocumentCleaner
        loader = DocumentLoader()
        cleaner = DocumentCleaner()
        docs = loader.load("tests/fixtures/sample.pdf")
        cleaned = cleaner.clean(docs)
        assert len(cleaned) > 0
        print(f"[PASS] Parsing: {len(cleaned)} cleaned elements")

    def test_chunking(self):
        from llama_index.core.schema import Document
        from chunking.semantic_chunker import SlideAwareChunker
        chunker = SlideAwareChunker()
        docs = [Document(text="Test content " * 100,
                         metadata={"slide_number": 1, "source": "test"})]
        nodes = chunker.chunk(docs)
        assert len(nodes) > 0
        print(f"[PASS] Chunking: {len(nodes)} nodes")

    def test_agent_response(self):
        from agent.rag_agent import agent
        result = agent.invoke({
            "messages": [{"role": "user", "content": "Test query"}],
            "context": ""
        })
        assert result["messages"][-1].content is not None
        print("[PASS] Agent: response received")

    def test_output_conversion(self):
        import subprocess
        subprocess.run(
            ["html-to-pptx", "tests/fixtures/slides.html",
             "tests/outputs/e2e_output.pptx"],
            capture_output=True, text=True
        )
        assert os.path.exists("tests/outputs/e2e_output.pptx")
        print("[PASS] Output: PPTX generated")

    def test_full_pipeline_timing(self):
        start = time.time()

        from ingestion.loaders import DocumentLoader
        from parsing.cleaner import DocumentCleaner
        from chunking.semantic_chunker import SlideAwareChunker
        from agent.rag_agent import agent

        loader = DocumentLoader()
        cleaner = DocumentCleaner()
        chunker = SlideAwareChunker()

        docs = loader.load("tests/fixtures/sample.pptx")
        t1 = time.time()

        cleaned = cleaner.clean(docs)
        t2 = time.time()

        nodes = chunker.chunk(cleaned)
        t3 = time.time()

        result = agent.invoke({
            "messages": [{"role": "user", "content": "Summarize"}],
            "context": ""
        })
        t4 = time.time()

        print(f"""
[PASS] End-to-End Pipeline
├── Ingestion:  {t1-start:.2f}s
├── Parsing:    {t2-t1:.2f}s
├── Chunking:   {t3-t2:.2f}s
├── Agent:      {t4-t3:.2f}s
└── Total:      {t4-start:.2f}s
        """)
PYEOF
```

### 10.2. Chạy end-to-end test

```bash
python -m pytest tests/test_pipeline_e2e.py -v --tb=short
```

**Tiêu chí PASS:** Tất cả test pass, không có lỗi import hoặc connection.

---

## TEST 11: Đánh giá RAG với RAGAS

**Mục đích:** Đo chất lượng pipeline RAG bằng các metrics chuẩn.

### 11.1. Cài đặt RAGAS

```bash
pip install ragas datasets
```

### 11.2. Tạo evaluation script

```python
# tests/test_ragas_eval.py
from datasets import Dataset
from ragas import evaluate
from ragas.metrics import (
    answer_relevancy,
    faithfulness,
    context_recall,
    context_precision,
)

# Chuẩn bị test set (question, context, ground_truth)
# Context lấy từ retrieval.retriever.retrieve() — Qdrant embedded
test_data = {
    "question": [
        "What is the main topic of slide 1?",
        "What tables are present in the PPTX?",
    ],
    "context": [
        ["Slide 1 content extracted from PPTX..."],
        ["Table data from slide 3..."],
    ],
    "ground_truth": [
        "The main topic is...",
        "The tables present are...",
    ],
}

dataset = Dataset.from_dict(test_data)

result = evaluate(
    dataset,
    metrics=[
        context_precision,
        faithfulness,
        answer_relevancy,
        context_recall,
    ],
)
print(result)
```

### 11.3. Chạy evaluation

```bash
python tests/test_ragas_eval.py
```

**Tiêu chí PASS:**
- `context_precision` > 0.7
- `faithfulness` > 0.8
- `answer_relevancy` > 0.7
- `context_recall` > 0.7

RAGAS cung cấp 4 metrics chính: **faithfulness** (đo hallucination), **context_precision** (chất lượng retrieval), **answer_relevancy** (độ liên quan câu trả lời), **context_recall** (khả năng retrieve đủ thông tin).

### 11.4. Tích hợp RAGAS vào CI

```bash
cat > tests/test_ragas_ci.py << 'EOF'
import pytest
from ragas import evaluate
from ragas.metrics import faithfulness

@pytest.mark.ragas_e2e
def test_ragas_faithfulness():
    result = evaluate(dataset, metrics=[faithfulness], in_ci=True)
    assert result["faithfulness"] > 0.8, f"Faithfulness too low: {result['faithfulness']}"
EOF
python -m pytest tests/test_ragas_ci.py -v -m ragas_e2e
```

**Tiêu chí PASS:** Faithfulness > 0.8. RAGAS hỗ trợ `in_ci` argument để tích hợp vào CI pipeline.

---

## Bảng tổng hợp Test Procedure

| Test | Thành phần | Tiêu chí PASS | Thời gian |
|---|---|---|---|
| **1** | Prerequisites | Tất cả ✅ (Docker/Chrome có thể skip) | 1 phút |
| **2** | Bifrost Gateway | `/health` healthy + chat completions OK | 2 phút |
| **3** | Codex Relay (`CODEX_HOME` cô lập) | `codex exec` thành công qua :4444 | 1 phút |
| **4** | pptx-tools MCP | `pptx_list_slides` + `pptx_get_slide_content` OK | 3 phút |
| **5** | Unstructured | PDF + HTML + PPTX parse OK | 5 phút |
| **6** | LlamaIndex Chunking | Slide-aware chunking + metadata OK | 3 phút |
| **7** | LangGraph Agent | Compile + invoke + unit test OK | 5 phút |
| **8** | Qdrant Retrieval (embedded) | Index + retrieval + ranking OK | 3 phút |
| **9** | Output Pipeline | html-to-pptx + frontend-slides OK | 5 phút |
| **10** | End-to-End | Tất cả test pass + timing logged | 10 phút |
| **11** | RAGAS Evaluation | Faithfulness > 0.8, Context Precision > 0.7 | 10 phút |

---

## Ghi chú quan trọng

1. **Thứ tự test:** Luôn chạy Test 1 → 11. Không bỏ qua test nào.
2. **Log:** Mọi test ghi vào `logs/test_YYYYMMDD_HHMMSS.log` (thư mục `logs/` đã git-ignored).
3. **Rollback:** Nếu test FAIL, sửa lỗi và chạy lại test đó, không cần chạy lại toàn bộ.
4. **RAGAS:** Cần OpenAI API key thật (không phải dummy) để chạy evaluation — hoặc một virtual key của Bifrost trỏ sang model hỗ trợ structured output.
5. **Qdrant embedded:** storage `data/qdrant/` bị lock bởi 1 process — đóng process trước khi chạy lại Test 8.
6. **pptx-tools:** Nếu NuGet publishing hoàn tất, có thể dùng `"command": "pptx"` thay vì `dotnet run`.
7. **Fixtures:** `tests/fixtures/` là file nhị phân sinh tự động — không commit; chạy lại `python scripts/create_fixtures.py` nếu thiếu.
