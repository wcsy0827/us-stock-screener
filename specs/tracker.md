# Tracker — 訊號追蹤規格

## Purpose

追蹤 L3 AI 推薦的個股是否已落入買入區間（active）、仍在等待（watch）、已失效（invalid）、已到期移除（expired），或已觸發結算並歸檔（settled）。活躍清單持久化於 `data/watchlist.json`；歷史績效持久化於 `data/performance_history.json`。

## Behavior

### 狀態機

```
[新加入] → watch
  watch  → active   （次日 price 落入 buy_zone，1-day lag）
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
price < stop_loss?                           → invalid（反轉策略）
price < ema20?                               → invalid（動能/突破策略）
price > upper * 1.08 且非 active?            → invalid（追高）
price > upper * 1.01?                        → watch（等回落）
price >= lower 且 price <= stop_loss?        → invalid（開盤跳空安全攔截）
price >= lower 且 price > stop_loss?         → active（進場）
price < lower 且 price >= stop_loss?         → watch（繼續觀察）
price < lower 且 price < stop_loss?          → invalid
```

- **必須**：`watch → active` 轉換前，需額外確認 `price > stop_loss`，防止 AI 誤設止損在買入區間內時造成績效污染（DD-7）

### 重複訊號雙軌分流（DD-9）

當每日 L3 篩出的新訊號與 watchlist 中既有個股重疊時，依當前狀態分流：

| 現有狀態 | 處理方式 |
|---------|---------|
| `active`（已持倉） | **訊號免疫**：跳過，不重置任何欄位，沿用原交易計劃。持倉繼續出現在 `categories["active"]` |
| `watch` / `invalid` | **訊號覆寫展期**：以最新 AI 參數（buy_zone、stop_loss、target、strategy）全面覆蓋，`watch_days` 歸零，`date_added` 更新為今日，重新計算 5 天觀察期 |
| 全新個股 | 加入 watchlist，`status=watch`，本輪不評估（1-day lag） |

### 執行順序約束（DD-11）

每日執行順序**必須**為 D → E → B/C：

1. **D（下載）**：`_fetch_latest(existing_symbols)`，只下載現有 watchlist 的 High/Low/Close/EMA
2. **E（評估）**：對現有 watchlist 全部條目執行 `_eval_status()` + `_check_settlement()`
3. **B/C（新訊號）**：處理今日 L3 新票，active 免疫、watch 覆寫、新增

此順序確保：
- 現有 watch 條目先以舊參數評估進場，再被新 AI 參數覆寫（若仍為 watch）
- 今日新加入的個股在本輪不被評估（1-day lag 天然實現）
- active 持倉在評估後受 DD-9 保護，不被同日新訊號重置

### 結算優先順序（DD-10）

active 部位結算依下列優先順序判定，盤中止損/停利使用**當日盤中 High/Low 實質觸價**；移動停利使用**收盤價**：

```
1. 黑天鵝防禦：today_low ≤ effective_stop_loss 且 today_high ≥ target → CLOSED_LOSS（保守原則）
2. 盤中止損：today_low ≤ effective_stop_loss                          → CLOSED_LOSS
3. 盤中停利：today_high ≥ target                                      → CLOSED_PROFIT
4. 移動停利：峰值浮盈 > 10% 且收盤回撤 ≥ 5%（僅動能/突破策略）       → CLOSED_TRAILING_STOP
5. 持倉到期：active_days ≥ hold_period                                → FORCE_EXPIRED
```

止損使用 `effective_stop_loss`（動態有效止損，見 DD-12），初始值等於 `planned_stop_loss`；觸發保本鎖定後上移至 `buy_zone_upper`。

出場價規則：
- `CLOSED_PROFIT`         → `exit_price = target`（目標絕對值，非 today_high）
- `CLOSED_LOSS`           → `exit_price = effective_stop_loss`（有效止損絕對值，非 today_low）
- `CLOSED_TRAILING_STOP`  → `exit_price = close`（當日收盤價）
- `FORCE_EXPIRED`         → `exit_price = close`（當日收盤價）

