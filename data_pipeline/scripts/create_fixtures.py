#!/usr/bin/env python3
"""Sinh dữ liệu mẫu cho tests/fixtures — thay vì commit file nhị phân.

Chạy: python scripts/create_fixtures.py
Yêu cầu: python-pptx, reportlab (có thể cài thêm: pip install reportlab)
"""
import os
import sys

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures")


def make_pptx(path: str) -> None:
    """PPTX 5 slides: text + bảng, để test ingestion PPTX và pptx-tools."""
    from pptx import Presentation
    from pptx.util import Inches, Pt

    prs = Presentation()
    titles = [
        "Data Pipeline Overview",
        "Ingestion Sources",
        "Quarterly Results Table",
        "Chunking Strategy",
        "Next Steps",
    ]
    for i, title in enumerate(titles, start=1):
        slide = prs.slides.add_slide(prs.slide_layouts[1])  # title + content
        slide.shapes.title.text = title
        body = slide.placeholders[1].text_frame
        body.text = f"Slide {i} body content for testing ingestion."
        p = body.add_paragraph()
        p.text = "Second bullet with enough length to be kept by the cleaner."
        if i == 3:  # slide có bảng
            rows, cols = 3, 3
            table = slide.shapes.add_table(
                rows, cols, Inches(1), Inches(3), Inches(8), Inches(2)
            ).table
            for r in range(rows):
                for c in range(cols):
                    table.cell(r, c).text = f"R{r}C{c}"
    prs.save(path)
    print(f"Created {path}")


def make_pdf(path: str) -> None:
    """PDF 3 trang có bảng, để test partition_pdf."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Table
        from reportlab.lib.styles import getSampleStyleSheet
    except ImportError:
        print("Cần reportlab: pip install reportlab")
        sys.exit(1)

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(path, pagesize=A4)
    story = []
    for page in range(1, 4):
        story.append(Paragraph(f"Sample PDF — Page {page}", styles["Title"]))
        story.append(Paragraph(
            f"This is page {page} of the test fixture for pipeline ingestion testing.",
            styles["Normal"],
        ))
        story.append(Table([["A", "B", "C"], ["1", "2", "3"]]))
        doc_page_break = None  # SimpleDocTemplate tự phân trang theo flow
    doc.build(story)
    print(f"Created {path}")


def make_nested_html(path: str) -> None:
    """HTML lồng sâu (depth > 10) để test huge_tree parsing."""
    depth = 30
    inner = "<li>Deep nested item — leaf content for parsing test.</li>\n"
    content = inner
    for _ in range(depth):
        content = f"<ul>\n<li>Level wrapper\n{content}</li>\n</ul>\n"
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Deep Nested Fixture</title></head>
<body>
<h1>Deep HTML Fixture</h1>
<p>Outer paragraph before nesting.</p>
{content}
<p>Paragraph after nesting.</p>
</body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Created {path}")


def make_slides_html(path: str) -> None:
    """HTML slide deck đơn giản để test html-to-pptx conversion."""
    slides = ""
    for i in range(1, 4):
        slides += f"""
  <section class="slide">
    <h1>Slide {i}</h1>
    <p>Editable text content for slide {i} — mapped to native PPTX shapes.</p>
    <ul><li>Point A</li><li>Point B</li></ul>
  </section>"""
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  .slide {{ width: 1280px; height: 720px; page-break-after: always;
            padding: 60px; box-sizing: border-box; }}
</style></head>
<body>{slides}</body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Created {path}")


if __name__ == "__main__":
    os.makedirs(FIXTURES, exist_ok=True)
    make_pptx(os.path.join(FIXTURES, "sample.pptx"))
    make_pdf(os.path.join(FIXTURES, "sample.pdf"))
    make_nested_html(os.path.join(FIXTURES, "sample.html"))
    make_slides_html(os.path.join(FIXTURES, "slides.html"))
    print("Done. Fixtures sẵn sàng cho TEST_PROCEDURE.md.")
