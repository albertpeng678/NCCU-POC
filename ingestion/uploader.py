# ingestion/uploader.py
from __future__ import annotations
import tempfile
import time
from pathlib import Path
from google import genai


def create_store(client: genai.Client, display_name: str = "nccu-courses-1142") -> str:
    """Create a new File Search Store. Returns store name."""
    store = client.file_search_stores.create(config={"display_name": display_name})
    print(f"[uploader] Created store: {store.name}")
    return store.name


def upload_document(
    client: genai.Client,
    store_name: str,
    course_id: str,
    syllabus_url: str,
    document_text: str,
) -> bool:
    """Upload one course document to the File Search Store.

    Returns True on success, False on failure.
    """
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(document_text)
            tmp_path = Path(f.name)

        try:
            # Upload file to Gemini Files API
            uploaded = client.files.upload(
                file=tmp_path,
                config={"display_name": f"course-{course_id}"},
            )

            # Import into File Search Store
            op = client.file_search_stores.import_file(
                file_search_store_name=store_name,
                file_name=uploaded.name,
                config={
                    "custom_metadata": [
                        {"key": "course_id", "string_value": course_id},
                        {"key": "syllabus_url", "string_value": syllabus_url},
                    ]
                },
            )
            # Poll until the LRO completes (no .result() on ImportFileOperation)
            # 240s：File Search import 為伺服器端索引，併發上傳時佇列較久，給足時間避免過早逾時
            deadline = time.time() + 240
            while not op.done:
                if time.time() > deadline:
                    raise TimeoutError(f"import_file timed out for {course_id}")
                time.sleep(2)
                op = client.operations.get(op)
            if op.error:
                raise RuntimeError(f"import_file error: {op.error}")
            return True
        finally:
            tmp_path.unlink(missing_ok=True)

    except Exception as e:
        print(f"[uploader] Failed to upload {course_id}: {e}")
        return False
