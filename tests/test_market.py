"""market.py 純函式測試：fetch_market_context 複用 Step 2 已下載的 SPY/產業 ETF 資料，
不重複下載（僅 ^VIX 一律單獨下載）。"""

import pandas as pd
import pytest

import market


def _make_df(base=100.0, n=60, end="2026-06-30"):
    idx = pd.bdate_range(end=end, periods=n)
    return pd.DataFrame({
        "Open":   [base] * n,
        "High":   [base + 1] * n,
        "Low":    [base - 1] * n,
        "Close":  [base + i * 0.01 for i in range(n)],
        "Volume": [1_000_000] * n,
    }, index=idx)


class TestFetchMarketContextReusesExistingData:
    def test_only_downloads_vix_when_spy_and_sectors_already_present(self, monkeypatch):
        """all_stocks_data 已含 SPY 與所有候選產業 ETF 時，yf.download 只應下載 ^VIX。"""
        all_stocks_data = {
            "SPY": _make_df(500.0),
            "XLK": _make_df(200.0),
        }
        captured = {}

        def fake_download(tickers, **kwargs):
            captured["tickers"] = tickers
            return _make_df(20.0, n=5)  # 模擬 ^VIX 單一 ticker 回傳（flat DataFrame）

        monkeypatch.setattr(market.yf, "download", fake_download)
        context = market.fetch_market_context(
            candidate_sectors={"Technology"},
            all_stocks_data=all_stocks_data,
            breadth_pct=55.0,
            vix_value=18.0,
        )

        assert captured["tickers"] == ["^VIX"]
        assert "sp500" in context
        assert "Technology" in context["sectors"]

    def _fake_multi_download(self, captured):
        def fake_download(tickers, **kwargs):
            captured["tickers"] = tickers
            if len(tickers) == 1:
                return _make_df(20.0, n=5)
            return pd.concat({t: _make_df(20.0, n=5) for t in tickers}, axis=1)
        return fake_download

    def test_downloads_only_missing_tickers_when_partially_present(self, monkeypatch):
        all_stocks_data = {"SPY": _make_df(500.0)}  # 缺 XLK
        captured = {}

        monkeypatch.setattr(market.yf, "download", self._fake_multi_download(captured))
        market.fetch_market_context(
            candidate_sectors={"Technology"},
            all_stocks_data=all_stocks_data,
            breadth_pct=55.0,
            vix_value=18.0,
        )

        assert set(captured["tickers"]) == {"^VIX", "XLK"}

    def test_downloads_everything_when_all_stocks_data_not_provided(self, monkeypatch):
        """未提供 all_stocks_data（如舊呼叫端）時，向下相容：一律下載 SPY/ETF/VIX。"""
        captured = {}

        monkeypatch.setattr(market.yf, "download", self._fake_multi_download(captured))
        market.fetch_market_context(candidate_sectors={"Technology"})

        assert set(captured["tickers"]) == {"^VIX", "SPY", "XLK"}
