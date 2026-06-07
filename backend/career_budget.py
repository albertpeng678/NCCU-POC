"""career_budget：50 固定職涯的離線預算（整池推薦結果）讀寫。

離線一次性算好每個職涯的整池 courses（含 group/rank/reason），存進 Postgres；
線上命中→直接讀回秒出（跳過即時 fan-out + stage2 = 0 次 AI）。無 DB / 查無 → 回 None（線上降級回即時）。
"""
import json

# 線上回傳 Course 形狀的已知欄位（deserialize 時過濾未知欄位、向前相容）
_COURSE_KEYS = (
    "course_id", "name", "department", "teacher", "credits",
    "group", "rank", "syllabus_url", "reason",
)


def serialize_pool(courses: list) -> list:
    """扁平 courses → 可 JSON 化 payload（已是 dict，原樣淺拷貝）。"""
    return [dict(c) for c in (courses or [])]


def deserialize_pool(payload: list) -> list:
    """payload → 扁平 courses：只留已知欄位、缺 rank 補 0（課綱結構漂移也不會線上崩）。"""
    out = []
    for c in (payload or []):
        d = {k: c[k] for k in _COURSE_KEYS if k in c}
        d.setdefault("rank", 0)
        out.append(d)
    return out


async def get_budget(pool, career: str):
    """命中→{courses, pool_size, built_at}；無 pool / 查無 / DB 例外→None（觸發線上降級回即時）。"""
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT payload_json, pool_size, built_at FROM career_budget WHERE career=$1",
                career,
            )
        if not row:
            return None
        payload = row["payload_json"]
        if isinstance(payload, str):     # asyncpg 未註冊 JSONB codec 時取回為 str
            payload = json.loads(payload)
        return {
            "courses": deserialize_pool(payload),
            "pool_size": row["pool_size"],
            "built_at": row["built_at"],
        }
    except Exception as e:
        print(f"[career_budget] get_budget failed: {e}")
        return None


async def upsert_budget(pool, career: str, courses: list, model: str, seed: int) -> bool:
    """離線腳本用：冪等寫入（ON CONFLICT DO UPDATE）。無 pool→False。"""
    if pool is None:
        return False
    try:
        payload = json.dumps(serialize_pool(courses), ensure_ascii=False)
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO career_budget (career, payload_json, pool_size, model, seed, built_at) "
                "VALUES ($1, $2::jsonb, $3, $4, $5, NOW()) "
                "ON CONFLICT (career) DO UPDATE SET "
                "payload_json=$2::jsonb, pool_size=$3, model=$4, seed=$5, built_at=NOW()",
                career, payload, len(courses), model, seed,
            )
        return True
    except Exception as e:
        print(f"[career_budget] upsert_budget failed: {e}")
        return False
