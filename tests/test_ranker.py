"""ranker.py 純函式測試：_enrich_fallback() 產生的結果須標記 is_fallback=True（DD-18），
供 tracker.py 區分「AI 未產生有效判斷」與「AI 給出過低信心分數」。"""

import ranker


def test_enrich_fallback_tags_results_as_fallback():
    candidates = [{"symbol": "AAPL", "sector": "Technology", "total_score": 90.0}]
    result = ranker._enrich_fallback(candidates, info_data={}, price_data={})

    assert len(result) == 1
    assert result[0]["is_fallback"] is True
    assert result[0]["confidence"] == 5
