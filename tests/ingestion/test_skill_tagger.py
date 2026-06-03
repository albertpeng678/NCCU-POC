# tests/ingestion/test_skill_tagger.py
from ingestion.skill_tagger import format_skill_bridge, chunk_list

def test_format_skill_bridge_contains_required_sections():
    raw = "培養技能：產品管理、用戶研究\n適合職涯：PM\n關鍵詞：roadmap"
    bridge = format_skill_bridge(raw)
    assert "培養技能" in bridge
    assert "適合職涯" in bridge

def test_format_skill_bridge_with_empty_input_returns_fallback():
    bridge = format_skill_bridge("")
    assert isinstance(bridge, str)
    assert len(bridge) > 0

def test_chunk_list_splits_correctly():
    items = list(range(25))
    chunks = chunk_list(items, 10)
    assert len(chunks) == 3
    assert chunks[0] == list(range(10))
    assert chunks[1] == list(range(10, 20))
    assert chunks[2] == list(range(20, 25))

def test_chunk_list_exact_multiple():
    items = list(range(20))
    chunks = chunk_list(items, 10)
    assert len(chunks) == 2

def test_chunk_list_single_item():
    chunks = chunk_list([1], 10)
    assert chunks == [[1]]
