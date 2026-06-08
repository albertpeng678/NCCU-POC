# tests/backend/test_qa_strip_markers.py
"""修 1：剝除 OpenAI 內文引用標記（U+E200 私有區 unicode）。

測試：
(a) 純函式 _strip_citation_markers：完整標記被剝乾淨（舊格式 + 官方格式）。
(b) 串流狀態機：標記跨兩個 delta 切割時，yield 出的文字不含標記。
"""
import pytest
from types import SimpleNamespace

from backend.qa import _strip_citation_markers, stream_answer

# 官方格式 helper chars
_CS = ""   # CITATION_START  (U+E200)
_CD = ""   # CITATION_DELIMITER  (U+E202)
_CE = ""   # CITATION_STOP  (U+E201)


# ─────────────────────────────── (a) 純函式 ────────────────────────────────


def test_strip_citation_markers_removes_full_marker():
    """完整 citation 標記（U+E200...U+E201）被完整剝除，周圍文字保留。"""
    # 建構一個完整標記（官方格式）：<CS>filecite<CD>turn0file0<CE>
    marker = f"{_CS}filecite{_CD}turn0file0{_CE}"
    text = f"助。{marker}\n- 下一行"
    result = _strip_citation_markers(text)
    assert result == "助。\n- 下一行"
    # 確認沒有任何私有區字元殘留
    for ch in result:
        assert not (0xE200 <= ord(ch) <= 0xE20F), f"殘留私有區字元: U+{ord(ch):04X}"


def test_strip_citation_markers_empty_string():
    """空字串不崩。"""
    assert _strip_citation_markers("") == ""


def test_strip_citation_markers_no_markers():
    """無標記文字原樣回傳。"""
    text = "這是正常文字，沒有標記。"
    assert _strip_citation_markers(text) == text


def test_strip_citation_markers_multiple_markers():
    """多個標記全部剝除。"""
    m1 = f"{_CS}filecite{_CD}turn0file0{_CE}"
    m2 = f"{_CS}filecite{_CD}turn1file2{_CE}"
    text = f"前{m1}中{m2}後"
    result = _strip_citation_markers(text)
    assert result == "前中後"


# ──────────── 官方格式 cases（修 A 新增） ────────────

def test_strip_official_filecite_format():
    """官方格式 filecite<CD>turn0file0 → 被完整剝除。"""
    marker = f"{_CS}filecite{_CD}turn0file0{_CE}"
    result = _strip_citation_markers(f"前{marker}後")
    assert result == "前後"
    for ch in result:
        assert not (0xE200 <= ord(ch) <= 0xE20F)


def test_strip_official_cite_with_line_locator():
    """cite body 帶 line locator（如 L8-L13）→ 整個標記被剝除，locator 不殘留。"""
    # 格式：<CS>cite<CD>turn0file0<CD>L8-L13<CE>
    marker = f"{_CS}cite{_CD}turn0file0{_CD}L8-L13{_CE}"
    result = _strip_citation_markers(f"前{marker}後")
    assert result == "前後"
    assert "L8" not in result
    for ch in result:
        assert not (0xE200 <= ord(ch) <= 0xE20F)


def test_strip_official_multi_source():
    """多來源 body（turn0search0、turn1news2）→ 整個標記被剝除。"""
    marker = f"{_CS}cite{_CD}turn0search0{_CD}turn1news2{_CE}"
    result = _strip_citation_markers(f"前{marker}後")
    assert result == "前後"
    for ch in result:
        assert not (0xE200 <= ord(ch) <= 0xE20F)


def test_strip_invalid_body_not_stripped_but_no_private_chars():
    """非法 body（含空格）→ 官方 helper 不視為合法 citation；
    殘留私有區字元清理保底確保控制字元不外洩給使用者。"""
    # 非法格式（body 含空格）
    marker = f"{_CS}filecite{_CD}xfoo bary{_CE}"
    result = _strip_citation_markers(f"前{marker}後")
    # 保底：無私有區字元外洩
    for ch in result:
        assert not (0xE200 <= ord(ch) <= 0xE20F), f"殘留私有區字元: U+{ord(ch):04X}"


# ─────────────────────────────── (b) 串流狀態機 ────────────────────────────


async def _aiter(items):
    for it in items:
        yield it


class _AsyncStreamCM:
    """Mirrors OpenAI AsyncStream: supports async with and async for."""

    def __init__(self, items):
        self._items = items

    def __aiter__(self):
        return _aiter(self._items).__aiter__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _text_delta(delta: str):
    return SimpleNamespace(delta=delta)


def _output_item_done(item_type="message"):
    item = SimpleNamespace(type=item_type)
    return SimpleNamespace(item=item)


def _fake_client(events):
    async def _create(**kwargs):
        return _AsyncStreamCM(events)
    return SimpleNamespace(responses=SimpleNamespace(create=_create))


async def _collect(gen):
    return [ev async for ev in gen]


@pytest.mark.asyncio
async def test_stream_strips_citation_marker_split_across_deltas():
    """標記跨兩個 delta（前半在 delta1、後半在 delta2）時，兩段都不可洩漏標記字元。

    delta1 = "助。<CS>filecite"    ← 標記開始在此 delta
    delta2 = "<CD>turn0file0<CE>next"  ← 標記結束在此 delta，next 是正常文字

    期望 yield 出的合併 token 文字 == "助。next"（標記整段不出現）。
    """
    open_char = _CS
    close_char = _CE
    sep_char = _CD

    delta1 = f"助。{open_char}filecite"
    delta2 = f"{sep_char}turn0file0{close_char}next"

    events = [
        _text_delta(delta1),
        _text_delta(delta2),
        _output_item_done("message"),
    ]
    client = _fake_client(events)
    result = await _collect(stream_answer(client, "vs_123", "問"))

    tokens = [e for e in result if e["event"] == "token"]
    joined = "".join(t["data"]["text"] for t in tokens)
    assert joined == "助。next", f"got: {repr(joined)}"

    # 確認 token 流中無任何私有區字元
    for ch in joined:
        assert not (0xE200 <= ord(ch) <= 0xE20F), f"token 流含私有區字元: U+{ord(ch):04X}"

    # done 的 answer_text 也應被剝乾淨
    done = [e for e in result if e["event"] == "done"][0]
    answer = done["data"]["answer_text"]
    for ch in answer:
        assert not (0xE200 <= ord(ch) <= 0xE20F), f"answer_text 含私有區字元: U+{ord(ch):04X}"
    assert "next" in answer
