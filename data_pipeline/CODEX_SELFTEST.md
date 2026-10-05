# CODEX_SELFTEST — Tự chạy test và đào file trong Codex

Bạn là Codex. Nhiệm vụ: **tự tạo fixture, tự chạy test, tự đào log, tự báo cáo**. Không hỏi lại. Không dừng giữa chừng. Ghi mọi thứ vào `logs/`.

> **Ngữ cảnh kiến trúc (đọc trước khi chạy):**
> - Retrieval engine là **Qdrant embedded** (`data/qdrant/`) — KHÔNG có RAGFlow, không probe port 9380.
> - Routing: ns-shim :8081 (Codex CLI hằng ngày) + codex-relay :4444 (chỉ agent pipeline, env `CODEX_HOME=config/codex-home`).
> - Python: dùng `.venv` (3.11–3.13) nếu có — máy hệ thống là 3.14, ngoài vùng hỗ trợ của Unstructured.
> - Chi tiết đầy đủ: `PREREQUISITES.md` mục 0, `TEST_PROCEDURE.md`, `AGENTS.md` mục 4.1.

---

## BƯỚC 0 — Bootstrap (tự tạo nếu chưa có)

Chạy tuần tự, bỏ qua lỗi nếu file đã tồn tại:

```bash
mkdir -p tests/fixtures tests/outputs logs scripts data/qdrant
touch logs/selftest_$(date +%Y%m%d_%H%M%S).log
```

Sau đó **tự đọc cây thư mục hiện tại** để biết đã có gì:

```bash
find . -maxdepth 2 -type d -not -path "./.venv*" -not -path "./repos/*" | sort
find . -maxdepth 2 \( -name "*.py" -o -name "*.md" -o -name "*.toml" -o -name "*.json" \) -not -path "./repos/*" -not -path "./.venv*" | sort
```

Ghi output vào `logs/00_bootstrap.log`.

---

## BƯỚC 1 — Tự tạo fixture

Fixtures được sinh từ script có sẵn — **tái sử dụng, đừng viết lại**:

```bash
if [ -f scripts/create_fixtures.py ]; then
    python scripts/create_fixtures.py 2>&1 | tee logs/01_fixtures.log
else
    echo "[BLOCKER] scripts/create_fixtures.py không tồn tại" | tee logs/01_fixtures.log
fi
```

Kết quả kỳ vọng trong `tests/fixtures/`:

| File | Mục đích |
|---|---|
| `sample.pptx` | PPTX 5 slides + bảng — ingestion PPTX, pptx-tools |
| `sample.pdf` | PDF 3 trang có bảng — partition_pdf (cần reportlab; script tự SKIP nếu thiếu) |
| `sample.html` | HTML lồng sâu 30 cấp — test huge_tree |
| `slides.html` | HTML slide deck — html-to-pptx |

**Sau bước này, xác nhận file tồn tại:**

```bash
ls -la tests/fixtures/ | tee -a logs/01_fixtures.log
```

---

## BƯỚC 2 — Đào môi trường (environment probe)

Chạy từng lệnh, **không fail-fast**, ghi hết vào log:

```bash
{
  echo "=== Python ==="; python --version
  echo "=== pip packages ==="; pip list 2>/dev/null | grep -Ei "unstructured|llama|langgraph|langchain|pptx|qdrant|openai|codex"
  echo "=== Node ==="; node --version 2>/dev/null || echo "missing"
  echo "=== .NET ==="; dotnet --version 2>/dev/null || echo "missing"
  echo "=== Docker ==="; docker --version 2>/dev/null || echo "missing"
  echo "=== Chrome ==="; which google-chrome chromium chromium-browser 2>/dev/null || echo "missing"
  echo "=== Ports ==="; (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | grep -E "8080|4444|3001|6333" || echo "none listening"
} 2>&1 | tee logs/02_env_probe.log
```

**Sau đó tự đọc log này và phân loại:**
- Cái gì có → chạy tiếp
- Cái gì thiếu → ghi vào mục "BLOCKERS" trong báo cáo cuối

Lưu ý port: **8080** Bifrost, **4444** codex-relay, **3001** pptx-tools, **6333** Qdrant server (không dùng — pipeline chạy embedded; nếu thấy listening thì là service khác).

---

## BƯỚC 3 — Đào service đang chạy