High/Low NaN 防禦：若 today_high 或 today_low 為 NaN（停牌/數據缺失），強制 fallback 為當日 close，退化為收盤價判定，避免停損免疫 Bug。

### 拆股免疫

- **必須**：首次加入 watchlist 時記錄 `signal_date_close`（當日 auto_adjust 收盤價）
- **必須**：每次評估前計算 `split_factor = 當前調整後歷史收盤 / signal_date_close`
- **若** `abs(split_factor - 1.0) > 0.01`：在記憶體中臨時縮放 `buy_zone_lower`、`buy_zone_upper`、`stop_loss`、`target`、`planned_stop_loss`、`effective_stop_loss`、`active_entry_price`、`highest_close_since_active`，**不寫回 watchlist**
- `highest_close_since_active` 在 watchlist 中以**原生未拆股標尺**存儲；比對前先乘以 `split_factor` 轉換至調整後標尺
- **必須**：觸發結算時，以 `adj`（縮放後的臨時字典）呼叫 `_check_settlement()`，確保出場價已正確反映拆股（見 DD-10）
- 原始絕對值保留在 watchlist 供下次重算，避免累積誤差

## Interface

```python
def run_tracker(
    new_ranked: list[dict],
    market_context: dict | None = None,
    market_date: str | None = None,   # 由 pipeline 注入 SPY 最後交易日（DD-11）
) -> tuple[list[dict], dict]:
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

def _fetch_latest(symbols: list[str]) -> dict[str, dict]:
    """
    批次下載最新 Close/High/Low/EMA。
    High/Low NaN 時 fallback 為 close，確保結算邏輯不受停牌股影響。
    """

def _check_settlement(
    entry: dict,
    price: float,
    today_high: float | None = None,
    today_low: float | None = None,
) -> tuple[str, float] | None:
    """
    判斷 active 部位是否觸發結算，使用盤中 High/Low 實質觸價（DD-10）。
    拆股情境下應傳入 adj（縮放後）作為 entry，確保出場價正確。
    """

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

### DD-12: 風控數據庫雙欄位結構（Original vs. Effective Stop Loss）

- **選擇**：`watchlist.json` 每筆 active entry 新增四個欄位：
  - `planned_stop_loss: float`（AI 原始止損，唯讀，DD-3 拆股計算基底）
  - `effective_stop_loss: float`（動態有效止損，保本鎖定後上移）
  - `highest_close_since_active: float`（進場後最高收盤，以**原生未拆股標尺**存儲）
  - `is_breakeven_locked: bool`（明示保本鎖定旗標，預設 `false`）
- **初始值**：`planned_stop_loss = effective_stop_loss = parsed(stop_loss)`；`highest_close_since_active = active_entry_price`；`is_breakeven_locked = false`
- **行為**：每日評估時，`planned_stop_loss` 與 `effective_stop_loss` 均被 `split_factor` 臨時縮放（不寫回），保本觸發時僅更新 `effective_stop_loss`。`highest_close_since_active` 只在今日原生標尺創新高時更新，防止逆向除法累積浮點誤差。
- **捨棄**：直接修改 `stop_loss`（DD-3 下次重算會 double-apply split_factor）；用浮點差判斷「是否已移動過」（抖動風險，改用 `is_breakeven_locked`）

### DD-13: 全自動保本鎖定與移動停利

- **選擇**：在 `_check_settlement()` 之前執行 `_apply_risk_controls()`，對 active 持倉自動維護風控狀態
- **條款 1（保本鎖定）**：`current_close >= active_entry_price + (target - active_entry_price) × 0.5` 且 `is_breakeven_locked == false`，將 `effective_stop_loss` 上移至 `buy_zone_upper`，鎖定 `is_breakeven_locked = true`
- **條款 2（移動停利）**：僅適用動能/突破策略（精確排除 `strategy == "反轉策略"`）；峰值浮盈 `(highest_close - entry) / entry >= 10%` 且收盤回撤 `(highest_close - close) / highest_close >= 5%`，觸發 `CLOSED_TRAILING_STOP`，出場價 = 收盤
- **開倉當日安全**：`watch → active` 初始化後立刻同步寫入 `settlement_entry`（adj），防止同日停損時 `active_entry_price = 0` 引發除零錯誤
- **捨棄**：修改 `stop_loss`（DD-3 衝突）；用收盤以外的價格觸發移動停利（盤中低點可能誤觸短線震盪）；對反轉策略啟用移動停利（反轉股進場點在 EMA50 下方，波動語意不同）

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

### DD-7: watch → active 加入 stop_loss 進場前安全攔截

- **選擇**：`price >= lower` 後額外確認 `price > stop_loss_price` 才標 active
- **原因**：AI 偶爾誤將 stop_loss 設在買入區間下限以上（如 buy_zone $45-$50，stop_loss $47），若不攔截，股價落在 $46 時會被標為 active 但實際已在止損下方，後續結算為立即停損，污染績效資料庫。此攔截對反轉策略是雙重保護（頂部已有 `price < stop_loss → invalid`），對動能/突破策略則補上了缺失的進場前核查。
- **捨棄**：只靠頂部失效條件攔截（動能/突破策略頂部只有 ema20 檢查，不覆蓋 stop_loss）

### DD-8: holding_days 使用交易日計算（active_days 計數器優先）

- **選擇**：`_archive_to_performance_history` 的 `holding_days` 以 `active_days` 計數器為主；計數器為 0 或缺失時才以 `_count_trading_days(start, end)` 補算（僅計週一至週五）
- **原因**：`active_days` 計數器每次 CI 執行（週一至週五）遞增一次，天然等同交易日數；舊做法使用 `exit_date - active_start_date` 的日曆天差，週五進場、次週五出場會記成 7 天而非 5 個交易日，與 publisher 顯示不一致。
- **捨棄**：日曆天差（`(exit_date - active_start).days`）作為 primary（包含週末，語意不符「持倉幾個交易日」）

### DD-6: active 部位生命週期改由 _check_settlement() 接管

- **選擇**：`_is_expired()` 對 active 狀態一律回傳 `False`；active 的結算完全由 `_check_settlement()` 的 CLOSED_PROFIT / CLOSED_LOSS / FORCE_EXPIRED 控制
- **原因**：`_is_expired()` 只能判斷「時間到期」，無法觸發停利/停損邏輯；讓兩套機制同時運作會有 double-exit 風險（先被 `_is_expired()` 移除，再被 `_check_settlement()` 嘗試歸檔 → KeyError）
- **捨棄**：`_is_expired()` 同時適用 active（兩套機制競爭，造成邏輯混亂）

### DD-9: active 持倉再入選時不重置交易計劃

- **選擇**：`existing[sym].status == "active"` 時，跳過 `update(base)`，不加入 `reset_symbols`，讓該部位繼續出現在 `categories["active"]`，沿用原有的 `stop_loss / target / hold_period / active_entry_price`
- **原因**：AI 再選同一股語意為「持倉觀點不變」；若允許重置則 `active_days` 歸零、`active_entry_price=None`，`hold_period` 永不觸發，`performance_history.json` 被污染
- **捨棄**：加入 `reset_symbols`（active 從 `categories["active"]` 消失，publisher 看不到持倉）；mid-trade 更新 stop_loss/target（系統缺乏明確「調單」語意，且會干擾績效計算）

### DD-10: 日內高低點實質結算（含拆股免疫聯動）

- **選擇**：`_check_settlement` 改用 `today_low ≤ stop_loss` 觸發 CLOSED_LOSS（出場價=stop_loss）、`today_high ≥ target` 觸發 CLOSED_PROFIT（出場價=target）；黑天鵝（同日雙觸發）優先判為 CLOSED_LOSS；拆股時以 `adj`（同時縮放 stop_loss 和 target）呼叫結算，確保出場價為拆股後的正確絕對值
- **原因**：盤中觸價單（Stop/Limit Order）以 High/Low 為準，純收盤判定會錯失盤中止損事件、低估停利觸發率、且拆股後原始止損值失準；NaN fallback 為 close 可防止停牌股觸發停損免疫 Bug
- **捨棄**：純 close 結算（落後實質觸價，績效系統性高估）；不縮放 target（split 後 exit_price 偏高，虛胖績效）

### DD-11: 執行順序強制約束 + 基準日錨定

- **選擇**：`run_tracker()` 重構為 D（下載現有持倉）→ E（評估現有持倉）→ B/C（處理新訊號）；`today` 改由 `market_date` 參數注入（由 pipeline 從 `price_data["SPY"].index[-1].date()` 提取），有值時使用，否則 fallback 為 `date.today()`
- **原因**：原順序 B/C→D→E 導致：(1) 新選股用同日 close 立即評估，缺少 1-day lag；(2) watch 狀態的舊個股在評估前被覆蓋為新 AI 參數，可能因買入區間不同而錯失進場。`date.today()` 在本地 CST 早晨執行與 CI UTC 盤後執行可能相差一天，導致 `is_rerun` 機制與 `date_added` 異常
- **捨棄**：在 B/C 後對新加入條目設 skip flag（增加複雜度）；繼續用 `date.today()`（時區漂移，本地/CI 行為不一致）

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
        "actual_exit_price", // CLOSED_PROFIT→target；CLOSED_LOSS→effective_stop_loss；CLOSED_TRAILING_STOP/FORCE_EXPIRED→close
        "exit_reason",       // CLOSED_PROFIT / CLOSED_LOSS / CLOSED_TRAILING_STOP / FORCE_EXPIRED
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
- [ ] CLOSED_PROFIT：手動將 today_high 設為高於 target → 觸發歸檔，`actual_exit_price = target`（非 today_high）
- [ ] CLOSED_LOSS：手動將 today_low 設為低於 stop_loss → 觸發歸檔，`actual_exit_price = stop_loss`
- [ ] 黑天鵝：today_low ≤ stop_loss 且 today_high ≥ target 同時成立 → 結算為 CLOSED_LOSS
- [ ] FORCE_EXPIRED：手動將 `active_days` 設為等於 `hold_period` 解析值 → 觸發歸檔，`actual_exit_price = close`
- [ ] 歸檔後 `performance_history.json` 有新紀錄，`return_pct` 計算正確，`is_win` 符號正確
- [ ] `entry_regime` 有值（非空字串），來自 `market_context["regime"]`
- [ ] `actual_entry_price` = 首次轉 active 當日的收盤價（非 buy_zone_lower）
- [ ] 反轉策略股票（strategy="反轉策略"）：`price=179, stop_loss="$180"` → invalid；`price=175, ema20=176` → **仍依 stop_loss 判斷**，不依 EMA20
- [ ] 動能策略股票：`price < ema20` → invalid，即使 `price > stop_loss`
- [ ] 拆股模擬：`signal_date_close=300`，close_series 信號日顯示 100 → `split_factor≈0.333`，門檻等比例縮小，stop_loss 與 target 均縮放
- [ ] **1-day lag**：今日新選股（全新 sym），當日 close 已在買入區間 → 仍保持 `watch`，不立即 `active`
- [ ] **Active 免疫**：active 持倉再次出現在 L3 選股 → status/active_days/active_entry_price 完整保留，不被重置
- [ ] **Watch 覆寫**：watch 個股再次出現在 L3 選股 → watch_days 歸零，date_added 更新為今日，AI 參數刷新
- [ ] **停牌 NaN**：today_high/today_low 為 NaN 的股票 → 不觸發結算（fallback 為 close，stop_loss/target 條件不滿足）
- [ ] **保本鎖定**：`active_entry_price=100, target=120, buy_zone_upper=105`，收盤 110 時 `effective_stop_loss` 自動更新為 105，`is_breakeven_locked=true`；次日收盤 112 時不再重複上移
- [ ] **移動停利**：`highest_close_since_active=115`（>10% 盈），收盤跌至 109（≥5% 回撤）→ 結算 `CLOSED_TRAILING_STOP`，`exit_price=109`（close），`is_win=true`（正回報）
- [ ] **反轉策略排除**：反轉股滿足回撤條件 → 不觸發 `CLOSED_TRAILING_STOP`
- [ ] **開倉當日停損**：新進股首次轉 active 當日 today_low 觸及 stop_loss → 正常觸發 CLOSED_LOSS，`return_pct` 為負數（非 inf）
