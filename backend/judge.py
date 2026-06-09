# backend/judge.py
from __future__ import annotations
import os
from pydantic import BaseModel


OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")


def _clamp(v) -> int:
    return max(1, min(5, int(v)))


class _JudgeOutput(BaseModel):
    relevance: int
    grouping: int
    reason_quality: int
    diversity: int
    critique: str


def _format_reason(reason) -> str:
    """Flatten reason (structured dict or plain string) into readable text."""
    if isinstance(reason, dict):
        lead = reason.get("lead", "")
        pts = "；".join(f"{p.get('term','')}:{p.get('detail','')}" for p in reason.get("points", []))
        return f"{lead}（{pts}）" if pts else lead
    return str(reason) if reason else "?"


def build_judge_prompt(career: str, result: dict) -> str:
    # Support both legacy {"groups": {...}} and current flat {"courses": [...]} pipeline format.
    groups = result.get("groups") or {}
    if not groups:
        courses = result.get("courses", [])
        groups = {
            "core": [c for c in courses if c.get("group") == "core"],
            "supporting": [c for c in courses if c.get("group") == "supporting"],
            "extended": [c for c in courses if c.get("group") == "extended"],
        }

    def format_group(name: str, courses: list[dict]) -> str:
        if not courses:
            return f"[{name}] （空）"
        lines = [f"[{name}]"]
        for c in courses:
            lines.append(f"  - {c.get('name', '?')} ({c.get('department', '?')}): {_format_reason(c.get('reason'))}")
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

critique 填 1-2 句整體評語，指出最大優點和最需改進之處。"""


def parse_judge_response(raw: str) -> dict | None:
    """Parse LLM judge JSON response. Returns None if malformed.

    Kept for backward compatibility (used by existing unit tests).
    """
    import json
    try:
        data = json.loads(raw.strip())
        required = {"relevance", "grouping", "reason_quality", "diversity", "critique"}
        if not required.issubset(data.keys()):
            return None

        r = _clamp(data["relevance"])
        g = _clamp(data["grouping"])
        rq = _clamp(data["reason_quality"])
        d = _clamp(data["diversity"])
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


def _scores_from_parsed(parsed: _JudgeOutput) -> dict:
    r = _clamp(parsed.relevance)
    g = _clamp(parsed.grouping)
    rq = _clamp(parsed.reason_quality)
    d = _clamp(parsed.diversity)
    overall = round((r + g + rq + d) / 4)
    return {
        "judge_relevance_score": r,
        "judge_grouping_score": g,
        "judge_reason_score": rq,
        "judge_diversity_score": d,
        "judge_overall_score": overall,
        "judge_critique": str(parsed.critique)[:500],
    }


async def evaluate_recommendation(
    client, career: str, result: dict
) -> dict | None:
    """Run LLM judge via OpenAI structured output. Returns parsed scores or None on failure."""
    try:
        prompt = build_judge_prompt(career, result)
        resp = await client.responses.parse(
            model=OPENAI_MODEL,
            input=[{"role": "user", "content": prompt}],
            text_format=_JudgeOutput,
        )
        parsed = resp.output_parsed
        if parsed is None:
            print("[judge] output_parsed is None (refusal/token-limit)")
            return None
        return _scores_from_parsed(parsed)
    except Exception as e:
        print(f"[judge] Evaluation failed: {e}")
        return None
