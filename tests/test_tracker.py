"""tracker.py 純函式單元測試。對應 specs/tracker.md 的 Acceptance Criteria。"""

import json

import pandas as pd
import pytest

import tracker


@pytest.fixture(autouse=True)
def isolate_data_dir(tmp_path, monkeypatch):
    """所有測試一律隔離至 tmp_path，避免誤寫 repo 的 data/watchlist.json。"""
    monkeypatch.setattr(tracker, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(tracker, "_WATCHLIST_PATH", tmp_path / "watchlist.json")
    monkeypatch.setattr(tracker, "_PERF_PATH", tmp_path / "performance_history.json")


# ── _parse_hold_period ──────────────────────────────────────────────

class TestParseHoldPeriod:
    def test_int_passthrough(self):
        assert tracker._parse_hold_period(10) == 10

    def test_float_truncated(self):
        assert tracker._parse_hold_period(10.9) == 10

    def test_plain_digit_string(self):
        assert tracker._parse_hold_period("10") == 10

    def test_extracts_max_number_from_text(self):
        assert tracker._parse_hold_period("1~2 週（約 10 天）") == 10

    @pytest.mark.parametrize("value", ["-", "", None])
    def test_unparseable_returns_default(self, value):
        assert tracker._parse_hold_period(value) == tracker._DEFAULT_HOLD_DAYS

    def test_custom_default(self):
        assert tracker._parse_hold_period("-", default=3) == 3

    def test_zero_clamped_to_minimum_one_dd19(self):
        """DD-19：hold_period<=0 的異常值必須夾在最小值 1，避免同日觸價成交
        （active_days 首輪即為 1）被誤判為 FORCE_EXPIRED。"""
        assert tracker._parse_hold_period(0) == 1

    def test_negative_int_clamped_to_minimum_one_dd19(self):
        assert tracker._parse_hold_period(-5) == 1

    def test_zero_string_clamped_to_minimum_one_dd19(self):
        assert tracker._parse_hold_period("0") == 1


# ── _count_trading_days ─────────────────────────────────────────────

class TestCountTradingDays:
    def test_same_day(self):
        assert tracker._count_trading_days("2024-01-01", "2024-01-01") == 0

    def test_monday_to_friday_excludes_end_date(self):
        # 2024-01-01 是週一，2024-01-05 是週五
        assert tracker._count_trading_days("2024-01-01", "2024-01-05") == 4

    def test_spans_weekend(self):
        # 2024-01-05（週五）到 2024-01-08（週一）之間只有週五算交易日
        assert tracker._count_trading_days("2024-01-05", "2024-01-08") == 1


# ── _parse_stop_loss / _parse_target ────────────────────────────────

class TestParseStopLoss:
    @pytest.mark.parametrize("raw, expected", [
        ("$182.50", 182.5),
        ("182", 182.0),
        ("$1,234.56", 1234.56),
    ])
    def test_valid(self, raw, expected):
        assert tracker._parse_stop_loss(raw) == expected

    @pytest.mark.parametrize("raw", ["-", "", None, "N/A"])
    def test_invalid_returns_none(self, raw):
        assert tracker._parse_stop_loss(raw) is None

    def test_parse_target_is_alias(self):
        assert tracker._parse_target("$210") == 210.0


# ── _parse_buy_zone ──────────────────────────────────────────────────

class TestParseBuyZone:
    def test_valid_range(self):
        assert tracker._parse_buy_zone("$185～$188") == (185.0, 188.0)

    def test_reversed_range_gets_sorted(self):
        assert tracker._parse_buy_zone("$188~$185") == (185.0, 188.0)

    @pytest.mark.parametrize("raw", ["-", "", None, "185"])
    def test_invalid_returns_none(self, raw):
        assert tracker._parse_buy_zone(raw) is None


# ── _calc_split_factor（DD-3 拆股免疫）──────────────────────────────

class TestCalcSplitFactor:
    def _series(self, dates, values):
        return pd.Series(values, index=pd.to_datetime(dates))

    def test_no_split_factor_is_one(self):
        series = self._series(["2024-01-01", "2024-01-02"], [100.0, 101.0])
        factor = tracker._calc_split_factor("2024-01-01", 100.0, series)
        assert factor == pytest.approx(1.0)

    def test_two_for_one_split_halves_factor(self):
        # 拆股前記錄 signal_date_close=300；yfinance 回溯調整後同一天顯示 150（2:1 拆股）
        series = self._series(["2024-01-01", "2024-01-02"], [150.0, 151.0])
        factor = tracker._calc_split_factor("2024-01-01", 300.0, series)
        assert factor == pytest.approx(0.5)

    def test_missing_signal_data_returns_one(self):
        series = self._series(["2024-01-01"], [100.0])
        assert tracker._calc_split_factor("", 100.0, series) == 1.0
        assert tracker._calc_split_factor("2024-01-01", 0, series) == 1.0

    def test_signal_date_before_all_history_returns_one(self):
        series = self._series(["2024-02-01"], [100.0])
        factor = tracker._calc_split_factor("2024-01-01", 100.0, series)
        assert factor == 1.0


# ── _fetch_latest：High/Low 與 Close 同列對齊（DD-19 前置修正）───────

class TestFetchLatestRowAlignment:
    def test_high_low_aligned_with_valid_close_row_not_incomplete_last_row(self, monkeypatch):
        """當最後一列 Close 為 NaN（殘缺列）時，High/Low 必須與 dropna 後的
        有效 Close 取自同一列，不得誤用殘缺列的 High/Low（會破壞
        today_low <= price <= today_high 恆等式，DD-19 的觸價判定依賴此式）。"""
        idx = pd.to_datetime(["2026-06-29", "2026-06-30"])
        df = pd.DataFrame({
            "Open":   [100.0, 105.0],
            "High":   [102.0, 999.0],   # 殘缺列的異常值，不應被誤用
            "Low":    [98.0,  0.1],     # 殘缺列的異常值，不應被誤用
            "Close":  [100.0, float("nan")],
            "Volume": [1000, 500],
        }, index=idx)

        monkeypatch.setattr(tracker.yf, "download", lambda **kwargs: df)
        result = tracker._fetch_latest(["TEST"])

        assert result["TEST"]["price"] == 100.0
        assert result["TEST"]["today_high"] == 102.0
        assert result["TEST"]["today_low"] == 98.0

    def test_high_low_match_close_row_when_no_missing_data(self, monkeypatch):
        idx = pd.to_datetime(["2026-06-29", "2026-06-30"])
        df = pd.DataFrame({
            "Open":   [100.0, 103.0],
            "High":   [102.0, 106.0],
            "Low":    [98.0,  101.0],
            "Close":  [100.0, 105.0],
            "Volume": [1000, 1200],
        }, index=idx)

        monkeypatch.setattr(tracker.yf, "download", lambda **kwargs: df)
        result = tracker._fetch_latest(["TEST"])

        assert result["TEST"]["price"] == 105.0
        assert result["TEST"]["today_high"] == 106.0
        assert result["TEST"]["today_low"] == 101.0


# ── _eval_status ─────────────────────────────────────────────────────

def _watch_entry(**overrides) -> dict:
    base = {
        "status": "watch",
        "buy_zone_lower": 100.0,
        "buy_zone_upper": 105.0,
        "stop_loss": "$95.00",
        "strategy": "突破策略",
    }
    base.update(overrides)
    return base


class TestEvalStatus:
    def test_already_invalid_stays_invalid(self):
        entry = _watch_entry(status="invalid", invalid_reason="舊原因")
        status, reason = tracker._eval_status(entry, price=150, ema20=100)
        assert (status, reason) == ("invalid", "舊原因")

    def test_reversal_strategy_invalid_below_stop_loss(self):
        entry = _watch_entry(strategy="反轉策略", stop_loss="$90.00")
        status, reason = tracker._eval_status(entry, price=89, ema20=200, ema50=200)
        assert status == "invalid"
        assert "反轉訊號失效" in reason

    def test_ema50_paradox_reversal_not_invalidated_by_ema(self):
        """DD-1：反轉股進場點本就在 EMA50 下方，不能用 EMA50 判失效。"""
        entry = _watch_entry(strategy="反轉策略", stop_loss="$90.00",
                              buy_zone_lower=100.0, buy_zone_upper=105.0)
        # price 遠低於 ema50，但仍高於 stop_loss，且落在買入區間 → active
        status, reason = tracker._eval_status(entry, price=102, ema20=None, ema50=200)
        assert status == "active"

    def test_momentum_strategy_invalid_below_ema20(self):
        entry = _watch_entry(strategy="動能策略")
        status, reason = tracker._eval_status(entry, price=98, ema20=99)
        assert status == "invalid"
        assert "趨勢轉弱" in reason

    def test_chase_high_invalid_when_not_active(self):
        entry = _watch_entry(status="watch")
        status, reason = tracker._eval_status(entry, price=105 * 1.09, ema20=50)
        assert status == "invalid"
        assert "已追高" in reason

    def test_chase_high_exempt_when_already_active(self):
        entry = _watch_entry(status="active")
        status, _ = tracker._eval_status(entry, price=105 * 1.09, ema20=50)
        assert status != "invalid"

    def test_active_short_circuits_regardless_of_price_or_ema_dd17(self):
        """DD-17：active 部位不再被 _eval_status 判定失效，一律回傳 active，
        生命週期完全交給 _check_settlement。"""
        entry = _watch_entry(status="active", strategy="反轉策略", stop_loss="$90.00")
        # 收盤已跌破止損（若照舊邏輯會被判 invalid），active 短路後仍回傳 active
        status, reason = tracker._eval_status(entry, price=85, ema20=None, ema50=None)
        assert (status, reason) == ("active", None)

    def test_active_momentum_below_ema20_still_short_circuits_dd17(self):
        entry = _watch_entry(status="active", strategy="動能策略")
        status, reason = tracker._eval_status(entry, price=90, ema20=200)
        assert (status, reason) == ("active", None)

    def test_price_above_upper_band_returns_watch(self):
        entry = _watch_entry()
        status, reason = tracker._eval_status(entry, price=105 * 1.02, ema20=50)
        assert (status, reason) == ("watch", None)

    def test_price_in_buy_zone_returns_active(self):
        entry = _watch_entry()
        status, reason = tracker._eval_status(entry, price=102, ema20=50)
        assert (status, reason) == ("active", None)

    def test_gap_down_safety_block_dd7(self):
        """DD-7：跳空進場時價格已在買入區間但跌破止損 → 拒絕進場。"""
        entry = _watch_entry(buy_zone_lower=100.0, buy_zone_upper=105.0, stop_loss="$101.00")
        status, reason = tracker._eval_status(entry, price=100.5, ema20=50)
        assert status == "invalid"
        assert "拒絕進場" in reason

    def test_below_lower_but_above_stop_loss_stays_watch(self):
        entry = _watch_entry(stop_loss="$90.00")
        status, reason = tracker._eval_status(entry, price=95, ema20=50)
        assert (status, reason) == ("watch", None)

    def test_below_lower_and_below_stop_loss_is_invalid(self):
        entry = _watch_entry(stop_loss="$90.00")
        status, reason = tracker._eval_status(entry, price=85, ema20=50)
        assert status == "invalid"
        assert "錯過買點" in reason


# ── _eval_status：DD-19 盤中限價單模擬進場（today_low 觸價優先） ──────

class TestEvalStatusIntradayTouchDD19:
    def test_touch_triggers_active_regardless_of_close_price(self):
        """today_low <= buy_zone_upper 即視為觸價成交，不論收盤價落在何處。"""
        entry = _watch_entry(buy_zone_upper=105.0)
        status, reason = tracker._eval_status(entry, price=130, ema20=50, today_low=104.0)
        assert (status, reason) == ("active", None)

    def test_touch_takes_priority_over_reversal_stop_invalidation(self):
        entry = _watch_entry(strategy="反轉策略", stop_loss="$90.00", buy_zone_upper=105.0)
        # 收盤已跌破止損（若照舊邏輯會被判 invalid），但今日曾觸及區間上緣 → 仍視為成交
        status, reason = tracker._eval_status(entry, price=88, ema20=None, ema50=None, today_low=85.0)
        assert (status, reason) == ("active", None)

    def test_touch_takes_priority_over_momentum_ema20_invalidation(self):
        entry = _watch_entry(strategy="動能策略", buy_zone_upper=105.0)
        status, reason = tracker._eval_status(entry, price=97, ema20=99, today_low=95.0)
        assert (status, reason) == ("active", None)

    def test_touch_takes_priority_over_chase_high(self):
        """跳空暴漲穿越整個買入區間：今日曾觸及上緣（限價單真實成交），
        即使收盤遠高於追高門檻，仍視為已成交，而非「已追高，錯過買點」。"""
        entry = _watch_entry(buy_zone_upper=100.0)
        status, reason = tracker._eval_status(entry, price=150, ema20=50, today_low=99.0)
        assert (status, reason) == ("active", None)

    def test_invalid_status_immune_to_intraday_touch(self):
        """鎖定順序：觸價檢查嚴格位於 invalid 短路之後，不得追溯復活既有 invalid 條目。"""
        entry = _watch_entry(status="invalid", invalid_reason="舊原因", buy_zone_upper=105.0)
        status, reason = tracker._eval_status(entry, price=50, ema20=None, today_low=50.0)
        assert (status, reason) == ("invalid", "舊原因")

    def test_active_status_short_circuits_before_touch_check(self):
        entry = _watch_entry(status="active", buy_zone_upper=105.0)
        status, reason = tracker._eval_status(entry, price=200, ema20=None, today_low=500.0)
        assert (status, reason) == ("active", None)

    def test_no_touch_falls_back_to_original_close_based_logic(self):
        """今日未觸價（today_low > upper）時，完全退化為原本收盤價判定，行為不變。"""
        entry = _watch_entry(buy_zone_upper=100.0)
        status, reason = tracker._eval_status(entry, price=100 * 1.09, ema20=50, today_low=100 * 1.05)
        assert status == "invalid"
        assert "已追高" in reason

    def test_today_low_none_falls_back_to_original_close_based_logic(self):
        """未提供 today_low（如既有呼叫端未升級）時，完全不影響既有行為，
        向下相容舊版逐字元一致。"""
        entry = _watch_entry()
        status, reason = tracker._eval_status(entry, price=102, ema20=50)
        assert (status, reason) == ("active", None)


# ── _check_settlement ────────────────────────────────────────────────

def _active_entry(**overrides) -> dict:
    base = {
        "status": "active",
        "strategy": "突破策略",
        "target": "$120.00",
        "stop_loss": "$90.00",
        "effective_stop_loss": 90.0,
        "hold_period": "10",
        "active_days": 1,
        "active_entry_price": 100.0,
        "highest_close_since_active": 100.0,
    }
    base.update(overrides)
    return base


class TestCheckSettlement:
    def test_not_active_returns_none(self):
        entry = _active_entry(status="watch")
        assert tracker._check_settlement(entry, price=100, today_high=130, today_low=80) is None

    def test_closed_profit_on_intraday_high(self):
        entry = _active_entry()
        result = tracker._check_settlement(entry, price=115, today_high=121, today_low=110)
        assert result == (tracker.EXIT_PROFIT, 120.0)

    def test_closed_loss_on_intraday_low(self):
        entry = _active_entry()
        result = tracker._check_settlement(entry, price=95, today_high=100, today_low=89)
        assert result == (tracker.EXIT_LOSS, 90.0)

    def test_black_swan_same_day_both_triggered_is_loss(self):
        entry = _active_entry()
        result = tracker._check_settlement(entry, price=100, today_high=125, today_low=85)
        assert result == (tracker.EXIT_LOSS, 90.0)

    def test_trailing_stop_triggers_for_momentum_strategy(self):
        entry = _active_entry(strategy="突破策略", active_entry_price=100.0,
                               highest_close_since_active=115.0, target="$200")
        # 峰值浮盈 15% (>10%)，從峰值回撤到 109（回撤 5.2% ≥ 5%）
        result = tracker._check_settlement(entry, price=109, today_high=109, today_low=108)
        assert result == (tracker.EXIT_TRAILING, 109)

    def test_trailing_stop_excluded_for_reversal_strategy(self):
        """DD-13：反轉策略精確排除移動停利。"""
        entry = _active_entry(strategy="反轉策略", active_entry_price=100.0,
                               highest_close_since_active=115.0, target="$200",
                               hold_period="999")
        result = tracker._check_settlement(entry, price=109, today_high=109, today_low=108)
        assert result is None

    def test_force_expired_on_hold_period_reached(self):
        """反轉策略不受 DD-21 延伸白名單覆蓋，到期一律強制出場，作為通用到期測試。"""
        entry = _active_entry(strategy="反轉策略", hold_period="5", active_days=5, target="$200")
        result = tracker._check_settlement(entry, price=101, today_high=102, today_low=100)
        assert result == (tracker.EXIT_EXPIRED, 101)

    def test_no_trigger_returns_none(self):
        entry = _active_entry(hold_period="10", active_days=1, target="$200")
        result = tracker._check_settlement(entry, price=101, today_high=102, today_low=100)
        assert result is None


# ── _check_settlement：到期趨勢延伸（DD-21）──────────────────────────

class TestCheckSettlementExpiryExtension:
    def test_momentum_at_expiry_close_to_peak_grants_extension(self):
        """案例 1：動能策略到期日、收盤回撤 <3% → None（獲延長）。
        此案例在舊碼（DD-21 之前）必紅：舊碼在 active_days>=hold_limit 時
        無條件回傳 FORCE_EXPIRED，不看回撤幅度。"""
        entry = _active_entry(strategy="動能策略", hold_period="10", active_days=10,
                               highest_close_since_active=100.0, target="$200")
        result = tracker._check_settlement(entry, price=98.5, today_high=99, today_low=97)
        assert result is None

    def test_momentum_at_expiry_retraced_3pct_expires(self):
        """案例 2：動能策略到期日、收盤回撤 ≥3% → (FORCE_EXPIRED, close)。"""
        entry = _active_entry(strategy="動能策略", hold_period="10", active_days=10,
                               highest_close_since_active=100.0, target="$200")
        result = tracker._check_settlement(entry, price=97, today_high=98, today_low=96)
        assert result == (tracker.EXIT_EXPIRED, 97)

    def test_reversal_strategy_never_extends(self):
        """案例 3：反轉策略到期 → 一律 FORCE_EXPIRED，即使回撤 0%（貼峰值）。"""
        entry = _active_entry(strategy="反轉策略", hold_period="10", active_days=10,
                               highest_close_since_active=100.0, target="$200")
        result = tracker._check_settlement(entry, price=100.0, today_high=100.0, today_low=99)
        assert result == (tracker.EXIT_EXPIRED, 100.0)

    def test_unknown_strategy_never_extends(self):
        """案例 4：strategy 為 "-" 或空字串到期 → 一律 FORCE_EXPIRED（白名單制）。"""
        for bad_strategy in ("-", ""):
            entry = _active_entry(strategy=bad_strategy, hold_period="10", active_days=10,
                                   highest_close_since_active=100.0, target="$200")
            result = tracker._check_settlement(entry, price=100.0, today_high=100.0, today_low=99)
            assert result == (tracker.EXIT_EXPIRED, 100.0)

    def test_extension_hard_cap_forces_expiry(self):
        """案例 5：延長期間 active_days >= hold_limit + 10 → FORCE_EXPIRED，即使回撤 0%。"""
        entry = _active_entry(strategy="動能策略", hold_period="10", active_days=20,
                               highest_close_since_active=100.0, target="$200")
        result = tracker._check_settlement(entry, price=100.0, today_high=100.0, today_low=99)
        assert result == (tracker.EXIT_EXPIRED, 100.0)

    def test_extension_period_stop_loss_still_triggers_first(self):
        """案例 6：延長期間盤中 today_low ≤ effective_stop_loss → 仍觸發 CLOSED_LOSS（優先序 2 先行）。"""
        entry = _active_entry(strategy="動能策略", hold_period="10", active_days=12,
                               effective_stop_loss=90.0, highest_close_since_active=100.0,
                               target="$200")
        result = tracker._check_settlement(entry, price=95, today_high=96, today_low=89)
        assert result == (tracker.EXIT_LOSS, 90.0)

    def test_extension_period_target_still_triggers_first(self):
        """案例 7：延長期間 today_high ≥ target → 仍觸發 CLOSED_PROFIT（優先序 3 先行）。"""
        entry = _active_entry(strategy="動能策略", hold_period="10", active_days=12,
                               highest_close_since_active=100.0, target="$120.00")
        result = tracker._check_settlement(entry, price=115, today_high=121, today_low=110)
        assert result == (tracker.EXIT_PROFIT, 120.0)

    def test_missing_highest_close_fails_safe_to_expired(self):
        """案例 8：highest_close_since_active 缺失（None/0）且到期 → FORCE_EXPIRED（fail-safe）。"""
        for missing in (None, 0):
            entry = _active_entry(strategy="動能策略", hold_period="10", active_days=10,
                                   highest_close_since_active=missing, target="$200")
            result = tracker._check_settlement(entry, price=100.0, today_high=100.0, today_low=99)
            assert result == (tracker.EXIT_EXPIRED, 100.0)

    def test_breakout_strategy_at_expiry_close_to_peak_grants_extension(self):
        """案例 9：突破策略到期回撤 <3% → None（白名單含突破）。"""
        entry = _active_entry(strategy="突破策略", hold_period="10", active_days=10,
                               highest_close_since_active=100.0, target="$200")
        result = tracker._check_settlement(entry, price=99, today_high=99.5, today_low=98)
        assert result is None


# ── _apply_risk_controls（DD-12、DD-13）─────────────────────────────

class TestApplyRiskControls:
    def _adj_and_original(self, **overrides):
        adj = {
            "symbol": "TEST",
            "status": "active",
            "active_entry_price": 100.0,
            "target": "$120.00",
            "effective_stop_loss": 90.0,
            "buy_zone_upper": 105.0,
            "highest_close_since_active": 100.0,
        }
        adj.update(overrides)
        original = dict(adj)
        return adj, original

    def test_non_active_is_noop(self):
        adj, original = self._adj_and_original(status="watch")
        tracker._apply_risk_controls(adj, price=115, split_factor=1.0, original_entry=original)
        assert adj["effective_stop_loss"] == 90.0

    def test_breakeven_lock_triggers_at_50pct_of_target_distance(self):
        # entry=100, target=120 → 50% 距離 = 110
        adj, original = self._adj_and_original()
        tracker._apply_risk_controls(adj, price=110, split_factor=1.0, original_entry=original)
        assert adj["is_breakeven_locked"] is True
        assert adj["effective_stop_loss"] == 105.0
        assert original["effective_stop_loss"] == 105.0

    def test_breakeven_lock_does_not_refire_once_locked(self):
        adj, original = self._adj_and_original(is_breakeven_locked=True, effective_stop_loss=105.0)
        original["is_breakeven_locked"] = True
        original["effective_stop_loss"] = 105.0
        tracker._apply_risk_controls(adj, price=118, split_factor=1.0, original_entry=original)
        assert adj["effective_stop_loss"] == 105.0

    def test_highest_close_updates_on_new_high(self):
        adj, original = self._adj_and_original()
        tracker._apply_risk_controls(adj, price=108, split_factor=1.0, original_entry=original)
        assert original["highest_close_since_active"] == 108.0
        assert adj["highest_close_since_active"] == 108.0

    def test_highest_close_not_overwritten_when_not_new_high(self):
        adj, original = self._adj_and_original(highest_close_since_active=120.0)
        original["highest_close_since_active"] = 120.0
        tracker._apply_risk_controls(adj, price=108, split_factor=1.0, original_entry=original)
        assert original["highest_close_since_active"] == 120.0


# ── _max_watch_days / _is_expired（DD-15、DD-16）────────────────────

class TestMaxWatchDays:
    @pytest.mark.parametrize("entry, expected", [
        ({"strategy": "突破策略", "entry_regime": "CONSOLIDATION_VOLATILE"}, 3),
        ({"strategy": "突破策略", "entry_regime": "BULL_TREND"}, 5),
        ({"strategy": "動能策略", "entry_regime": "BULL_TREND"}, 5),
        ({"strategy": "反轉策略", "entry_regime": "PANIC_REVERSAL", "vix_value": 36}, 5),
        ({"strategy": "反轉策略", "entry_regime": "PANIC_REVERSAL", "vix_value": 28}, 10),
        ({"strategy": "反轉策略", "entry_regime": "PANIC_REVERSAL", "vix_value": None}, 10),
        ({"strategy": "反轉策略", "entry_regime": "BEAR_DISTRIBUTION", "vix_value": 40}, 10),
        ({"strategy": "未知策略"}, 5),
    ])
    def test_max_watch_days(self, entry, expected):
        assert tracker._max_watch_days(entry) == expected


class TestIsExpired:
    def test_active_never_expires_regardless_of_days(self):
        entry = {"status": "active", "strategy": "突破策略", "tracked_dates": ["d"] * 999}
        assert tracker._is_expired(entry) is False

    def test_watch_expires_at_default_limit(self):
        entry = {"status": "watch", "strategy": "突破策略",
                 "tracked_dates": ["d"] * tracker._DEFAULT_WATCH_DAYS}
        assert tracker._is_expired(entry) is True

    def test_watch_not_yet_expired_below_limit(self):
        entry = {"status": "watch", "strategy": "突破策略",
                 "tracked_dates": ["d"] * (tracker._DEFAULT_WATCH_DAYS - 1)}
        assert tracker._is_expired(entry) is False

    def test_dd16_breakout_consolidation_volatile_expires_at_3(self):
        entry = {"status": "watch", "strategy": "突破策略",
                 "entry_regime": "CONSOLIDATION_VOLATILE", "tracked_dates": ["d"] * 3}
        assert tracker._is_expired(entry) is True

    def test_dd16_reversal_vix_spike_expires_at_5(self):
        entry = {"status": "watch", "strategy": "反轉策略", "entry_regime": "PANIC_REVERSAL",
                 "vix_value": 36, "tracked_dates": ["d"] * 5}
        assert tracker._is_expired(entry) is True

    def test_dd16_reversal_vix_edge_still_uses_10_day_limit(self):
        entry = {"status": "watch", "strategy": "反轉策略", "entry_regime": "PANIC_REVERSAL",
                 "vix_value": 28, "tracked_dates": ["d"] * 9}
        assert tracker._is_expired(entry) is False
        entry["tracked_dates"] = ["d"] * 10
        assert tracker._is_expired(entry) is True


# ── _archive_to_performance_history ─────────────────────────────────

class TestArchiveToPerformanceHistory:
    def _entry(self, **overrides):
        base = {
            "symbol": "TEST",
            "name": "Test Corp",
            "sector": "Technology",
            "date_added": "2024-01-01",
            "active_start_date": "2024-01-02",
            "active_entry_price": 100.0,
            "active_days": 5,
            "entry_regime": "BULL_TREND",
            "market_breadth_pct": 65.0,
            "vix_value": 15.0,
            "l2_score": 80,
            "strategy": "突破策略",
            "ai_confidence": 8,
            "ai_strategy_reason": "測試理由",
            "buy_zone_lower": 98.0,
            "buy_zone_upper": 102.0,
            "target": "$120.00",
            "planned_stop_loss": 90.0,
        }
        base.update(overrides)
        return base

    def _read_records(self):
        with open(tracker._PERF_PATH, "r", encoding="utf-8") as f:
            return json.load(f)["history_records"]

    def test_writes_new_record_with_correct_return_pct_and_win_flag(self):
        entry = self._entry()
        tracker._archive_to_performance_history(entry, tracker.EXIT_PROFIT, 120.0, "2024-01-10")
        records = self._read_records()
        assert len(records) == 1
        rec = records[0]
        assert rec["performance_metrics"]["return_pct"] == 20.0
        assert rec["performance_metrics"]["is_win"] is True
        assert rec["signal_details"]["entry_regime"] == "BULL_TREND"
        assert rec["actual_outcome"]["exit_reason"] == tracker.EXIT_PROFIT

    def test_appends_rather_than_overwrites(self):
        entry_a = self._entry(symbol="AAA")
        entry_b = self._entry(symbol="BBB")
        tracker._archive_to_performance_history(entry_a, tracker.EXIT_PROFIT, 120.0, "2024-01-10")
        tracker._archive_to_performance_history(entry_b, tracker.EXIT_LOSS, 90.0, "2024-01-11")
        records = self._read_records()
        assert [r["meta_data"]["ticker"] for r in records] == ["AAA", "BBB"]

    def test_holding_days_prefers_active_days_counter(self):
        """DD-8：holding_days 以 active_days 為主，不用日曆天差。"""
        entry = self._entry(active_days=7, active_start_date="2024-01-01")
        tracker._archive_to_performance_history(entry, tracker.EXIT_EXPIRED, 100.0, "2024-03-01")
        rec = self._read_records()[0]
        assert rec["actual_outcome"]["holding_days"] == 7

    def test_holding_days_falls_back_to_trading_day_count_when_missing(self):
        entry = self._entry(active_start_date="2024-01-01")
        del entry["active_days"]
        tracker._archive_to_performance_history(entry, tracker.EXIT_EXPIRED, 100.0, "2024-01-05")
        rec = self._read_records()[0]
        assert rec["actual_outcome"]["holding_days"] == 4

    def test_loss_produces_negative_return_and_is_win_false(self):
        entry = self._entry(active_entry_price=100.0)
        tracker._archive_to_performance_history(entry, tracker.EXIT_LOSS, 90.0, "2024-01-10")
        rec = self._read_records()[0]
        assert rec["performance_metrics"]["return_pct"] == -10.0
        assert rec["performance_metrics"]["is_win"] is False


# ── run_tracker：B/C 新訊號處理（DD-14 信心過濾等）────────────────
# _fetch_latest([]) 對空 existing_symbols 直接回傳 {}，不需連網即可測 B/C 邏輯。

class TestRunTrackerNewSignals:
    def _stock(self, **overrides):
        base = {
            "symbol": "NEW1",
            "name": "New Co",
            "sector": "Technology",
            "buy_zone": "$100～$105",
            "target": "$120",
            "stop_loss": "$90",
            "hold_period": "10",
            "strategy": "突破策略",
            "confidence": 8,
            "total_score": 80,
            "strategy_reason": "reason",
        }
        base.update(overrides)
        return base

    def test_low_confidence_signal_skipped(self):
        stock = self._stock(confidence=5)
        watchlist, categories = tracker.run_tracker([stock], market_date="2024-01-01")
        assert categories["new"] == []
        assert watchlist == []

    def test_confidence_at_threshold_is_accepted(self):
        stock = self._stock(confidence=tracker.MIN_AI_CONFIDENCE)
        watchlist, categories = tracker.run_tracker([stock], market_date="2024-01-01")
        assert len(categories["new"]) == 1
        assert watchlist[0]["symbol"] == "NEW1"

    def test_unparseable_buy_zone_skipped(self):
        stock = self._stock(buy_zone="-")
        watchlist, categories = tracker.run_tracker([stock], market_date="2024-01-01")
        assert watchlist == []

    def test_fallback_result_skipped_via_distinct_path_not_confidence_gate(self):
        """DD-20：is_fallback=True（AI 排序失敗的 L2 分數退化輸出）一律跳過，
        且不得與「信心分數不足」混為一談——即使 confidence 剛好達標也要擋下，
        因為 5 分是寫死的佔位值，不是 AI 真實判斷。"""
        stock = self._stock(is_fallback=True, confidence=tracker.MIN_AI_CONFIDENCE)
        watchlist, categories = tracker.run_tracker([stock], market_date="2024-01-01")
        assert categories["new"] == []
        assert watchlist == []

    def test_new_signal_captures_regime_and_vix_snapshot(self):
        stock = self._stock()
        market_context = {"regime": "CONSOLIDATION_VOLATILE",
                           "market_breadth_pct": 45.0,
                           "vix": {"value": 22.5}}
        watchlist, _ = tracker.run_tracker([stock], market_context=market_context,
                                            market_date="2024-01-01")
        assert watchlist[0]["entry_regime"] == "CONSOLIDATION_VOLATILE"
        assert watchlist[0]["vix_value"] == 22.5

    def test_1day_lag_new_signal_not_evaluated_same_round(self):
        """DD-11：新選股當輪不評估，狀態維持 watch，不會當天就變成 active。"""
        stock = self._stock()
        watchlist, categories = tracker.run_tracker([stock], market_date="2024-01-01")
        assert watchlist[0]["status"] == "watch"
        assert watchlist[0]["active_entry_price"] is None
        assert categories["active"] == []

    def test_signal_date_close_written_immediately_on_creation_dd17(self):
        """DD-17：新條目建立當下就寫入訊號日收盤價，不留 None 等下一輪回填。"""
        stock = self._stock(price=102.5)
        watchlist, _ = tracker.run_tracker([stock], market_date="2024-01-01")
        assert watchlist[0]["signal_date_close"] == 102.5

    def test_signal_date_close_refreshed_on_reset_path_dd17(self, monkeypatch):
        """DD-17：watch 個股被新訊號覆寫展期（reset）時，signal_date_close
        必須跟著更新為本輪訊號價，不能被清成 None（否則延續舊 bug 的次日回填錯位）。"""
        existing = _watch_entry(
            status="watch", symbol="NEW1", buy_zone_lower=100.0, buy_zone_upper=105.0,
        )
        existing.update({
            "tracked_dates": ["2023-12-20"], "watch_days": 3, "date_added": "2023-12-20",
            "signal_date_close": 88.0, "active_entry_price": None,
        })
        tracker.save_watchlist([existing])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {})

        stock = self._stock(price=95.0)
        watchlist, categories = tracker.run_tracker([stock], market_date="2024-01-01")
        assert len(categories["reset"]) == 1
        assert watchlist[0]["signal_date_close"] == 95.0
        assert watchlist[0]["tracked_dates"] == ["2024-01-01"]