Với mỗi service dưới đây, thử ping. Nếu fail → ghi `[SKIP]` và tiếp tục, **không dừng**:

```bash
{
  echo "=== Bifrost ==="
  curl -sf -m 3 http://localhost:8080/health && echo "OK" || echo "SKIP"

  echo "=== Codex Relay (pipeline agent, :4444) ==="
  curl -sf -m 3 http://127.0.0.1:4444/v1/models | head -c 200 && echo "" && echo "OK" || echo "SKIP"

  echo "=== ns-shim (Codex CLI hằng ngày, :8081) ==="
  curl -sf -m 3 -X HEALTH http://127.0.0.1:8081/ && echo "OK" || echo "SKIP"

  echo "=== pptx-tools MCP ==="
  curl -sf -m 3 http://localhost:3001/health && echo "OK" || echo "SKIP"

  echo "=== Qdrant embedded (không có service — kiểm tra storage) ==="
  ls data/qdrant/ 2>/dev/null && echo "STORAGE EXISTS" || echo "SKIP (chưa index lần nào)"
} 2>&1 | tee logs/03_services.log
```

> Qdrant chạy **embedded mode** — không probe port nào cả. Chỉ check thư mục storage. RAGFlow **đã bị loại khỏi kiến trúc** — không probe.

---

## BƯỚC 4 — Đào code hiện có

Quét code trong repo để biết module nào đã tồn tại:

```bash
{
  echo "=== Modules ==="
  for d in ingestion parsing chunking embedding retrieval output agent; do
    if [ -d "$d" ]; then
      echo "[$d]"
      ls -la "$d"/*.py 2>/dev/null || echo "  (empty)"
    else
      echo "[$d] MISSING"
    fi
  done

  echo ""
  echo "=== Import smoke test ==="
  python - <<'PY' 2>&1
mods = ["ingestion.loaders", "parsing.cleaner", "chunking.semantic_chunker",
        "embedding.qdrant_store", "retrieval.retriever", "agent.rag_agent",
        "output.html_to_pptx", "output.pptx_to_html"]
for m in mods:
    try:
        __import__(m)
        print(f"[OK] {m}")
    except Exception as e:
        print(f"[FAIL] {m}: {type(e).__name__}: {e}")
PY
} 2>&1 | tee logs/04_code_probe.log
```

**Đọc `logs/04_code_probe.log` để biết module nào ready, module nào cần tự viết.**

> Tên module khớp với `SETUP_PLAN.md`: `embedding.qdrant_store` + `retrieval.retriever` (Qdrant embedded — Giai đoạn 4), không có `embedding.embedder`/`embedding.vector_store` riêng.

---

## BƯỚC 5 — Tự viết test nếu module đã sẵn sàng

Với mỗi module **import được** (từ BƯỚC 4), tự sinh test tương ứng và chạy.

Tạo `tests/test_selftest_pipeline.py`:

