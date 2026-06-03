# backend/qa_judge.py
from __future__ import annotations

import json

from google import genai


def build_qa_judge_prompt(
    question: str, answer: str, citation_names: list[str]
) -> str:
    """Build RAGAS-style judge prompt for QA evaluation."""
    citations_text = "、".join(citation_names) if citation_names else "（無引用課程）"
    return f"""你是課程問答品質評審（RAGAS 方法）。請評估以下問答的品質。

學生問題：{question}

系統回答：{answer}

引用課程：{citations_text}

請從以下 3 個維度各給 1-5 分（1=很差，5=優秀）：

- faithfulness（忠實性）：回答內容是否忠實於引用課程資料，沒有捏造資訊
- relevancy（相關性）：回答是否切題，有效回應學生的問題
- context_precision（脈絡精確性）：引用的課程資料是否精準、恰當，沒有包含不相關資訊

回傳 JSON（只回 JSON，不要其他文字）：
{{
  "faithfulness": <1-5>,
  "relevancy": <1-5>,
  "context_precision": <1-5>,
  "critique": "<1-2句整體評語，指出最大優點和最需改進之處>"
}}"""


def parse_qa_judge(raw: str) -> dict | None:
    """Parse LLM judge JSON response for QA.

    Returns dict with keys:
        judge_faithfulness, judge_relevancy, judge_context_prec,
        judge_overall, judge_critique
    Returns None if malformed.
    """
    try:
        data = json.loads(raw.strip())
        required = {"faithfulness", "relevancy", "context_precision", "critique"}
        if not required.issubset(data.keys()):
            return None

        def clamp(v) -> int:
            return max(1, min(5, int(v)))

        f = clamp(data["faithfulness"])
        r = clamp(data["relevancy"])
        c = clamp(data["context_precision"])
        overall = round((f + r + c) / 3)

        return {
            "judge_faithfulness": f,
            "judge_relevancy": r,
            "judge_context_prec": c,
            "judge_overall": overall,
            "judge_critique": str(data["critique"])[:500],
        }
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None


async def evaluate_qa(
    client: genai.Client,
    question: str,
    answer: str,
    citation_names: list[str],
) -> dict | None:
    """Run RAGAS 3-dim LLM judge for QA evaluation.

    Returns parsed scores dict or None on failure.
    """
    try:
        prompt = build_qa_judge_prompt(question, answer, citation_names)
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        return parse_qa_judge(resp.text)
    except Exception as e:
        print(f"[qa_judge] Evaluation failed: {e}")
        return None