# ── run_tracker：DD-17 active 部位結算/失效修復 ─────────────────────

class TestRunTrackerActiveSettlementDD17:
    def _active_watchlist_entry(self, **overrides) -> dict:
        entry = {
            "symbol": "TEST", "name": "Test Corp", "sector": "Technology",
            "buy_zone": "$100.00～$105.00", "buy_zone_lower": 100.0, "buy_zone_upper": 105.0,
            "target": "$130.00", "stop_loss": "$90.00", "hold_period": "10",
            "strategy": "動能策略",
            "tracked_dates": ["2026-06-25", "2026-06-26", "2026-06-29"],
            "status": "active", "invalid_reason": None,
            "watch_days": 1, "active_days": 3,
            "signal_date_close": 100.0,
            "active_entry_price": 100.0, "active_start_date": "2026-06-26",
            "date_added": "2026-06-25",
            "entry_regime": "BULL_TREND", "market_breadth_pct": 65.0, "vix_value": 15.0,
            "l2_score": 80, "ai_confidence": 8, "ai_strategy_reason": "",
            "planned_stop_loss": 90.0, "effective_stop_loss": 90.0,
            "is_breakeven_locked": False, "highest_close_since_active": 100.0,
        }
        entry.update(overrides)
        return entry

    def _flat_series(self, value=100.0, end="2026-06-30", n=60):
        idx = pd.bdate_range(end=end, periods=n)
        return pd.Series([value] * n, index=idx)

    def test_reversal_active_real_stop_breach_settles_as_closed_loss(self, monkeypatch):
        """DD-17：反轉策略 active 部位實質跌破止損（today_low<=effective_stop_loss）
        必須經 _check_settlement 結算為 CLOSED_LOSS 並歸檔，不能只落 invalid 就消失。"""
        entry = self._active_watchlist_entry(strategy="反轉策略", stop_loss="$98.00",
                                              planned_stop_loss=98.0, effective_stop_loss=98.0)
        tracker.save_watchlist([entry])

        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            "TEST": {"price": 97.0, "today_high": 99.0, "today_low": 96.5,
                     "ema20": 100.0, "ema50": 100.0, "close_series": self._flat_series()},
        })
        watchlist, categories = tracker.run_tracker([], market_date="2026-06-30")

        assert [e["symbol"] for e in categories["settled"]] == ["TEST"]
        assert categories["settled"][0]["_exit_reason"] == tracker.EXIT_LOSS
        assert categories["invalid"] == []
        assert watchlist == []
        assert tracker._PERF_PATH.exists()
        with open(tracker._PERF_PATH, encoding="utf-8") as f:
            records = json.load(f)["history_records"]
        assert len(records) == 1
        assert records[0]["actual_outcome"]["exit_reason"] == tracker.EXIT_LOSS
        assert records[0]["performance_metrics"]["is_win"] is False

    def test_momentum_active_below_ema20_without_stop_breach_stays_active(self, monkeypatch):
        """DD-17：動能策略 active 部位收盤跌破 EMA20 但盤中止損未觸，維持 active，
        不再被 _eval_status 判定失效而無聲移除。"""
        entry = self._active_watchlist_entry()
        tracker.save_watchlist([entry])

        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            "TEST": {"price": 99.0, "today_high": 100.5, "today_low": 91.0,
                     "ema20": 100.0, "ema50": 95.0, "close_series": self._flat_series()},
        })
        watchlist, categories = tracker.run_tracker([], market_date="2026-06-30")

        assert [e["symbol"] for e in categories["active"]] == ["TEST"]
        assert categories["invalid"] == []
        assert categories["expired"] == []
        assert categories["settled"] == []
        assert watchlist[0]["status"] == "active"


