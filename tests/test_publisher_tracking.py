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
    """tracker DD-20 / publisher DD-8：滿倉未進場的 watch 條目顯示觸價被擋註記。"""

    def test_blocked_watch_shows_slot_full_text_with_remaining_days(self):
        entry = _entry(slot_blocked_today=True, tracked_dates=["d1", "d2"])
        html = publisher._tracking_row(entry, "watch")
        assert f"今日觸價但持倉已滿 {publisher.MAX_ACTIVE_POSITIONS} 支，未進場" in html
        assert "剩 3 天自動移除" in html
        assert "等待回落至買入區間" not in html

    def test_unblocked_watch_keeps_waiting_text(self):
        entry = _entry(slot_blocked_today=False)
        html = publisher._tracking_row(entry, "watch")
        assert "等待回落至買入區間" in html
        assert "持倉已滿" not in html

    def test_legacy_entry_without_flag_keeps_waiting_text(self):
        """存量條目缺 slot_blocked_today 欄位時，維持既有顯示不拋錯。"""
        entry = _entry()
        entry.pop("slot_blocked_today", None)
        html = publisher._tracking_row(entry, "watch")
        assert "等待回落至買入區間" in html

    def test_blocked_text_reflects_patched_cap(self, monkeypatch):
        # 注意 patch publisher 的綁定（from tracker import 已複製），非 tracker 的
        monkeypatch.setattr(publisher, "MAX_ACTIVE_POSITIONS", 3)
        entry = _entry(slot_blocked_today=True)
        html = publisher._tracking_row(entry, "watch")
        assert "持倉已滿 3 支" in html