```python
import os, time, json, subprocess
import pytest

LOGDIR = "logs"
FIXTURES = "tests/fixtures"
OUT = "tests/outputs"
os.makedirs(OUT, exist_ok=True)


def log(name, data):
    with open(f"{LOGDIR}/{name}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def test_01_fixtures_exist():
    files = ["sample.pptx", "sample.html"]
    for f in files:
        p = os.path.join(FIXTURES, f)
        assert os.path.exists(p), f"Missing {p}"
    log("t01_fixtures", {"files": [os.path.getsize(os.path.join(FIXTURES, f)) for f in files]})


def test_02_ingestion_pptx():
    from ingestion.loaders import DocumentLoader
    loader = DocumentLoader()
    docs = loader.load(f"{FIXTURES}/sample.pptx")
    assert len(docs) > 0, "No docs loaded"
    log("t02_ingestion_pptx", {"count": len(docs), "sample": str(docs[0])[:200]})


def test_03_ingestion_html():
    from unstructured.partition.html import partition_html
    os.environ["UNSTRUCTURED_HTML_HUGE_TREE"] = "1"
    els = partition_html(f"{FIXTURES}/sample.html", strategy="html")
    assert len(els) > 0
    log("t03_ingestion_html", {"count": len(els)})


def test_04_cleaner():
    from ingestion.loaders import DocumentLoader
    from parsing.cleaner import DocumentCleaner
    docs = DocumentLoader().load(f"{FIXTURES}/sample.pptx")
    cleaned = DocumentCleaner().clean(docs)
    assert len(cleaned) > 0
    log("t04_cleaner", {"in": len(docs), "out": len(cleaned)})


def test_05_chunking():
    from llama_index.core.schema import Document
    from chunking.semantic_chunker import SlideAwareChunker
    docs = [
        Document(text="short", metadata={"slide_number": 1}),
        Document(text="long " * 500, metadata={"slide_number": 2}),
    ]
    nodes = SlideAwareChunker().chunk(docs)
    assert len(nodes) >= 2
    log("t05_chunking", {"nodes": len(nodes), "meta": [n.metadata for n in nodes[:3]]})


def test_06_qdrant_index_and_retrieve():
    """Qdrant embedded — index + retrieval (thay test RAGFlow của bản gốc)."""
    from embedding.qdrant_store import build_index
    from retrieval.retriever import retrieve
    from llama_index.core import Document

    build_index([Document(text="Selftest: Qdrant embedded index và retrieval hoạt động.")])
    context = retrieve("selftest qdrant", top_k=3)
    assert context.strip(), "No context retrieved"
    log("t06_qdrant", {"context_chars": len(context)})


def test_07_agent_compile():
    from agent.rag_agent import agent
    assert agent is not None
    log("t07_agent", {"nodes": list(agent.get_graph().nodes.keys())})


def test_08_output_html2pptx():
    if subprocess.run(["which", "html-to-pptx"], capture_output=True).returncode != 0:
        pytest.skip("html-to-pptx not installed")
    out = f"{OUT}/selftest.pptx"
    subprocess.run(["html-to-pptx", f"{FIXTURES}/slides.html", out], check=True)
    assert os.path.exists(out)
    from pptx import Presentation
    prs = Presentation(out)
    log("t08_output", {"slides": len(prs.slides), "size": os.path.getsize(out)})


def test_99_summary():
    """Tổng hợp tất cả log JSON đã ghi."""
    summary = {}
    for f in os.listdir(LOGDIR):
        if f.startswith("t") and f.endswith(".json"):
            with open(os.path.join(LOGDIR, f), encoding="utf-8") as fh:
                summary[f] = json.load(fh)
    log("99_summary", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
```

Chạy:

```bash
python -m pytest tests/test_selftest_pipeline.py -v --tb=short 2>&1 | tee logs/05_pytest.log
```

> ⚠️ `test_06_qdrant_index_and_retrieve` sẽ FAIL nếu một process khác đang mở `data/qdrant/` (storage lock) — đó là giới hạn của embedded mode, không phải bug. Ghi vào BLOCKERS và chạy lại khi storage rảnh.

---

## BƯỚC 6 — Nếu module thiếu, tự sinh stub tối thiểu

Với mỗi module import fail ở BƯỚC 4, **tự sinh file stub** để test không block. Stub phải có docstring ghi rõ `TODO` để review sau.

**Ưu tiên copy code mẫu từ `SETUP_PLAN.md`** (Giai đoạn 3 và 4 có implementation đầy đủ cho loaders/cleaner/chunker/qdrant_store/retriever/rag_agent) thay vì tự chế — chỉ fallback sang stub nếu code mẫu trong plan không chạy được.

Ví dụ `ingestion/loaders.py` nếu thiếu (đã trùng với SETUP_PLAN.md 3.1, trừ MCP PPTX — stub bản skeleton):

```python
"""AUTO-GENERATED STUB — TODO: thay bằng implementation thật từ SETUP_PLAN.md Giai đoạn 3.1."""
import os
from unstructured.partition.pdf import partition_pdf
from unstructured.partition.html import partition_html
from unstructured.partition.docx import partition_docx
from unstructured.partition.auto import partition

os.environ.setdefault("UNSTRUCTURED_HTML_HUGE_TREE", "1")

class DocumentLoader:
    def load(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pptx":
            raise NotImplementedError("Cần pptx-tools MCP chạy ở :3001 — SETUP_PLAN.md Giai đoạn 2.4/3.1")
        if ext == ".pdf":
            return partition_pdf(path, strategy="hi_res")
        if ext in (".docx", ".doc"):
            return partition_docx(path)
        if ext in (".html", ".htm"):
            return partition_html(path, strategy="html")
        return partition(filename=path)
```