# ── run_tracker：DD-18 同日重跑不得重複遞增 watch_days/active_days ──

class TestRunTrackerSameDayRerunCountersDD18:
    def _flat_series(self, value=100.0, end="2026-06-30", n=60):
        idx = pd.bdate_range(end=end, periods=n)
        return pd.Series([value] * n, index=idx)

    def test_watch_counter_not_double_incremented_on_same_day_rerun(self, monkeypatch):
        entry = {
            "symbol": "TEST", "name": "Test Corp", "sector": "Technology",
            "buy_zone": "$100.00～$105.00", "buy_zone_lower": 100.0, "buy_zone_upper": 105.0,
            "target": "$130.00", "stop_loss": "$90.00", "hold_period": "10",
            "strategy": "動能策略",
            "tracked_dates": ["2026-06-29"],
            "status": "watch", "invalid_reason": None,
            "watch_days": 1, "active_days": 0,
            "signal_date_close": 100.0, "date_added": "2026-06-29",
        }
        tracker.save_watchlist([entry])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            "TEST": {"price": 108.0, "today_high": 108.5, "today_low": 107.0,
                     "ema20": 100.0, "ema50": 95.0, "close_series": self._flat_series()},
        })

        watchlist, _ = tracker.run_tracker([], market_date="2026-06-30")
        assert watchlist[0]["watch_days"] == 2

        # 同一天再跑一次（模擬使用者手動重跑並確認繼續）
        watchlist, _ = tracker.run_tracker([], market_date="2026-06-30")
        assert watchlist[0]["watch_days"] == 2, "同日重跑不應再遞增 watch_days"

    def test_active_counter_not_double_incremented_on_same_day_rerun(self, monkeypatch):
        entry = {
            "symbol": "TEST", "name": "Test Corp", "sector": "Technology",
            "buy_zone": "$100.00～$105.00", "buy_zone_lower": 100.0, "buy_zone_upper": 105.0,
            "target": "$130.00", "stop_loss": "$90.00", "hold_period": "10",
            "strategy": "動能策略",
            "tracked_dates": ["2026-06-25", "2026-06-26", "2026-06-29"],
            "status": "active", "invalid_reason": None,
            "watch_days": 1, "active_days": 3,
            "signal_date_close": 100.0,
            "active_entry_price": 100.0, "active_start_date": "2026-06-26",
            "date_added": "2026-06-25",
            "planned_stop_loss": 90.0, "effective_stop_loss": 90.0,
            "is_breakeven_locked": False, "highest_close_since_active": 100.0,
        }
        tracker.save_watchlist([entry])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            "TEST": {"price": 102.0, "today_high": 103.0, "today_low": 101.0,
                     "ema20": 95.0, "ema50": 90.0, "close_series": self._flat_series()},
        })

        watchlist, _ = tracker.run_tracker([], market_date="2026-06-30")
        assert watchlist[0]["active_days"] == 4

        watchlist, _ = tracker.run_tracker([], market_date="2026-06-30")
        assert watchlist[0]["active_days"] == 4, "同日重跑不應再遞增 active_days"

    def test_counter_still_increments_across_different_days(self, monkeypatch):
        entry = {
            "symbol": "TEST", "name": "Test Corp", "sector": "Technology",
            "buy_zone": "$100.00～$105.00", "buy_zone_lower": 100.0, "buy_zone_upper": 105.0,
            "target": "$130.00", "stop_loss": "$90.00", "hold_period": "10",
            "strategy": "動能策略",
            "tracked_dates": ["2026-06-29"],
            "status": "watch", "invalid_reason": None,
            "watch_days": 1, "active_days": 0,
            "signal_date_close": 100.0, "date_added": "2026-06-29",
        }
        tracker.save_watchlist([entry])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            "TEST": {"price": 108.0, "today_high": 108.5, "today_low": 107.0,
                     "ema20": 100.0, "ema50": 95.0, "close_series": self._flat_series()},
        })

        watchlist, _ = tracker.run_tracker([], market_date="2026-06-30")
        assert watchlist[0]["watch_days"] == 2

        watchlist, _ = tracker.run_tracker([], market_date="2026-07-01")
        assert watchlist[0]["watch_days"] == 3, "跨日應正常遞增，守衛不應誤擋非重跑情境"


