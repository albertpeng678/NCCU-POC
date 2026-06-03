# tests/ingestion/test_xlsx_parser.py
import pytest
from ingestion.xlsx_parser import course_id_to_url, parse_xlsx_row, build_courses_meta

def test_course_id_to_url_standard():
    url = course_id_to_url("000211012")
    assert url == "https://newdoc.nccu.edu.tw/teaschm/1142/schmPrv.jsp-yy=114&smt=2&num=000211&gop=01&s=2.html"

def test_course_id_to_url_zero_gop():
    url = course_id_to_url("000216001")
    assert url == "https://newdoc.nccu.edu.tw/teaschm/1142/schmPrv.jsp-yy=114&smt=2&num=000216&gop=00&s=1.html"

def test_parse_xlsx_row_standard():
    row = ("000211012", 3, "政治學                    ", "Political science", "蔡中民  ", "TSAI", "政治系  ", "Dept", "一D56", "mon13-16", "Room", "必/Required", "中文/Mandarin", "否/No", "note", "remark")
    result = parse_xlsx_row(row)
    assert result["course_id"] == "000211012"
    assert result["name"] == "政治學"
    assert result["credits"] == 3.0
    assert result["department"] == "政治系"
    assert result["teacher"] == "蔡中民"
    assert result["kind"] == "必修"
    assert result["source"] == "pending"
    assert "syllabus_url" in result

def test_parse_xlsx_row_skips_non_9digit_id():
    row = ("HEADER", None, "title", None, None, None, None, None, None, None, None, None, None, None, None, None)
    assert parse_xlsx_row(row) is None

def test_build_courses_meta_deduplicates_same_course_id():
    rows = [
        {"course_id": "000211012", "name": "政治學", "credits": 3.0, "department": "政治系",
         "teacher": "蔡中民", "kind": "必修", "syllabus_url": "https://x.com/a", "source": "pending"},
        {"course_id": "000211012", "name": "政治學", "credits": 3.0, "department": "政治系",
         "teacher": "蔡中民", "kind": "必修", "syllabus_url": "https://x.com/a", "source": "pending"},
    ]
    meta = build_courses_meta(rows)
    assert len(meta) == 1