Tương tự cho `parsing/cleaner.py`, `chunking/semantic_chunker.py`, `embedding/qdrant_store.py`, `retrieval/retriever.py`, `agent/rag_agent.py`, `output/html_to_pptx.py`.

**Sau khi sinh stub, chạy lại BƯỚC 5.**

---

## BƯỚC 7 — Tổng hợp báo cáo

Tự tạo `logs/SELFTEST_REPORT.md` với các mục sau. **Đọc trực tiếp từ log JSON, không bịa số.**

```markdown
# SELFTEST REPORT — <timestamp>

## 1. Môi trường
- Python: ...
- Node: ...
- .NET: ...
- Docker: ...
- Chrome: ...

## 2. Services
| Service | Status | Ghi chú |
|---|---|---|
| Bifrost :8080 | OK/SKIP | ... |
| Codex Relay :4444 (pipeline) | OK/SKIP | ... |
| ns-shim :8081 (Codex CLI) | OK/SKIP | ... |
| pptx-tools MCP :3001 | OK/SKIP | ... |
| Qdrant embedded storage | OK/SKIP | ... |

## 3. Modules
| Module | Import | Test |
|---|---|---|
| ingestion.loaders | OK/FAIL | PASS/FAIL |
| parsing.cleaner | ... | ... |
| chunking.semantic_chunker | ... | ... |
| embedding.qdrant_store | ... | ... |
| retrieval.retriever | ... | ... |
| agent.rag_agent | ... | ... |
| output.html_to_pptx | ... | ... |

## 4. Test results
- Tổng: X passed / Y failed / Z skipped
- Chi tiết: (paste từ logs/05_pytest.log)

## 5. BLOCKERS
- [ ] ...
- [ ] ...

## 6. NEXT ACTIONS
1. ...
2. ...
```

Sau đó **in ra màn hình** nội dung `SELFTEST_REPORT.md`.

---

## BƯỚC 8 — Xuất kết quả cho người dùng

In ra terminal theo format cố định:

```
══════════════════════════════════════════════
  CODEX SELFTEST — KẾT QUẢ
══════════════════════════════════════════════
✓ Fixtures:   X file đã tạo
✓ Env:        X/Y tool có sẵn
✓ Services:   X/Y service online
✓ Modules:    X/Y module import OK
✓ Tests:      X passed / Y failed / Z skipped

📁 Logs:      logs/
📄 Report:    logs/SELFTEST_REPORT.md
🔴 Blockers:  <số lượng>
🟢 Ready:     <số lượng module sẵn sàng>

→ Đọc logs/SELFTEST_REPORT.md để xem chi tiết.
══════════════════════════════════════════════
```

---

## QUY TẮC BẮT BUỘC

1. **Không hỏi lại** — tự quyết định và chạy tiếp.
2. **Không fail-fast** — service nào fail thì ghi SKIP, chạy tiếp service khác.
3. **Mọi bước có log** — ghi vào `logs/NN_*.log` hoặc `logs/t*.json`.
4. **Stub thay vì dừng** — module thiếu thì sinh stub tối thiểu (ưu tiên code mẫu từ `SETUP_PLAN.md`) để test chạy được.
5. **Báo cáo dựa trên log thật** — không bịa số, không đoán.
6. **Không probe RAGFlow :9380** — đã bị loại khỏi kiến trúc.
7. **Không sửa `~/.codex/config.toml`** — routing hiện tại thuộc về Codex CLI hằng ngày; relay của pipeline chỉ chạy trong `CODEX_HOME=config/codex-home`.
8. **Cuối cùng in terminal summary** theo format ở BƯỚC 8.

---

## SAU KHI CHẠY

Đọc `logs/SELFTEST_REPORT.md`. Nếu có BLOCKERS:
- Sửa blocker (cài tool, sửa config, thay stub bằng code thật từ `SETUP_PLAN.md`).
- Chạy lại `codex exec --sandbox workspace-write "Đọc CODEX_SELFTEST.md, bỏ qua BƯỚC 1 nếu fixture đã có."`

Nếu tất cả PASS:
- Chạy `./scripts/run_all_tests.sh` để chạy bộ test đầy đủ theo `TEST_PROCEDURE.md` (Test 1 → 11).
- Chuyển sang bước tích hợp RAGAS evaluation (TEST_PROCEDURE.md Test 11 — cần virtual key Bifrost thật, không dùng dummy-key).
