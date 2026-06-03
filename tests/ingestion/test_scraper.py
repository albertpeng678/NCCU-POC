# tests/ingestion/test_scraper.py
import pytest
from ingestion.scraper import extract_text_from_html, build_document_text

def test_extract_text_from_html_returns_meaningful_content():
    html = """<html><body>
    <h2>政治學</h2>
    <div>課程簡介</div>
    <p>旨在致力基礎學科之訓練，培養對政治學的興趣。</p>
    <script>var x = 1;</script>
    <style>.nav{color:red}</style>
    </body></html>"""
    text = extract_text_from_html(html)
    assert "政治學" in text
    assert "旨在致力基礎學科" in text
    assert "var x = 1" not in text  # scripts stripped
    assert ".nav" not in text         # styles stripped

def test_extract_text_from_html_empty_returns_empty_string():
    assert extract_text_from_html("") == ""
    assert extract_text_from_html("<html></html>") == ""

def test_build_document_text_includes_course_id_header():
    meta = {"course_id": "000211012", "name": "政治學", "department": "政治系",
            "teacher": "蔡中民", "credits": 3.0, "kind": "必修",
            "syllabus_url": "https://x.com/a", "source": "pending"}
    syllabus_text = "課程介紹內容..."
    skill_bridge = "培養技能：政治分析\n適合職涯：公務員"
    doc = build_document_text(meta, syllabus_text, skill_bridge)
    assert "課程代號: 000211012" in doc
    assert "政治學" in doc
    assert "課程介紹內容" in doc
    assert "培養技能：政治分析" in doc

def test_build_document_text_with_no_syllabus_uses_name_only():
    meta = {"course_id": "000211012", "name": "政治學", "department": "政治系",
            "teacher": "蔡中民", "credits": 3.0, "kind": "必修",
            "syllabus_url": "https://x.com/a", "source": "name_only"}
    doc = build_document_text(meta, syllabus_text=None, skill_bridge="培養技能：政治分析")
    assert "課程代號: 000211012" in doc
    assert "政治學" in doc
    assert "培養技能：政治分析" in doc
