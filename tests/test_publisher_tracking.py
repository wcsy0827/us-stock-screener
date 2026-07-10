"""publisher._tracking_row 純函式測試：動態止損顯示、移動停利觸發線、
watch/invalid 剩餘天數（依 tracker._max_watch_days 差異化上限，非寫死 5 天）。"""

import publisher


def _entry(**overrides) -> dict:
    base = {
        "symbol": "TEST", "name": "Test Corp", "strategy": "動能策略",
        "tracked_dates": ["d1", "d2"], "current_price": 108.0,
        "buy_zone": "$100.00～$105.00", "target": "$130.00", "stop_loss": "$90.00",
        "hold_period": "10", "active_days": 2,
        "active_entry_price": 100.0,
    }
    base.update(overrides)
    return base


class TestActiveDynamicStopLoss:
    def test_shows_effective_stop_loss_when_present(self):
        entry = _entry(effective_stop_loss=95.0, is_breakeven_locked=False)
        html = publisher._tracking_row(entry, "active")
        assert "$95.00" in html
        assert "$90.00" not in html

    def test_shows_breakeven_lock_tag(self):
        entry = _entry(effective_stop_loss=105.0, is_breakeven_locked=True)
        html = publisher._tracking_row(entry, "active")
        assert "$105.00" in html
        assert "🔒保本" in html

    def test_falls_back_to_ai_stop_loss_when_effective_missing(self):
        """存量條目缺 effective_stop_loss 時，退化為顯示 AI 原始 stop_loss。"""
        entry = _entry(stop_loss="$88.00")
        entry.pop("effective_stop_loss", None)
        html = publisher._tracking_row(entry, "active")
        assert "$88.00" in html


class TestActiveTrailingStopLine:
    def test_shows_trailing_line_once_armed(self):
        # entry=100, highest=112 → 峰值浮盈 12% ≥ 10% 門檻，觸發線 = 112*(1-0.05) = 106.4
        entry = _entry(active_entry_price=100.0, highest_close_since_active=112.0,
                       effective_stop_loss=95.0)
        html = publisher._tracking_row(entry, "active")
        assert "移動停利線 $106.40" in html

    def test_no_trailing_line_before_armed(self):
        # 峰值浮盈僅 5% < 10% 門檻，尚未武裝
        entry = _entry(active_entry_price=100.0, highest_close_since_active=105.0,
                       effective_stop_loss=95.0)
        html = publisher._tracking_row(entry, "active")
        assert "移動停利線" not in html

    def test_reversal_strategy_never_shows_trailing_line(self):
        """DD-13：反轉策略精確排除移動停利，報告不應顯示觸發線。"""
        entry = _entry(strategy="反轉策略", active_entry_price=100.0,
                       highest_close_since_active=120.0, effective_stop_loss=90.0)
        html = publisher._tracking_row(entry, "active")
        assert "移動停利線" not in html


class TestWatchInvalidRemainingDaysUsesStrategyLimit:
    def test_watch_remaining_days_uses_reversal_10day_limit(self):
        """反轉策略 watch 上限為 10 日（非寫死 5 日）。"""
        entry = _entry(strategy="反轉策略", tracked_dates=["d1", "d2", "d3"])
        html = publisher._tracking_row(entry, "watch")
        assert "剩 7 天自動移除" in html

    def test_watch_remaining_days_uses_default_5day_limit(self):
        entry = _entry(strategy="動能策略", tracked_dates=["d1", "d2"])
        html = publisher._tracking_row(entry, "watch")
        assert "剩 3 天自動移除" in html

    def test_invalid_remaining_days_uses_consolidation_volatile_3day_limit(self):
        """DD-16：高波動整理市的突破策略 watch 上限縮短為 3 日。"""
        entry = _entry(strategy="突破策略", entry_regime="CONSOLIDATION_VOLATILE",
                       tracked_dates=["d1"], invalid_reason="趨勢轉弱，訊號失效")
        html = publisher._tracking_row(entry, "invalid")
        assert "剩 2 天自動移除" in html


