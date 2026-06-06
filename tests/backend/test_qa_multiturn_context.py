# tests/backend/test_qa_multiturn_context.py
"""多輪追問脈絡化：根因是模型拿到歷史卻沒用歷史理解簡短追問（「那金融呢」→ 通用金融、脫離 PM 脈絡）。
修法把「延續上文脈絡化追問」寫進共用 system_instruction（stream + replay 兩模式共用）。

本檔是結構守門（regression guard）：確保該規則存在且 history 仍正確串進 contents。
真正的行為驗證走 live Playwright 多輪 e2e（需 Gemini 線上）。
"""
from backend.qa import _SYSTEM_INSTRUCTION, build_qa_contents


def test_system_instruction_mandates_followup_contextualization():
    """system_instruction 必須指示模型：簡短追問要結合先前對話脈絡再檢索，不可當獨立新問題。"""
    si = _SYSTEM_INSTRUCTION
    # 必含「追問 / 延續上文脈絡」的概念與具體例子（如「那…呢」），避免規則被誤刪
    assert "追問" in si, "system_instruction 缺『追問』脈絡化規則"
    assert "脈絡" in si, "system_instruction 缺『脈絡』延續指示"
    assert "呢" in si, "system_instruction 缺簡短追問的具體示例（如「那金融呢」）"
    # 逃生口：使用者真的換新主題時不可硬扯舊脈絡——守住這句避免被誤刪而過度錨定
    assert "全新主題" in si, "system_instruction 缺『換新主題就不延續』的逃生口"


def test_build_qa_contents_still_threads_history_before_current_turn():
    """脈絡化要能成立，前提是歷史仍被串進 contents（本輪問題在最後）。"""
    history = [{"question": "想成為 pm 要修什麼課", "answer": "PM 需要產品、數據、溝通等能力…"}]
    contents = build_qa_contents("那金融呢", history)
    # 第一段是上一輪 user 問題（原文，未被 template 包裝）
    assert contents[0]["role"] == "user"
    assert "pm" in contents[0]["parts"][0]["text"].lower()
    # 上一輪 model 回答接著
    assert contents[1]["role"] == "model"
    # 最後一段是本輪追問（含 template 包裝）
    assert contents[-1]["role"] == "user"
    assert "那金融呢" in contents[-1]["parts"][0]["text"]