# ── run_tracker：DD-19 盤中限價單模擬進場 ────────────────────────────

class TestRunTrackerIntradayTouchEntryDD19:
    def _watch_entry(self, **overrides) -> dict:
        base = {
            "symbol": "TEST", "name": "Test Corp", "sector": "Technology",
            "buy_zone": "$100.00～$105.00", "buy_zone_lower": 100.0, "buy_zone_upper": 105.0,
            "target": "$130.00", "stop_loss": "$95.00", "hold_period": "10",
            "strategy": "動能策略",
            "tracked_dates": ["2026-06-29"],
            "status": "watch", "invalid_reason": None,
            "watch_days": 1, "active_days": 0,
            "signal_date_close": 100.0, "date_added": "2026-06-29",
        }
        base.update(overrides)
        return base

    def _flat_series(self, value=100.0, end="2026-06-30", n=60):
        idx = pd.bdate_range(end=end, periods=n)
        return pd.Series([value] * n, index=idx)

    def test_entry_price_uses_buy_zone_upper_not_close(self, monkeypatch):
        """收盤價大幅高於買入區間（盤中回落又反彈），進場代理價仍應為
        使用者實際掛單的 buy_zone_upper，而非收盤價。"""
        tracker.save_watchlist([self._watch_entry()])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            "TEST": {"price": 118.0, "today_high": 119.0, "today_low": 103.0,
                     "ema20": 95.0, "ema50": 90.0, "close_series": self._flat_series()},
        })
        watchlist, categories = tracker.run_tracker([], market_date="2026-06-30")

        assert [e["symbol"] for e in categories["active"]] == ["TEST"]
        assert watchlist[0]["active_entry_price"] == 105.0

    def test_gap_through_zone_and_stop_settles_same_day_as_closed_loss(self, monkeypatch):
        """DD-19 取代 DD-7：同日觸價成交又跌破止損（跳空急殺），保守判定為
        當日進場即停損，結算歸檔 CLOSED_LOSS，而非舊版直接標記 invalid
        拒絕進場、完全不留紀錄。"""
        tracker.save_watchlist([self._watch_entry(stop_loss="$95.00")])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            "TEST": {"price": 92.0, "today_high": 104.0, "today_low": 90.0,
                     "ema20": 100.0, "ema50": 98.0, "close_series": self._flat_series()},
        })
        watchlist, categories = tracker.run_tracker([], market_date="2026-06-30")

        assert categories["invalid"] == []
        assert [e["symbol"] for e in categories["settled"]] == ["TEST"]
        assert categories["settled"][0]["_exit_reason"] == tracker.EXIT_LOSS
        assert watchlist == []
        with open(tracker._PERF_PATH, encoding="utf-8") as f:
            records = json.load(f)["history_records"]
        assert len(records) == 1
        assert records[0]["actual_outcome"]["exit_reason"] == tracker.EXIT_LOSS
        assert records[0]["performance_metrics"]["return_pct"] < 0


