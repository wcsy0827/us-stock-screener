"""filter.py 純函式測試：財報防禦牆日期錨定於 market_date（非 datetime.now()）。"""

from datetime import date

import filter as filter_module


class TestApplyEarningsFilterDateAnchoring:
    def test_uses_provided_today_not_system_clock(self):
        """必須以呼叫端傳入的 today（market_date）為準，而非執行當下的系統時鐘。"""
        earnings_data = {"AAPL": date(2026, 7, 3)}
        # 系統時鐘可能是任何日期；這裡故意傳入一個與「現在」不同的 market_date，
        # 驗證排除邏輯確實以傳入值為準
        result = filter_module.apply_earnings_filter(
            ["AAPL"], earnings_data, days_ahead=5, today=date(2026, 6, 30),
        )
        assert result == []  # 7/3 財報落在 6/30~7/5 的排除窗口內

    def test_provided_today_outside_blackout_window_passes(self):
        earnings_data = {"AAPL": date(2026, 7, 20)}
        result = filter_module.apply_earnings_filter(
            ["AAPL"], earnings_data, days_ahead=5, today=date(2026, 6, 30),
        )
        assert result == ["AAPL"]

    def test_none_earnings_date_always_passes(self):
        result = filter_module.apply_earnings_filter(
            ["AAPL"], {"AAPL": None}, today=date(2026, 6, 30),
        )
        assert result == ["AAPL"]

    def test_missing_today_falls_back_to_system_clock(self, monkeypatch):
        """未提供 today 時 fallback 為 date.today()，向下相容未升級呼叫端。"""
        fixed_today = date(2026, 6, 30)

        class _FakeDate(date):
            @classmethod
            def today(cls):
                return fixed_today

        monkeypatch.setattr(filter_module, "date", _FakeDate)
        earnings_data = {"AAPL": date(2026, 7, 3)}
        result = filter_module.apply_earnings_filter(["AAPL"], earnings_data, days_ahead=5)
        assert result == []
