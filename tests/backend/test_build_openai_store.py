from scripts.build_openai_vector_store import filename_for, file_attributes, load_docs
import json
import tempfile
import os


def test_filename_is_course_id():
    assert filename_for("070415001") == "070415001.txt"


def test_attributes_carry_course_id_and_url():
    a = file_attributes({"course_id": "070415001", "syllabus_url": "http://x/1"})
    assert a["course_id"] == "070415001" and a["syllabus_url"] == "http://x/1"


def test_load_docs_parses_jsonl():
    records = [
        {"course_id": "070415001", "doc_text": "課綱A", "syllabus_url": "http://x/1"},
        {"course_id": "070415002", "doc_text": "課綱B", "syllabus_url": "http://x/2"},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        path = f.name
    try:
        docs = load_docs(path)
        assert len(docs) == 2
        assert docs[0]["course_id"] == "070415001"
        assert docs[1]["doc_text"] == "課綱B"
    finally:
        os.unlink(path)