class TestWatchSlotBlockedDisplay:
    """tracker DD-20 v2 / publisher DD-8：未在掛單名單的 watch 條目顯示觸價被擋註記。"""

    def test_blocked_watch_shows_not_in_roster_text_with_remaining_days(self):
        entry = _entry(slot_blocked_today=True, tracked_dates=["d1", "d2"])
        html = publisher._tracking_row(entry, "watch")
        assert "今日觸價但未在掛單名單，未進場" in html
        assert "剩 3 天自動移除" in html
        assert "等待回落至買入區間" not in html

    def test_unblocked_watch_keeps_waiting_text(self):
        entry = _entry(slot_blocked_today=False)
        html = publisher._tracking_row(entry, "watch")
        assert "等待回落至買入區間" in html
        assert "未在掛單名單" not in html

    def test_legacy_entry_without_flag_keeps_waiting_text(self):
        """存量條目缺 slot_blocked_today 欄位時，維持既有顯示不拋錯。"""
        entry = _entry()
        entry.pop("slot_blocked_today", None)
        html = publisher._tracking_row(entry, "watch")
        assert "等待回落至買入區間" in html

    def test_watch_row_shows_confidence_and_l2(self):
        """publisher DD-9：留意清單列顯示信心/L2，供與掛單計畫對照。"""
        entry = _entry(ai_confidence=9, l2_score=91.4)
        html = publisher._tracking_row(entry, "watch")
        assert "信心 9/10" in html
        assert "L2 91 分" in html

    def test_watch_row_missing_confidence_shows_na(self):
        entry = _entry()
        entry.pop("ai_confidence", None)
        entry.pop("l2_score", None)
        html = publisher._tracking_row(entry, "watch")
        assert "信心 N/A" in html


class TestOrderPlanSection:
    """publisher DD-9：明日掛單計畫區段（資料來自 tracker.compute_order_plan）。"""

    def _roster_entry(self, symbol, confidence=8, l2=85.0, **overrides):
        base = {
            "symbol": symbol, "name": f"{symbol} Corp", "strategy": "動能策略",
            "ai_confidence": confidence, "l2_score": l2,
            "buy_zone": "$100.00～$105.00", "buy_zone_upper": 105.0,
            "stop_loss": "$95.00", "target": "$130.00",
            "tracked_dates": ["d1", "d2"],
        }
        base.update(overrides)
        return base

    def test_empty_roster_returns_empty_string(self):
        assert publisher._order_plan_section({"free_slots": 3, "roster": []}) == ""
        assert publisher._order_plan_section({}) == ""

    def test_marks_top_free_slots_as_recommended_rest_as_backup(self):
        plan = {"free_slots": 1, "roster": [
            self._roster_entry("HIGHC", confidence=9),
            self._roster_entry("LOWC", confidence=7),
        ]}
        html = publisher._order_plan_section(plan)
        # 排名順序照 roster，第 1 名建議掛單、第 2 名備援
        assert html.index("#1 HIGHC") < html.index("#2 LOWC")
        assert html.count("✅ 建議掛單") == 1
        assert html.count("⏸ 備援（名額外）") == 1
        assert "明日可進場名額 1 支" in html

    def test_zero_slots_shows_full_message_but_lists_roster(self):
        plan = {"free_slots": 0, "roster": [self._roster_entry("W1")]}
        html = publisher._order_plan_section(plan)
        assert "名額 0，持倉已滿，明日不建議掛新單" in html
        assert "#1 W1" in html
        assert "✅ 建議掛單" not in html

    def test_row_contains_order_price_and_trade_params(self):
        plan = {"free_slots": 1, "roster": [self._roster_entry("W1", confidence=9, l2=91.0)]}
        html = publisher._order_plan_section(plan)
        assert "掛單價 $105.00" in html
        assert "買入區間 $100.00～$105.00" in html
        assert "止損 $95.00" in html
        assert "目標 $130.00" in html
        assert "信心 9/10" in html
        assert "L2 91 分" in html

    def test_remaining_days_uses_strategy_limit(self):
        """反轉策略 watch 上限 10 日：已追蹤 2 日 → 剩 8 天觀察期。"""
        plan = {"free_slots": 1, "roster": [
            self._roster_entry("REV", strategy="反轉策略", tracked_dates=["d1", "d2"]),
        ]}
        html = publisher._order_plan_section(plan)
        assert "剩 8 天觀察期" in html
