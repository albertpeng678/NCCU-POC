# ingestion/xlsx_parser.py
from __future__ import annotations
import re
import ssl
import tempfile
import urllib.request
import openpyxl
from pathlib import Path

XLSX_URL = "https://newdoc.nccu.edu.tw/teaschm/CoursesList.xlsx"
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE
_SSL_CTX.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)


def course_id_to_url(course_id: str) -> str:
    """Convert 9-digit course ID to syllabus URL."""
    num = course_id[:6]
    gop = course_id[6:8]
    s = course_id[8]
    return (
        f"https://newdoc.nccu.edu.tw/teaschm/1142/schmPrv.jsp"
        f"-yy=114&smt=2&num={num}&gop={gop}&s={s}.html"
    )


def parse_xlsx_row(row: tuple) -> dict | None:
    """Parse one XLSX data row. Returns None if row is not a valid course."""
    course_id = str(row[0]).strip() if row[0] else ""
    if not re.fullmatch(r"\d{9}", course_id):
        return None

    name_raw = str(row[2]).strip() if row[2] else ""
    name = name_raw.split("\n")[0].strip()

    teacher_raw = str(row[4]).strip() if row[4] else ""
    teacher = teacher_raw.split("\n")[0].strip()

    dept_raw = str(row[6]).strip() if row[6] else ""
    department = dept_raw.split("\n")[0].strip()

    kind_raw = str(row[11]).strip() if row[11] else ""
    kind = "必修" if "必" in kind_raw else "選修"

    try:
        credits = float(row[1]) if row[1] is not None else 0.0
    except (ValueError, TypeError):
        credits = 0.0

    return {
        "course_id": course_id,
        "name": name,
        "credits": credits,
        "department": department,
        "teacher": teacher,
        "kind": kind,
        "syllabus_url": course_id_to_url(course_id),
        "source": "pending",
    }


def build_courses_meta(rows: list[dict]) -> dict[str, dict]:
    """Deduplicate by course_id and return keyed dict."""
    meta = {}
    for row in rows:
        if row["course_id"] not in meta:
            meta[row["course_id"]] = row
    return meta


def download_and_parse_xlsx(xlsx_path: Path | None = None) -> dict[str, dict]:
    """Download XLSX and parse all courses. Returns courses_meta dict."""
    if xlsx_path is None:
        req = urllib.request.Request(
            XLSX_URL,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://qrysub.nccu.edu.tw/"},
        )
        with urllib.request.urlopen(req, context=_SSL_CTX) as resp:
            data = resp.read()
        tmp_path = Path(tempfile.gettempdir()) / "CoursesList.xlsx"
        tmp_path.write_bytes(data)
        xlsx_path = tmp_path

    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    try:
        ws = wb.active
        rows = []
        for row in ws.iter_rows(min_row=3, values_only=True):  # skip 2 header rows
            parsed = parse_xlsx_row(row)
            if parsed:
                rows.append(parsed)
    finally:
        wb.close()
    return build_courses_meta(rows)
