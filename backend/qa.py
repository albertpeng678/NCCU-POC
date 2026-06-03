# backend/qa.py
from __future__ import annotations

import json
import re
import time
from typing import Optional

from google import genai
from google.genai._interactions.types.tool_param import FileSearch


_PROMPT_TEMPLATE = """\
你是 NCCU（國立政治大學）課程諮詢助手。請根據課程知識庫回答學生問題。

學生問題：{question}

請：
1. 根據知識庫中的課程大綱和資料回答問題
2. 引用具體課程名稱和資訊
3. 回答完畢後，輸出以下 JSON（只輸出 JSON 區塊，不要其他文字）：

```json
{{
  "answer": "<完整的回答文字，包含課程資訊>",
  "followup_suggestions": [
    "<建議的後續問題1>",
    "<建議的後續問題2>",
    "<建議的後續問題3>"
  ]
}}
```

followup_suggestions 請提供 2-3 個與問題相關的後續問題建議。
"""


def parse_qa_response(raw: str) -> dict:
    """Extract JSON object {answer, followup_suggestions} from possibly-fenced text.

    Fallback: {"answer": raw.strip(), "followup_suggestions": []}
    """
    if not raw or not raw.strip():
        return {"answer": "", "followup_suggestions": []}

    stripped = raw.strip()

    # Try to find a fenced JSON block first
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if fence_match:
        try:
            data = json.loads(fence_match.group(1))
            if isinstance(data, dict) and "answer" in data:
                return {
                    "answer": str(data.get("answer", "")),
                    "followup_suggestions": list(data.get("followup_suggestions", [])),
                }
        except json.JSONDecodeError:
            pass

    # Try to find an inline JSON object {…}
    brace_match = re.search(r"\{.*\}", stripped, re.DOTALL)
    if brace_match:
        try:
            data = json.loads(brace_match.group(0))
            if isinstance(data, dict) and "answer" in data:
                return {
                    "answer": str(data.get("answer", "")),
                    "followup_suggestions": list(data.get("followup_suggestions", [])),
                }
        except json.JSONDecodeError:
            pass

    # Fallback: treat the whole response as the answer
    return {"answer": stripped, "followup_suggestions": []}


def extract_citations(course_ids: list[str], meta: dict) -> list[dict]:
    """Dedup course_ids preserving order, look up meta, skip missing, return enriched dicts."""
    seen: set[str] = set()
    result = []
    for cid in course_ids:
        if cid in seen:
            continue
        seen.add(cid)
        m = meta.get(cid)
        if not m:
            continue
        result.append({
            "course_id": cid,
            "name": m.get("name", ""),
            "department": m.get("department", ""),
            "teacher": m.get("teacher", ""),
            "credits": m.get("credits"),
            "syllabus_url": m.get("syllabus_url", ""),
        })
    return result


def extract_course_ids_from_grounding(response) -> list[str]:
    """Parse Gemini interactions response to extract course_ids.

    Strategy 1: FileCitation annotations in TextContent — file_name like "course-{id}"
    Strategy 2: FileSearchResultContent results — display_name like "course-{id}"
    Strategy 3: Regex scan of answer text for 9-digit codes

    Returns list of 9-digit course_id strings (best-effort).
    """
    try:
        found: list[str] = []
        _COURSE_ID_RE = re.compile(r"\b(\d{9})\b")
        _NAME_RE = re.compile(r"course-(\d{9})")

        # Walk through outputs (list of Turn objects)
        outputs = getattr(response, "outputs", None) or []
        answer_text = ""

        for turn in outputs:
            content_list = getattr(turn, "content", None) or []
            for item in content_list:
                item_type = getattr(item, "type", None)

                # TextContent: look at annotations for FileCitation
                if item_type == "text":
                    answer_text += getattr(item, "text", "") or ""
                    annotations = getattr(item, "annotations", None) or []
                    for ann in annotations:
                        ann_type = getattr(ann, "type", None)
                        if ann_type == "file_citation":
                            file_name = getattr(ann, "file_name", None) or ""
                            m = _NAME_RE.search(file_name)
                            if m:
                                found.append(m.group(1))

                # FileSearchResultContent: look at results
                elif item_type == "file_search_call":
                    pass  # this is the call, not the results

                elif item_type == "file_search_result":
                    results = getattr(item, "result", None) or []
                    for chunk in results:
                        # chunk may be a dict or object
                        if isinstance(chunk, dict):
                            name = chunk.get("file", {}).get("display_name", "") or chunk.get("display_name", "")
                        else:
                            file_obj = getattr(chunk, "file", None)
                            name = getattr(file_obj, "display_name", None) if file_obj else None
                            if not name:
                                name = getattr(chunk, "display_name", None) or ""
                        m = _NAME_RE.search(str(name))
                        if m:
                            found.append(m.group(1))

        # Strategy 3: regex scan answer text for 9-digit codes
        if answer_text:
            for m in _COURSE_ID_RE.finditer(answer_text):
                found.append(m.group(1))

        # Dedup preserving order
        seen: set[str] = set()
        unique = []
        for cid in found:
            if cid not in seen:
                seen.add(cid)
                unique.append(cid)
        return unique

    except Exception:
        return []


def answer_question(
    client: genai.Client,
    store_name: str,
    question: str,
    previous_interaction_id: Optional[str] = None,
) -> dict:
    """Call Gemini interactions API to answer a course question.

    Returns:
        {
            "answer": str,
            "followup_suggestions": list[str],
            "citations_course_ids": list[str],
            "interaction_id": str,
            "latency_ms": int,
        }
    """
    t0 = time.monotonic()
    prompt = _PROMPT_TEMPLATE.format(question=question)

    kwargs: dict = {
        "model": "gemini-2.5-flash",
        "input": prompt,
        "tools": [{"type": "file_search", "file_search_store_names": [store_name]}],
    }
    if previous_interaction_id is not None:
        kwargs["previous_interaction_id"] = previous_interaction_id

    response = client.interactions.create(**kwargs)
    latency_ms = int((time.monotonic() - t0) * 1000)

    # Extract text from outputs
    raw_text = ""
    outputs = getattr(response, "outputs", None) or []
    for turn in outputs:
        content_list = getattr(turn, "content", None) or []
        for item in content_list:
            if getattr(item, "type", None) == "text":
                raw_text += getattr(item, "text", "") or ""

    parsed = parse_qa_response(raw_text)
    citations_course_ids = extract_course_ids_from_grounding(response)

    return {
        "answer": parsed["answer"],
        "followup_suggestions": parsed["followup_suggestions"],
        "citations_course_ids": citations_course_ids,
        "interaction_id": getattr(response, "id", None),
        "latency_ms": latency_ms,
    }
