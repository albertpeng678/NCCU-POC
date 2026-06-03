# backend/judge.py
from __future__ import annotations
import json
from google import genai


def build_judge_prompt(career: str, result: dict) -> str:
    groups = result.get("groups", {})

    def format_group(name: str, courses: list[dict]) -> str:
        if not courses:
            return f"[{name}] （空）"
        lines = [f"[{name}]"]
        for c in courses:
            lines.append(f"  - {c.get('name', '?')} ({c.get('department', '?')}): {c.get('reason', '?')}")
        return "\n".join(lines)

    courses_text = "\n".join([
        format_group("核心技能", groups.get("core", [])),
        format_group("輔助技能", groups.get("supporting", [])),
        format_group("延伸視野", groups.get("extended", [])),
    ])

    return f"""你是大學課程推薦品質評審。請評估以下推薦結果的品質。

職涯目標：{career}

推薦課程：
{courses_text}

請從以下4個維度各給 1-5 分（1=很差，5=優秀）：

- relevance（相關性）：推薦課程與「{career}」職涯目標的整體相關程度
- grouping（分組品質）：核心/輔助/延伸三組的分類是否合理
- reason_quality（原因品質）：每門課的推薦原因是否具體、可幫助學生做決策
- diversity（多樣性）：推薦課程的系所、領域是否足夠多元，避免同質化

回傳 JSON（只回 JSON，不要其他文字）：
{{
  "relevance": <1-5>,
  "grouping": <1-5>,
  "reason_quality": <1-5>,
  "diversity": <1-5>,
  "critique": "<1-2句整體評語，指出最大優點和最需改進之處>"
}}"""


def parse_judge_response(raw: str) -> dict | None:
    """Parse LLM judge JSON response. Returns None if malformed."""
    try:
        data = json.loads(raw.strip())
        required = {"relevance", "grouping", "reason_quality", "diversity", "critique"}
        if not required.issubset(data.keys()):
            return None

        def clamp(v) -> int:
            return max(1, min(5, int(v)))

        r = clamp(data["relevance"])
        g = clamp(data["grouping"])
        rq = clamp(data["reason_quality"])
        d = clamp(data["diversity"])
        overall = round((r + g + rq + d) / 4)

        return {
            "judge_relevance_score": r,
            "judge_grouping_score": g,
            "judge_reason_score": rq,
            "judge_diversity_score": d,
            "judge_overall_score": overall,
            "judge_critique": str(data["critique"])[:500],
        }
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None


async def evaluate_recommendation(
    client: genai.Client, career: str, result: dict
) -> dict | None:
    """Run LLM judge. Returns parsed scores or None on failure."""
    try:
        prompt = build_judge_prompt(career, result)
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        return parse_judge_response(resp.text)
    except Exception as e:
        print(f"[judge] Evaluation failed: {e}")
        return None
