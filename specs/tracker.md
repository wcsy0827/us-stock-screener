# Tracker — 訊號追蹤規格

## Purpose

追蹤 L3 AI 推薦的個股是否已落入買入區間（active）、仍在等待（watch）、已失效（invalid）、已到期移除（expired），或已觸發結算並歸檔（settled）。活躍清單持久化於 `data/watchlist.json`；歷史績效持久化於 `data/performance_history.json`。

## Behavior

### 狀態機

```
[新加入] → watch
  watch  → active   （price 落入 buy_zone）
  watch  → invalid  （失效條件觸發）
  watch  → expired  （watch_days >= MAX_WATCH_DAYS=5）
  active → settled  （CLOSED_PROFIT / CLOSED_LOSS / FORCE_EXPIRED，歸檔至 performance_history.json）
  active → invalid  （失效條件觸發，e.g. 跌破止損但尚未達結算門檻）
  invalid → expired （_days() >= MAX_WATCH_DAYS）
```

**active 部位不再由 `_is_expired()` 到期**，改由 `_check_settlement()` 控制完整生命週期。

- **必須**：watch 和 active 使用**分開的計數器**（`watch_days` / `active_days`），不能共用總追蹤天數
- **不得**：active 持倉到期上限使用固定的 5 日；必須讀取 AI 指定的 `hold_period` 字串並解析
- **必須**：同一天重複執行時（`is_rerun`），清除當日新增的股票後重新加入，已有的跨日追蹤股票不受影響

### 失效條件（雙軌制）

依 `strategy` 欄位區分：

| strategy | 失效門檻 |
|----------|----------|
| `"反轉策略"` | `price < stop_loss 絕對價` |
| 其他（動能/突破）| `price < EMA20` |

- **必須**：追高失效（`price > upper * 1.08`）只適用於**非 active** 狀態，active 持倉大漲屬正常獲利，不觸發追高失效
- **不得**：對反轉策略使用 EMA50 作為失效門檻（見 DD-1）

### 狀態機下限判定順序

```
price < stop_loss?           → invalid
price > upper * 1.08 且非active? → invalid（追高）
price > upper * 1.01?        → watch（等回落）
price >= lower?              → active（進場）
price < lower 且 >= stop_loss?  → watch（繼續觀察）
price < lower 且 < stop_loss?   → invalid
```

### 拆股免疫

- **必須**：首次加入 watchlist 時記錄 `signal_date_close`（當日 auto_adjust 收盤價）
- **必須**：每次評估前計算 `split_factor = 當前調整後歷史收盤 / signal_date_close`
- **若** `abs(split_factor - 1.0) > 0.01`：在記憶體中臨時縮放 `buy_zone_lower`、`buy_zone_upper`、`stop_loss`，**不寫回 watchlist**
- 原始絕對值保留在 watchlist 供下次重算，避免累積誤差

## Interface

```python
def run_tracker(new_ranked: list[dict]) -> tuple[list[dict], dict]:
    """執行訊號追蹤流程，回傳 (updated_watchlist, categories)。"""

# categories 結構：
# {
#   "active":  list[dict],   # 持倉中
#   "watch":   list[dict],   # 等待中
#   "invalid": list[dict],   # 失效但未到期
#   "expired": list[dict],   # 本次到期快照
#   "new":     list[dict],   # 本次新加入（完整 AI 資料）
#   "reset":   list[dict],   # 本次重新入選並重置
# }

def _parse_hold_period(hold_period_str: str, default: int = 10) -> int:
    """解析 "5-10 個交易日" → 取最大值（10）；無法解析回傳 default。"""

def _calc_split_factor(signal_date: str, signal_date_close: float,
                       close_series: pd.Series) -> float:
    """
    回傳 adjusted_hist / signal_date_close。
    無拆股 → 1.0；3:1 拆股後 → 約 0.333。
    """

def _is_expired(entry: dict) -> bool:
    """watch/invalid: _days() >= 5；active: active_days >= hold_period。"""
```

## Design Decisions

### DD-1: 反轉策略失效門檻改用 stop_loss 絕對價，而非 EMA50

- **選擇**：`price < stop_loss` 作為反轉策略的唯一失效條件
- **原因**：反轉策略的進場點（buy_zone）本就在 EMA50 之下，若以「跌破 EMA50」作為失效條件，股票在加入 watchlist 的第一天就會立刻觸發失效。EMA50 對反轉股語意無效。
- **捨棄**：`price < ema50`（會在 day 1 立刻失效）、`price < ema20`（同樣問題）

### DD-2: watch / active 計數器分離

- **選擇**：`watch_days` 和 `active_days` 獨立遞增，到期邏輯分別比對 `MAX_WATCH_DAYS` 和 `hold_period`
- **原因**：若共用總天數計數，active 持倉（應持有 7-10 日）在第 5 天就被截斷，導致正常獲利被強制出清。
- **捨棄**：`len(tracked_dates) >= MAX_TRACK_DAYS`（統一 5 日上限，active 被截斷）