# ── _slot_priority_key：tracker DD-20 名額競爭優先序 ────────────────

class TestSlotPriorityKey:
    def test_orders_by_confidence_then_l2_then_symbol(self):
        entries = [
            {"symbol": "LOW",  "ai_confidence": 7, "l2_score": 95.0},
            {"symbol": "TIE2", "ai_confidence": 9, "l2_score": 85.0},
            {"symbol": "TIE1", "ai_confidence": 9, "l2_score": 85.0},
            {"symbol": "MID",  "ai_confidence": 9, "l2_score": 91.0},
        ]
        ordered = sorted(entries, key=tracker._slot_priority_key)
        assert [e["symbol"] for e in ordered] == ["MID", "TIE1", "TIE2", "LOW"]

    def test_none_confidence_sorts_last_without_typeerror(self):
        entries = [
            {"symbol": "NONE", "ai_confidence": None, "l2_score": None},
            {"symbol": "HAS",  "ai_confidence": 6,    "l2_score": 80.0},
        ]
        ordered = sorted(entries, key=tracker._slot_priority_key)
        assert [e["symbol"] for e in ordered] == ["HAS", "NONE"]


# ── run_tracker：tracker DD-20 組合層級 active 持倉上限（槽位制）─────

class TestRunTrackerActiveCapDD20:
    def _flat_series(self, value=100.0, end="2026-06-30", n=60):
        idx = pd.bdate_range(end=end, periods=n)
        return pd.Series([value] * n, index=idx)

    def _watch_entry(self, symbol="W1", confidence=8, l2=85.0, **overrides) -> dict:
        base = {
            "symbol": symbol, "name": f"{symbol} Corp", "sector": "Technology",
            "buy_zone": "$100.00～$105.00", "buy_zone_lower": 100.0, "buy_zone_upper": 105.0,
            "target": "$130.00", "stop_loss": "$95.00", "hold_period": "10",
            "strategy": "動能策略",
            "tracked_dates": ["2026-06-29"],
            "status": "watch", "invalid_reason": None, "slot_blocked_today": False,
            "watch_days": 1, "active_days": 0,
            "signal_date_close": 100.0, "date_added": "2026-06-29",
            "active_entry_price": None, "active_start_date": None,
            "entry_regime": "BULL_TREND", "market_breadth_pct": 65.0, "vix_value": 15.0,
            "l2_score": l2, "ai_confidence": confidence, "ai_strategy_reason": "",
        }
        base.update(overrides)
        return base

    def _active_entry(self, symbol="ACT", **overrides) -> dict:
        base = {
            "symbol": symbol, "name": f"{symbol} Corp", "sector": "Technology",
            "buy_zone": "$100.00～$105.00", "buy_zone_lower": 100.0, "buy_zone_upper": 105.0,
            "target": "$130.00", "stop_loss": "$90.00", "hold_period": "10",
            "strategy": "動能策略",
            "tracked_dates": ["2026-06-25", "2026-06-26", "2026-06-29"],
            "status": "active", "invalid_reason": None, "slot_blocked_today": False,
            "watch_days": 1, "active_days": 3,
            "signal_date_close": 100.0,
            "active_entry_price": 100.0, "active_start_date": "2026-06-26",
            "date_added": "2026-06-25",
            "entry_regime": "BULL_TREND", "market_breadth_pct": 65.0, "vix_value": 15.0,
            "l2_score": 80, "ai_confidence": 8, "ai_strategy_reason": "",
            "planned_stop_loss": 90.0, "effective_stop_loss": 90.0,
            "is_breakeven_locked": False, "highest_close_since_active": 100.0,
        }
        base.update(overrides)
        return base

    def _safe_active_quote(self):
        """既有 active 部位的無事件報價：不觸止損/停利/移動停利/到期。"""
        return {"price": 100.0, "today_high": 101.0, "today_low": 99.0,
                "ema20": 98.0, "ema50": 95.0, "close_series": self._flat_series()}

    def _touch_quote(self):
        """watch 條目觸價報價：today_low <= upper，收盤在買入區間內。"""
        return {"price": 104.0, "today_high": 106.0, "today_low": 103.0,
                "ema20": 98.0, "ema50": 95.0, "close_series": self._flat_series()}

    def _no_touch_quote(self):
        """watch 條目未觸價報價：today_low > upper，收盤略高於區間（等回落）。"""
        return {"price": 107.0, "today_high": 108.0, "today_low": 106.0,
                "ema20": 98.0, "ema50": 95.0, "close_series": self._flat_series()}

    def test_touch_blocked_when_cap_full_stays_watch_with_flag(self, monkeypatch):
        """滿倉觸價被擋：維持 watch、旗標 True、無任何進場副作用、watch_days 照常遞增。"""
        monkeypatch.setattr(tracker, "MAX_ACTIVE_POSITIONS", 1)
        tracker.save_watchlist([self._active_entry(), self._watch_entry()])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            "ACT": self._safe_active_quote(), "W1": self._touch_quote(),
        })
        watchlist, categories = tracker.run_tracker([], market_date="2026-06-30")

        w1 = next(e for e in watchlist if e["symbol"] == "W1")
        assert w1["status"] == "watch"
        assert w1["slot_blocked_today"] is True
        assert w1["active_entry_price"] is None
        assert w1["watch_days"] == 2
        assert [e["symbol"] for e in categories["watch"]] == ["W1"]
        assert [e["symbol"] for e in categories["active"]] == ["ACT"]
        assert categories["settled"] == []
        assert not tracker._PERF_PATH.exists()

    def test_touch_enters_when_slot_free(self, monkeypatch):
        monkeypatch.setattr(tracker, "MAX_ACTIVE_POSITIONS", 2)
        tracker.save_watchlist([self._active_entry(), self._watch_entry()])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            "ACT": self._safe_active_quote(), "W1": self._touch_quote(),
        })
        watchlist, categories = tracker.run_tracker([], market_date="2026-06-30")

        w1 = next(e for e in watchlist if e["symbol"] == "W1")
        assert w1["status"] == "active"
        assert w1["slot_blocked_today"] is False
        assert w1["active_entry_price"] == 105.0
        assert sorted(e["symbol"] for e in categories["active"]) == ["ACT", "W1"]

    def test_priority_higher_ai_confidence_wins_single_free_slot(self, monkeypatch):
        monkeypatch.setattr(tracker, "MAX_ACTIVE_POSITIONS", 1)
        tracker.save_watchlist([
            self._watch_entry(symbol="LOWC", confidence=7),
            self._watch_entry(symbol="HIGHC", confidence=9),
        ])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            "LOWC": self._touch_quote(), "HIGHC": self._touch_quote(),
        })
        watchlist, categories = tracker.run_tracker([], market_date="2026-06-30")

        assert [e["symbol"] for e in categories["active"]] == ["HIGHC"]
        lowc = next(e for e in watchlist if e["symbol"] == "LOWC")
        assert lowc["status"] == "watch"
        assert lowc["slot_blocked_today"] is True
        assert lowc["watch_days"] == 2

    def test_priority_tiebreak_l2_score(self, monkeypatch):
        monkeypatch.setattr(tracker, "MAX_ACTIVE_POSITIONS", 1)
        tracker.save_watchlist([
            self._watch_entry(symbol="LOWL2", confidence=9, l2=85.0),
            self._watch_entry(symbol="HIGHL2", confidence=9, l2=91.0),
        ])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            "LOWL2": self._touch_quote(), "HIGHL2": self._touch_quote(),
        })
        _, categories = tracker.run_tracker([], market_date="2026-06-30")
        assert [e["symbol"] for e in categories["active"]] == ["HIGHL2"]

    def test_blocked_close_below_stop_becomes_invalid_not_settled(self, monkeypatch):
        """被擋 + 收盤跌破止損 → invalid 清除（沒掛單的死訊號），不結算、不寫績效。
        對照 DD-19 的 gap-through 測試：有名額時同樣報價會進場並當日 CLOSED_LOSS。"""
        monkeypatch.setattr(tracker, "MAX_ACTIVE_POSITIONS", 1)
        tracker.save_watchlist([self._active_entry(), self._watch_entry()])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            "ACT": self._safe_active_quote(),
            "W1": {"price": 92.0, "today_high": 104.0, "today_low": 90.0,
                   "ema20": 91.0, "ema50": 95.0, "close_series": self._flat_series()},
        })
        watchlist, categories = tracker.run_tracker([], market_date="2026-06-30")

        assert [e["symbol"] for e in categories["invalid"]] == ["W1"]
        w1 = next(e for e in watchlist if e["symbol"] == "W1")
        assert "止損" in w1["invalid_reason"]
        assert categories["settled"] == []
        assert not tracker._PERF_PATH.exists()

    def test_blocked_chase_high_close_becomes_invalid(self, monkeypatch):
        """被擋 + 收盤已追高（> upper*1.08）→ invalid：沒掛到單且股價跑遠，訊號作廢。"""
        monkeypatch.setattr(tracker, "MAX_ACTIVE_POSITIONS", 1)
        tracker.save_watchlist([self._active_entry(), self._watch_entry()])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            "ACT": self._safe_active_quote(),
            "W1": {"price": 118.0, "today_high": 119.0, "today_low": 103.0,
                   "ema20": 95.0, "ema50": 90.0, "close_series": self._flat_series()},
        })
        watchlist, categories = tracker.run_tracker([], market_date="2026-06-30")

        assert [e["symbol"] for e in categories["invalid"]] == ["W1"]
        w1 = next(e for e in watchlist if e["symbol"] == "W1")
        assert "追高" in w1["invalid_reason"]

    def test_blocked_close_recovered_stays_watch(self, monkeypatch):
        """被擋 + 收盤介於止損與區間下緣之間 → 維持 watch 續觀察。"""
        monkeypatch.setattr(tracker, "MAX_ACTIVE_POSITIONS", 1)
        tracker.save_watchlist([self._active_entry(), self._watch_entry()])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            "ACT": self._safe_active_quote(),
            "W1": {"price": 97.0, "today_high": 101.0, "today_low": 96.0,
                   "ema20": 96.0, "ema50": 95.0, "close_series": self._flat_series()},
        })
        watchlist, categories = tracker.run_tracker([], market_date="2026-06-30")

        w1 = next(e for e in watchlist if e["symbol"] == "W1")
        assert w1["status"] == "watch"
        assert w1["slot_blocked_today"] is True

    def test_settlement_same_run_does_not_refund_slot(self, monkeypatch):
        """既有 active 當日結算出場，同日觸價的 watch 仍被擋：名額次日才釋放（1-day lag）。"""
        monkeypatch.setattr(tracker, "MAX_ACTIVE_POSITIONS", 1)
        tracker.save_watchlist([self._active_entry(), self._watch_entry()])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            "ACT": {"price": 89.0, "today_high": 100.0, "today_low": 88.0,
                    "ema20": 98.0, "ema50": 95.0, "close_series": self._flat_series()},
            "W1": self._touch_quote(),
        })
        watchlist, categories = tracker.run_tracker([], market_date="2026-06-30")

        assert [e["symbol"] for e in categories["settled"]] == ["ACT"]
        w1 = next(e for e in watchlist if e["symbol"] == "W1")
        assert w1["status"] == "watch"
        assert w1["slot_blocked_today"] is True
        assert categories["active"] == []

    def test_over_cap_existing_actives_not_liquidated(self, monkeypatch):
        """既有 active 數超過上限：不強制平倉，全數維持 active 由結算自然收斂。"""
        monkeypatch.setattr(tracker, "MAX_ACTIVE_POSITIONS", 1)
        tracker.save_watchlist([
            self._active_entry(symbol="A1"),
            self._active_entry(symbol="A2"),
            self._active_entry(symbol="A3"),
        ])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            s: self._safe_active_quote() for s in syms
        })
        _, categories = tracker.run_tracker([], market_date="2026-06-30")

        assert sorted(e["symbol"] for e in categories["active"]) == ["A1", "A2", "A3"]
        assert categories["settled"] == []
        assert categories["expired"] == []

    def test_slot_blocked_flag_reset_next_day_without_touch(self, monkeypatch):
        monkeypatch.setattr(tracker, "MAX_ACTIVE_POSITIONS", 1)
        tracker.save_watchlist([self._active_entry(), self._watch_entry()])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            "ACT": self._safe_active_quote(), "W1": self._touch_quote(),
        })
        watchlist, _ = tracker.run_tracker([], market_date="2026-06-30")
        assert next(e for e in watchlist if e["symbol"] == "W1")["slot_blocked_today"] is True

        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            "ACT": self._safe_active_quote(), "W1": self._no_touch_quote(),
        })
        watchlist, _ = tracker.run_tracker([], market_date="2026-07-01")
        w1 = next(e for e in watchlist if e["symbol"] == "W1")
        assert w1["slot_blocked_today"] is False
        assert w1["status"] == "watch"

    def test_slot_blocked_flag_reset_even_when_symbol_missing_from_latest(self, monkeypatch):
        """F1 回歸：下載失敗（symbol 不在 latest）仍須重置旗標，不殘留昨日 True。"""
        entry = self._watch_entry(slot_blocked_today=True)
        tracker.save_watchlist([entry])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {})
        watchlist, _ = tracker.run_tracker([], market_date="2026-06-30")
        assert watchlist[0]["slot_blocked_today"] is False

    def test_new_signals_still_added_as_watch_at_cap(self, monkeypatch):
        """滿倉時當日 L3 新訊號照常加入 watchlist（B/C 步驟不受名額影響）。"""
        monkeypatch.setattr(tracker, "MAX_ACTIVE_POSITIONS", 1)
        tracker.save_watchlist([self._active_entry()])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            "ACT": self._safe_active_quote(),
        })
        stock = {"symbol": "NEW1", "name": "New Co", "sector": "Technology",
                 "buy_zone": "$100～$105", "target": "$120", "stop_loss": "$90",
                 "hold_period": "10", "strategy": "突破策略",
                 "confidence": 8, "total_score": 80, "price": 102.0,
                 "strategy_reason": ""}
        watchlist, categories = tracker.run_tracker([stock], market_date="2026-06-30")

        assert len(categories["new"]) == 1
        new1 = next(e for e in watchlist if e["symbol"] == "NEW1")
        assert new1["status"] == "watch"
        assert new1["slot_blocked_today"] is False

    def test_reset_path_clears_slot_blocked_flag(self, monkeypatch):
        """F2 回歸：今日被擋（旗標 True）的 watch 條目同日被 L3 覆寫展期（reset），
        旗標須隨 base 字典清為 False（新買入區間下「今日觸價被擋」已不成立）。"""
        monkeypatch.setattr(tracker, "MAX_ACTIVE_POSITIONS", 1)
        tracker.save_watchlist([self._active_entry(), self._watch_entry()])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            "ACT": self._safe_active_quote(), "W1": self._touch_quote(),
        })
        stock = {"symbol": "W1", "name": "W1 Corp", "sector": "Technology",
                 "buy_zone": "$106～$110", "target": "$130", "stop_loss": "$98",
                 "hold_period": "10", "strategy": "動能策略",
                 "confidence": 9, "total_score": 88, "price": 107.0,
                 "strategy_reason": ""}
        watchlist, categories = tracker.run_tracker([stock], market_date="2026-06-30")

        assert len(categories["reset"]) == 1
        w1 = next(e for e in watchlist if e["symbol"] == "W1")
        assert w1["slot_blocked_today"] is False
        assert w1["buy_zone_upper"] == 110.0

    def test_watchlist_saved_order_preserved_after_priority_evaluation(self, monkeypatch):
        """名單優先序只影響進場資格，save_watchlist 寫出順序與讀入一致。"""
        tracker.save_watchlist([
            self._watch_entry(symbol="CCC", confidence=5),
            self._watch_entry(symbol="AAA", confidence=9),
            self._watch_entry(symbol="BBB", confidence=7),
        ])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            s: self._no_touch_quote() for s in syms
        })
        watchlist, _ = tracker.run_tracker([], market_date="2026-06-30")
        assert [e["symbol"] for e in watchlist] == ["CCC", "AAA", "BBB"]

    def test_out_of_roster_touch_blocked_even_with_free_slot(self, monkeypatch):
        """v2 名單制關鍵行為：名額 1、名單第 1 名（HIGHC）未觸價、第 2 名（LOWC）
        觸價 → LOWC 被擋（使用者只掛了 HIGHC 的單）、無人進場。
        事後擇優（v1）會錯誤放行 LOWC，把使用者沒掛單的交易寫進績效。"""
        monkeypatch.setattr(tracker, "MAX_ACTIVE_POSITIONS", 1)
        tracker.save_watchlist([
            self._watch_entry(symbol="HIGHC", confidence=9),
            self._watch_entry(symbol="LOWC", confidence=7),
        ])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            "HIGHC": self._no_touch_quote(), "LOWC": self._touch_quote(),
        })
        watchlist, categories = tracker.run_tracker([], market_date="2026-06-30")

        assert categories["active"] == []
        lowc = next(e for e in watchlist if e["symbol"] == "LOWC")
        assert lowc["status"] == "watch"
        assert lowc["slot_blocked_today"] is True
        highc = next(e for e in watchlist if e["symbol"] == "HIGHC")
        assert highc["status"] == "watch"
        assert highc["slot_blocked_today"] is False
        assert not tracker._PERF_PATH.exists()

    def test_categories_contains_order_plan_with_todays_new_entry(self, monkeypatch):
        """categories["order_plan"]：free_slots／依優先序排序的 roster（含今日
        新進條目）／eligible 前 N 名切點。"""
        monkeypatch.setattr(tracker, "MAX_ACTIVE_POSITIONS", 2)
        tracker.save_watchlist([
            self._watch_entry(symbol="LOWC", confidence=7),
            self._watch_entry(symbol="HIGHC", confidence=9),
        ])
        monkeypatch.setattr(tracker, "_fetch_latest", lambda syms: {
            s: self._no_touch_quote() for s in syms
        })
        stock = {"symbol": "NEW1", "name": "New Co", "sector": "Technology",
                 "buy_zone": "$100～$105", "target": "$120", "stop_loss": "$90",
                 "hold_period": "10", "strategy": "突破策略",
                 "confidence": 8, "total_score": 80, "price": 102.0,
                 "strategy_reason": ""}
        _, categories = tracker.run_tracker([stock], market_date="2026-06-30")

        plan = categories["order_plan"]
        assert plan["free_slots"] == 2
        assert [e["symbol"] for e in plan["roster"]] == ["HIGHC", "NEW1", "LOWC"]
        assert plan["eligible"] == {"HIGHC", "NEW1"}


