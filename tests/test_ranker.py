"""ranker.py 純函式測試：
- _enrich_fallback() 產生的結果須標記 is_fallback=True（DD-18），
  供 tracker.py 區分「AI 未產生有效判斷」與「AI 給出過低信心分數」。
- compute_indicators() 須回傳 atr14 美元值、候選池表格須含 ATR14 欄、
  SYSTEM_PROMPT 須含 ATR 錨定買進區間新規則（DD-19）。
"""

import pandas as pd

import ranker


def _make_df(base: float = 100.0, n: int = 60) -> pd.DataFrame:
    idx = pd.bdate_range(end="2026-06-30", periods=n)
    close = [base + i * 0.5 for i in range(n)]
    return pd.DataFrame({
        "Open":   close,
        "High":   [c + 1.0 for c in close],
        "Low":    [c - 1.0 for c in close],
        "Close":  close,
        "Volume": [1_000_000] * n,
    }, index=idx)


def test_enrich_fallback_tags_results_as_fallback():
    candidates = [{"symbol": "AAPL", "sector": "Technology", "total_score": 90.0}]
    result = ranker._enrich_fallback(candidates, info_data={}, price_data={})

    assert len(result) == 1
    assert result[0]["is_fallback"] is True
    assert result[0]["confidence"] == 5


# ── DD-19: ATR 錨定買進區間 ──────────────────────────────────────

def test_compute_indicators_returns_atr14_dollar_value():
    indic = ranker.compute_indicators("TEST", _make_df())
    assert indic["atr14"] is not None
    assert indic["atr14"] > 0


def test_compute_indicators_atr14_none_when_insufficient_history():
    indic = ranker.compute_indicators("TEST", _make_df(n=14))
    assert indic["atr14"] is None


def test_candidates_table_contains_atr14_column():
    candidates = [{"symbol": "TEST", "sector": "Technology", "total_score": 90.0}]
    table = ranker._generate_candidates_markdown_table(
        candidates, price_data={"TEST": _make_df()}, info_data={},
    )
    header = table.splitlines()[0]
    assert "| ATR14 |" in header
    # header 與資料列欄數一致（Markdown 表格對齊守門）
    data_row = table.splitlines()[2]
    assert header.count("|") == data_row.count("|")
    assert "$" in data_row.split("|")[header.split("|").index(" ATR14 ")]


def test_system_prompt_uses_atr_anchored_buy_zone_rules():
    assert "ATR14" in ranker.SYSTEM_PROMPT
    assert "淺回檔" in ranker.SYSTEM_PROMPT
    # 舊規則字樣須完全移除，防止新舊規則並存造成 Prompt 自相矛盾
    assert "距 EMA5 已超過 +5%" not in ranker.SYSTEM_PROMPT
    assert "5MA 探針帶" not in ranker.SYSTEM_PROMPT