### DD-3: split_factor 臨時縮放，不寫回 watchlist

- **選擇**：每次評估時即時計算、即時縮放，評估後丟棄
- **原因**：若寫回 watchlist 就等於把縮放後的值當作新基準，下次再縮放會累積誤差（double-apply）。保留原始值讓每次都從 `signal_date_close` 重算，冪等且精確。
- **捨棄**：覆寫 watchlist 欄位（累積誤差、難以回溯）

### DD-4: active 追高保護

- **選擇**：`price > upper * 1.08` 的追高失效只在 `status != "active"` 時觸發
- **原因**：已持倉的股票大漲（>8%）是正常獲利波段，由 `_check_settlement()` 的 CLOSED_PROFIT 接管，不應在中途強制認定失效。未持倉的 watch 股票追高才有意義。
- **捨棄**：所有狀態一律檢查追高（active 持倉大漲會被錯殺）

### DD-5: active_entry_price = 首次轉 active 當日收盤（代理進場價）

- **選擇**：以股票第一次 `status` 從 `watch` 轉為 `active` 當日的收盤價作為報酬率計算基準
- **原因**：系統只知道 AI 給的買入區間（`$185~$188`），不知道用戶的實際成交價。使用「首次落入買入區間當日收盤」作為代理，比 `buy_zone_lower`（AI 下限，過於樂觀）更接近真實進場成本。
- **捨棄**：`buy_zone_lower` 作為進場價（永遠在區間下限，系統性高估報酬）；`buy_zone_midpoint`（區間中點，仍是估算）

### DD-6: active 部位生命週期改由 _check_settlement() 接管

- **選擇**：`_is_expired()` 對 active 狀態一律回傳 `False`；active 的結算完全由 `_check_settlement()` 的 CLOSED_PROFIT / CLOSED_LOSS / FORCE_EXPIRED 控制
- **原因**：`_is_expired()` 只能判斷「時間到期」，無法觸發停利/停損邏輯；讓兩套機制同時運作會有 double-exit 風險（先被 `_is_expired()` 移除，再被 `_check_settlement()` 嘗試歸檔 → KeyError）
- **捨棄**：`_is_expired()` 同時適用 active（兩套機制競爭，造成邏輯混亂）

---

## performance_history.json Schema

`data/performance_history.json` 為績效結算資料庫，格式如下：

```json
{
  "history_records": [
    {
      "meta_data": { "ticker", "company_name", "sector" },
      "signal_details": {
        "signal_date",       // 加入 watchlist 日期
        "entry_regime",      // 信號日 Regime（來自 market_context）
        "market_breadth_pct",
        "vix_value",
        "l2_score",          // L2 技術評分
        "assigned_strategy",
        "ai_confidence",     // AI 信心分數（1-10）
        "ai_strategy_reason"
      },
      "execution_plan": { "buy_zone_lower", "buy_zone_upper", "planned_target", "planned_stop_loss" },
      "actual_outcome": {
        "triggered_date",    // 首次進場（active）日期
        "actual_entry_price", // 代理進場價（首次轉 active 當日收盤）
        "exit_date",
        "actual_exit_price",
        "exit_reason",       // CLOSED_PROFIT / CLOSED_LOSS / FORCE_EXPIRED
        "holding_days"
      },
      "performance_metrics": { "return_pct", "is_win" }
    }
  ]
}
```

**原子寫入**：先寫 `.tmp` 暫存檔，再以 `rename` 替換，防止寫入中途崩潰導致 JSON 損壞。

---

## Acceptance Criteria

- [ ] watch 股票連跑 6 次（6 個交易日），第 6 次出現在 `expired`，不是 `watch`
- [ ] active 部位不因 `_is_expired()` 到期：跑超過 10 次而未觸發停利/停損/FORCE_EXPIRED，不出現在 expired
- [ ] CLOSED_PROFIT：手動將 watchlist entry 的 `target` 設為略低於 `current_price` → 觸發歸檔，從 watchlist 消失，出現在 `categories["settled"]`
- [ ] CLOSED_LOSS：`stop_loss` 設為略高於 `current_price` → 觸發歸檔
- [ ] FORCE_EXPIRED：手動將 `active_days` 設為等於 `hold_period` 解析值 → 觸發歸檔
- [ ] 歸檔後 `performance_history.json` 有新紀錄，`return_pct` 計算正確，`is_win` 符號正確
- [ ] `entry_regime` 有值（非空字串），來自 `market_context["regime"]`
- [ ] `actual_entry_price` = 首次轉 active 當日的收盤價（非 buy_zone_lower）
- [ ] 反轉策略股票（strategy="反轉策略"）：`price=179, stop_loss="$180"` → invalid；`price=175, ema20=176` → **仍依 stop_loss 判斷**，不依 EMA20
- [ ] 動能策略股票：`price < ema20` → invalid，即使 `price > stop_loss`
- [ ] 拆股模擬：`signal_date_close=300`，close_series 信號日顯示 100 → `split_factor≈0.333`，門檻等比例縮小