# ── compute_order_plan：tracker DD-20 事前掛單名單純函式 ─────────────

class TestComputeOrderPlan:
    def _entry(self, symbol, status="watch", confidence=8, l2=85.0):
        return {"symbol": symbol, "status": status,
                "ai_confidence": confidence, "l2_score": l2}

    def test_roster_sorted_and_eligible_cut(self, monkeypatch):
        monkeypatch.setattr(tracker, "MAX_ACTIVE_POSITIONS", 2)
        wl = [
            self._entry("ACT1", status="active"),
            self._entry("LOW", confidence=6),
            self._entry("HIGH", confidence=9),
            self._entry("MID", confidence=8),
        ]
        plan = tracker.compute_order_plan(wl)
        assert plan["free_slots"] == 1
        assert [e["symbol"] for e in plan["roster"]] == ["HIGH", "MID", "LOW"]
        assert plan["eligible"] == {"HIGH"}

    def test_over_cap_active_gives_zero_slots_empty_eligible(self, monkeypatch):
        monkeypatch.setattr(tracker, "MAX_ACTIVE_POSITIONS", 1)
        wl = [self._entry(f"A{i}", status="active") for i in range(3)]
        wl.append(self._entry("W1"))
        plan = tracker.compute_order_plan(wl)
        assert plan["free_slots"] == 0
        assert plan["eligible"] == set()
        assert [e["symbol"] for e in plan["roster"]] == ["W1"]

    def test_empty_watchlist(self):
        plan = tracker.compute_order_plan([])
        assert plan["roster"] == []
        assert plan["eligible"] == set()

    def test_deterministic_repeat(self, monkeypatch):
        monkeypatch.setattr(tracker, "MAX_ACTIVE_POSITIONS", 2)
        wl = [self._entry("B"), self._entry("A"), self._entry("ACT", status="active")]
        p1 = tracker.compute_order_plan(wl)
        p2 = tracker.compute_order_plan(wl)
        assert p1["eligible"] == p2["eligible"]
        assert [e["symbol"] for e in p1["roster"]] == [e["symbol"] for e in p2["roster"]]

    def test_invalid_entries_excluded_from_roster(self):
        wl = [self._entry("W1"), self._entry("BAD", status="invalid")]
        plan = tracker.compute_order_plan(wl)
        assert [e["symbol"] for e in plan["roster"]] == ["W1"]
