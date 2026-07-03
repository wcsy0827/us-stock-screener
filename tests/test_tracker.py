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
        entry = _active_entry(hold_period="5", active_days=5, target="$200")
        result = tracker._check_settlement(entry, price=101, today_high=102, today_low=100)
        assert result == (tracker.EXIT_EXPIRED, 101)

    def test_no_trigger_returns_none(self):
        entry = _active_entry(hold_period="10", active_days=1, target="$200")
        result = tracker._check_settlement(entry, price=101, today_high=102, today_low=100)
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
