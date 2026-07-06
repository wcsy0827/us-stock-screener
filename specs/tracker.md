# Tracker — 訊號追蹤規格

## Purpose

追蹤 L3 AI 推薦的個股是否已落入買入區間（active）、仍在等待（watch）、已失效（invalid）、已到期移除（expired），或已觸發結算並歸檔（settled）。活躍清單持久化於 `data/watchlist.json`；歷史績效持久化於 `data/performance_history.json`。

## Behavior

### 狀態機

```
[新加入] → watch
  watch  → active   （次日 today_low <= buy_zone_upper 即視為觸價成交，1-day lag，DD-19）
  watch  → invalid  （失效條件觸發，僅當今日未觸價成交時才可能發生）
  watch  → expired  （watch_days >= 策略對應 watch 上限：突破/動能=5日，反轉=10日）
  active → settled  （CLOSED_PROFIT / CLOSED_LOSS / CLOSED_TRAILING_STOP / FORCE_EXPIRED，歸檔至 performance_history.json）
  invalid → expired （_days() >= 策略對應 watch 上限）
```

**active 部位不再由 `_is_expired()` 到期，也不再由 `_eval_status()` 判定失效**，完整生命週期只交給 `_check_settlement()`（DD-17）。

- **必須**：watch 和 active 使用**分開的計數器**（`watch_days` / `active_days`），不能共用總追蹤天數
- **不得**：active 持倉到期上限使用固定的 5 日；必須讀取 AI 指定的 `hold_period` 字串並解析
- **必須**：同一天重複執行時（`is_rerun`），清除當日新增的股票後重新加入，已有的跨日追蹤股票不受影響

### 失效條件（雙軌制）

依 `strategy` 欄位區分：

| strategy | 失效門檻 |
|----------|----------|
| `"反轉策略"` | `price < stop_loss 絕對價` |
| 其他（動能/突破）| `price < EMA20` |

- **必須**：`_eval_status()` 對 `status == "active"` 的條目一律直接短路回傳 `("active", None)`，不執行下方任何失效判定（見 DD-17）。追高失效、反轉止損、EMA20 判定等條件僅適用 watch 狀態
- **不得**：對反轉策略使用 EMA50 作為失效門檻（見 DD-1）
- **必須**：`today_low <= buy_zone_upper` 的觸價成交判定（DD-19）嚴格位於 invalid/active 短路之後、其餘所有收盤價判定之前

### 狀態機下限判定順序（僅適用 watch 狀態；active/invalid 已於函式頂部短路）

```
status == "invalid"?                         → invalid（不重新判定）
status == "active"?                          → active（不判定，交給 _check_settlement）
today_low <= buy_zone_upper?                 → active（DD-19：盤中觸價成交，優先於下方一切收盤價判定）
─── 以下僅在今日未觸價成交（today_low > upper，或未提供 today_low）時才會執行 ───
price < stop_loss?                           → invalid（反轉策略；正常參數配置下此分支實務不可達，見 DD-19）
price < ema20?                               → invalid（動能/突破策略；同上，實務不可達）
price > upper * 1.08?                        → invalid（追高）
price > upper * 1.01?                        → watch（等回落）
price >= lower 且 price <= stop_loss?        → invalid（開盤跳空安全攔截，DD-7，已被 DD-19 取代並列為 dormant）
price >= lower 且 price > stop_loss?         → active（進場；正常參數配置下實務不可達，僅作未提供 today_low 時的向下相容路徑）
price < lower 且 price >= stop_loss?         → watch（繼續觀察）
price < lower 且 price < stop_loss?          → invalid
```

- **必須**：`watch → active` 轉換前，需額外確認 `price > stop_loss`，防止 AI 誤設止損在買入區間內時造成績效污染（DD-7，已被 DD-19 取代，見下方 DD-19 說明）

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

- **必須**：首次加入 watchlist（B/C 步驟建立條目當下）就直接寫入 `signal_date_close = stock["price"]`（L3/L2 訊號日收盤價），**不得**延遲到下一輪評估時才用「當時的收盤價」回填（見 DD-17）
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
    High/Low 與 price 取自同一列（price_date），避免 Close 缺值時 dropna
    導致日期錯位；NaN 時 fallback 為 close，確保結算邏輯不受停牌股影響（DD-19）。
    """

def _eval_status(
    entry: dict,
    price: float,
    ema20: float | None,
    ema50: float | None = None,
    today_low: float | None = None,
) -> tuple[str, str | None]:
    """
    評估訊號狀態。today_low 提供且 <= buy_zone_upper 時，優先視為當日觸價
    成交（DD-19，盤中限價單模擬），優先於下方一切收盤價判定；為 None 或
    未觸價時完全退化為 DD-19 之前的收盤價判定邏輯。
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
    """解析 "5-10 個交易日" → 取最大值（10）；無法解析回傳 default。
    下界固定為 1，AI 給出 <=0 的異常值時夾在 1（DD-19）。"""

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
- **DD-19 更新**：進場代理價已改為 `buy_zone_upper`（使用者實際掛限價單的價位），不再是收盤價，見 DD-19。

### DD-7: watch → active 加入 stop_loss 進場前安全攔截

- **選擇**：`price >= lower` 後額外確認 `price > stop_loss_price` 才標 active
- **原因**：AI 偶爾誤將 stop_loss 設在買入區間下限以上（如 buy_zone $45-$50，stop_loss $47），若不攔截，股價落在 $46 時會被標為 active 但實際已在止損下方，後續結算為立即停損，污染績效資料庫。此攔截對反轉策略是雙重保護（頂部已有 `price < stop_loss → invalid`），對動能/突破策略則補上了缺失的進場前核查。
- **捨棄**：只靠頂部失效條件攔截（動能/突破策略頂部只有 ema20 檢查，不覆蓋 stop_loss）
- **DD-19 取代**：此機制在 DD-19 之後已列為 dormant——盤中觸價成交判定優先於此分支，若同日觸價又跌破止損，直接視為當日進場並交由 `_check_settlement()` 立即結算 CLOSED_LOSS，而非拒絕進場、不留紀錄。程式碼保留此區塊作為未提供 `today_low` 時的向下相容路徑，見 DD-19。

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

### DD-12: 風控雙欄位（planned_stop_loss + effective_stop_loss）

- **選擇**：`planned_stop_loss`（float）為 AI 原始值，唯讀，專作 DD-3 拆股基底；`effective_stop_loss`（float）為動態止損；`is_breakeven_locked`（bool）保本鎖定旗標
- **原因**：保本鎖定後 `effective_stop_loss` 上移至 `buy_zone_upper`，若僅存一個欄位，拆股縮放基底不明確，累積除法誤差；分離後各司其職
- **捨棄**：單欄位 stop_loss（拆股後基底不可信）

### DD-13: 全自動保本鎖定與移動停利

- **選擇**：進場後收盤達目標距離 50% 時自動鎖定保本（`effective_stop_loss` 上移至 `buy_zone_upper`）；動能/突破策略峰值浮盈超過 10% 後收盤回撤 5% 觸發 `CLOSED_TRAILING_STOP`
- **原因**：手動設定保本點容易遺漏；移動停利鎖定已兌現部分利潤，防止高峰回吐
- **捨棄**：固定停利（錯失更大上漲）；無保本鎖定（V型反彈後止損在買入區間以下，部位歸零）

### DD-14: ai_confidence 最低門檻過濾（tracker B/C 步驟）

- **選擇**：B/C 步驟處理 new_ranked 時，`confidence < MIN_AI_CONFIDENCE`（預設 6）的個股直接跳過，不加入 watchlist
- **原因**：`ranker.py` 已要求 AI 提供 1-10 的信心分數，存入 watchlist 的 `ai_confidence` 欄位，但從未用於任何過濾。信心 5 分以下的選股屬於 AI 自評「不確定」的狀況，加入 watchlist 卻無法區分處理，等同白收數據。門檻設 6（預設），可透過 `MIN_AI_CONFIDENCE` env var 調整。`ranker.py` 預設 fallback 為 5，因此設 6 意味著 AI 明確給分低於 6 或未給分的選股均被過濾。
- **欄位路徑**：`ranker.py` 回傳的 dict 中為 `"confidence"`（int）；tracker 讀取時使用 `stock.get("confidence") or 0`，None/缺失均視為 0（不通過）
- **捨棄**：在 ranker.py 過濾（使架構耦合）；無門檻保持現狀（信心數據形同虛設）

### DD-15: 依策略差異化 watch 天數上限

- **選擇**：以 `_WATCH_DAYS_BY_STRATEGY` 字典 + `_DEFAULT_WATCH_DAYS=5` 替換 `MAX_WATCH_DAYS=5` 常數；`反轉策略` 對應 10 日，其餘策略維持 5 日
- **原因**：突破/動能策略的進場信號具時效性，超過 5 天未突破則 setup 失效，5 日合理；反轉策略的底部確認需要更長時間醞釀（底部整理、拋壓衰竭），5 日 expired 太早，常在真正反彈前就被移除。差異化 watch 上限讓兩類策略各取所需。
- **`_max_watch_days(entry)` 函式**：讀取 `entry["strategy"]`，查表回傳對應上限，查無則回傳 `_DEFAULT_WATCH_DAYS`
- **捨棄**：統一 5 日（對反轉策略過短）；統一 10 日（突破/動能 setup 過期後 5 天仍佔用 watchlist）

### DD-16: watch 天數上限疊加訊號當下 regime/VIX 條件

- **選擇**：`_max_watch_days()` 在 DD-15 策略查表之上疊加兩條 regime/VIX 條件分支：`突破策略` 且訊號當下 `entry_regime == "CONSOLIDATION_VOLATILE"` → 3 日；`反轉策略` 且訊號當下 `entry_regime == "PANIC_REVERSAL"` 且 `vix_value > 35` → 5 日；其餘沿用 DD-15 既有查表結果（含反轉策略 VIX 25~30 或未落入以上分支的所有情況，維持 10 日）
- **原因**：高波動整理市（CONSOLIDATION_VOLATILE）中帶量突破前高的假突破機率明顯升高，3 天內無法穩住價格即高機率退化為高位套牢，縮短觀察期讓風控更敏銳；VIX > 35 對應流動性擠壓式尖底，優質股錯殺後的 V 型反彈通常數日內兌現，5 天仍未進場，代表個股可能有未引爆的基本面問題（非單純大盤恐慌錯殺），10 天的等待期在此情境下反而暴露於接刀風險
- **判斷基準用訊號當下鎖定值**：兩條件均讀取 `entry["entry_regime"]`／`entry["vix_value"]`（於 B/C 步驟建立條目時寫入，見上方 `base` dict），不用每日重新查詢的當下 regime，與 `buy_zone`/`stop_loss`/`target` 等訊號特徵鎖定的既有慣例一致，避免同一檔股票的 watch 上限在追蹤期間內隨當下 regime 波動
- **既有條目相容性**：不做版本判斷或遷移，`data/watchlist.json` 中所有現存 `watch` 狀態條目立即套用新規則——`entry_regime`／`vix_value` 早於本次改動就已寫入既有條目，新規則生效當下即可直接讀取
- **捨棄**：改用每日重新評估的當下 regime（會讓 watch 上限在追蹤期間內波動）；以 `date_added` 判斷新舊條目、僅對部署後新訊號生效（既有欄位已支援新規則，無需遷移邏輯）
- → 詳見 `plans/2026-07-03-watch-days-regime-vix.md`

### DD-17: active 部位失效判定移除 + signal_date_close 訊號日即時寫入

- **選擇（缺陷 1）**：`_eval_status()` 在 `status == "invalid"` 短路之後，新增 `status == "active"` 短路，直接回傳 `("active", None)`，不再對 active 部位執行策略失效判定（反轉 `price < stop_loss`、動能/突破 `price < ema20`）與追高失效。active 部位的生命週期完全交給 `_check_settlement()` 的四態結算。
- **原因**：`run_tracker()` 的 E 步驟在同一輪迭代中先呼叫 `_eval_status()` 並立即寫回 `entry["status"]`，再呼叫 `_check_settlement()`；後者開頭即檢查 `status != "active"` 直接跳過。結果是 active 部位一旦被 `_eval_status()` 判定失效，狀態被翻成 `invalid`，`_check_settlement()` 永遠跳過該筆，虧損不會被歸檔至 `performance_history.json`，之後經 `_is_expired()` 無聲移除。以反轉策略為例：`price < stop_loss` 必然隱含 `today_low ≤ price < stop_loss`，即 DD-10 的盤中止損（`today_low ≤ effective_stop_loss`）必定同時成立，代表 `_check_settlement()` 本可正確結算為 `CLOSED_LOSS`，卻被 `_eval_status()` 搶先攔截。這也是 spec 舊版狀態機圖 `active → invalid（跌破止損但尚未達結算門檻）` 條目在 DD-10 之下自相矛盾、不可達的根因：只要收盤已跌破止損，盤中最低點必然也已跌破，DD-10 的止損判定必先觸發。後果是 `performance_history.json` 系統性漏記虧損交易，`analyzer.py` 的勝率統計向上偏差，回饋進 L3 Prompt 的歷史績效回顧因而失真樂觀。
- **動能股殘留風險與取捨**：移除失效判定後，動能/突破策略持倉「收盤跌破 EMA20 但盤中止損未觸」時不再提前標記失效，而是持續 active 直到止損、停利、移動停利或 `hold_period` 到期四者之一觸發，屬有界（受 `hold_period` 限制，非無限期滯留）。此行為與使用者「僅依止損/停利區間出場、不依當下均線位置」的實際操作方式一致，故不額外補上「趨勢轉弱提前出場」機制。
- **選擇（缺陷 2）**：`run_tracker()` B/C 步驟建立 `base` 字典時，`signal_date_close` 直接寫入 `stock.get("price")`（L2/L3 訊號日收盤價），不再留空由下一輪評估時回填。原本 E 步驟的回填邏輯（`if entry.get("signal_date_close") is None`）保留作為存量條目的 fallback。
- **原因**：`_calc_split_factor()` 用 `entry["tracked_dates"][0]`（訊號日）作為比對錨定日，但 `signal_date_close` 舊實作是在**次一輪評估**（訊號日的次一交易日）才寫入當日收盤價，兩者錯開一個交易日。只要訊號日到次日的正常漲跌幅超過 ±1%（`abs(split_factor - 1.0) > 0.01` 的門檻），就會被誤判為拆股，`buy_zone`/`stop_loss`/`target`/`effective_stop_loss` 全數被錯誤縮放，可能讓已進場（active）部位被翻回 `watch`，或讓 `effective_stop_loss` 被誤縮小而使真實止損事件在 DD-10 的比對中漏判（與缺陷 1 的漏記虧損風險疊加）。`ranker.py` 回傳的候選股 dict 本就含訊號日 `price` 欄位（L2 `score_stock()` 產出），無需額外下載即可在建立條目當下寫入正確值。此欄位透過 B/C 共用的 `base` 字典寫入，同時涵蓋全新個股與 watch/invalid 重置覆寫兩條路徑（`existing[sym].update(base)`），修復 reset 路徑會把 `signal_date_close` 覆寫回 `None`（因而延續次日回填錯位）的問題。
- **不修復存量資料**：既有 `data/watchlist.json` 條目的 `signal_date_close` 若為舊邏輯寫入的錯位值，不做一次性 migration；此系統目前處於冷啟動期（尚無 active 部位、`performance_history.json` 尚未產生），存量 `watch`/`invalid` 條目會在數個交易日內依既有 watch 上限自然到期，無需回溯修正。
- **捨棄**：讓 active 部位觸發失效時改以收盤價強制結算歸檔（等同新增第二套結算路徑，與 `_check_settlement()` 職責重疊，複雜化生命週期管理）；把 `_calc_split_factor` 的錨定日從 `tracked_dates[0]` 改為 `tracked_dates[1]`（治標且該索引在條目第一輪評估時尚不存在，需額外邊界處理，比直接修正寫入時機更脆弱）。

### DD-18: 同日重跑不得重複遞增 watch_days/active_days

- **選擇**：`run_tracker()` 的 E 步驟在附加 `tracked_dates` 前，先讀出 `already_tracked_today = today in entry["tracked_dates"]`；`tracked_dates.append(today)` 沿用既有的「未含今日才附加」去重邏輯，但計數器遞增（`watch_days += 1` / `active_days += 1`）額外加上 `if not already_tracked_today` 守衛，讓兩者的去重判斷共用同一個旗標，維持單一事實來源。
- **原因**：`tracked_dates` 本身已正確去重（`if today not in entry["tracked_dates"]`），但緊接著的計數器遞增沒有比照守衛，是兩段邏輯各自為政、未同步更新的遺漏。使用者的實際操作習慣是收盤後執行一次，但偶爾會在本機重跑核對（`main.py` 對同日重跑僅詢問是否繼續，`--yes` 略過確認），一旦選擇繼續執行，`watch_days`/`active_days` 會在同一天內被多次遞增。後果：`active_days` 可能提前抵達 `hold_period` 觸發 `FORCE_EXPIRED`（同一天內連跑兩次即可能少算一個完整交易日就強制出場），`_max_watch_days()` 的到期判斷同樣受影響；`_archive_to_performance_history()` 的 `holding_days`（DD-8，優先取 `active_days` 計數器）因而失真，多算的重跑次數會被誤記為多出的交易日。
- **不影響的相鄰邏輯**：`_apply_risk_controls()`（保本鎖定、`highest_close_since_active` 更新）與 `_check_settlement()` 本身即為冪等或以「是否創新高」/「是否已鎖定」判斷，同日重跑重複呼叫不會累積誤差，故不需要疊加 `already_tracked_today` 守衛；新加入的 watch 個股（`base` 字典建立時 `watch_days=0`）不受影響，因為新條目在 B/C 步驟建立、不進入本輪 E 步驟迴圈。
- **捨棄**：另外新增一個 `_last_counted_date` 欄位或旗標（重複 `tracked_dates` 已有的資訊，違反單一事實來源）；在 `run_tracker()` 開頭偵測 `is_rerun` 後直接整批跳過 E 步驟（會連帶跳過本應執行的 `_check_settlement()`，同日內若股價已觸發止損/停利也無法即時反映）。

### DD-19: 盤中限價單模擬進場（觸價優先於收盤價判定）

- **背景**：使用者的實際操作方式是收盤後跑選股，次一交易日盤中依 AI 給的買入區間掛限價單，價位設在區間**上緣**（`buy_zone_upper`）。原本 `_eval_status()` 只認收盤價：股價盤中回落到區間、使用者的限價單已經成交，但收盤又彈出區間上緣以上時，系統仍判 `watch`（等回落）；若隔日續漲超過 8%，系統甚至會判「已追高，錯過買點」而移除，使用者手上的真實部位從此不被追蹤，`performance_history.json` 記錄的也不是使用者的真實交易。
- **選擇**：`_eval_status()` 新增 `today_low: float | None = None` 參數，在 `status=="invalid"`/`"active"` 短路之後、其餘所有判定之前，插入一行檢查：`if today_low is not None and today_low <= entry["buy_zone_upper"]: return "active", None`。此檢查優先於下方所有以收盤價為準的判定（反轉止損失效、動能 EMA20 失效、追高失效）。今日未觸價（`today_low > upper`）或呼叫端未提供 `today_low`（`None`）時，完全退化為插入前的原始邏輯，逐行為不變——**下方所有原本分支（含 DD-7 的開盤跳空攔截、`lower` 相關判斷）全部保留，不刪除**，僅在正常情境下變成實務不可達的 dormant 程式碼，作為 AI 給出異常參數或呼叫端未升級時的防禦網。
- **同日跳空穿越止損的處理**：若同日觸價成交、`today_low` 也同時跌破止損（例如跳空急殺直接開盤在止損之下），`_eval_status()` 仍直接回傳 `active`；`_check_settlement()`（無需任何修改）會在同一輪迭代內立即以 `today_low <= effective_stop_loss` 判定 `CLOSED_LOSS`，比照既有 DD-10 黑天鵝保守原則同日結算歸檔。此為使用者明確選擇的處理方式（保守記為真實交易），取代 DD-7 原本「拒絕進場、完全不留紀錄」的做法——後者與 DD-17 已修復的「虧損繞過結算」屬同一類缺陷（真實經濟事件未被記錄）。
- **進場代理價改為 `buy_zone_upper`**：不再是收盤價（DD-5 原始選擇），而是使用者實際掛單的價位，拆股情境下讀取已由 `split_factor` 縮放的 `settlement_entry["buy_zone_upper"]`，與 `active_entry_price` 既有「以當下現值標尺存儲」的慣例一致。**不使用 `min(今日開盤, buy_zone_upper)`**：抗辯審查中發現此方案需額外抓取 `today_open` 欄位，換來的精確度僅在「開盤即跳空至限價之下」的罕見情境才有意義，卻引入開盤價異常值（熔斷/停牌）污染 `return_pct` 的風險；`buy_zone_upper` 是 AI 輸出、已由 `_parse_buy_zone()` 驗證過的乾淨數值，無此風險。
- **前置修正：`_fetch_latest()` 的 High/Low 讀取列對齊**：原本 `price` 取自 `df["Close"].dropna().iloc[-1]`（若最後一列 Close 為 NaN 會回退至前一列），但 `today_high`/`today_low` 卻不論 Close 是否為 NaN，一律取 `df["High"/"Low"].iloc[-1]`（literal 最後一列）。當最後一列 Close 缺值時，`price` 與 `today_high`/`today_low` 會來自不同日期，破壞 DD-19 的觸價判定所依賴的 `today_low <= price <= today_high` 恆等式。修正為 `high_raw = df["High"].loc[price_date]`（`price_date = close.index[-1]`），確保三者一律取自同一列。
- **`_parse_hold_period()` 加下界 1**：DD-19 讓「當日觸價即成交」變常態（`active_days` 首輪即為 1），若 AI 給出 `hold_period<=0` 的異常值，`_check_settlement()` 的 `active_days >= hold_limit` 會讓剛成交當天就被誤判 `FORCE_EXPIRED`。修正為所有解析路徑（int/float/字串正規表達式）皆套用 `max(1, ...)`，單點根治所有呼叫端。
- **與 DD-4（追高保護）的交互（記錄，非缺陷）**：若股票跳空暴漲穿越整個買入區間，但當日最低價（開盤前）仍曾 `<= upper`，DD-19 判定為已成交（真實限價單確實會在該價位成交），優先於 DD-4 的「已追高，錯過買點」分類。這是 DD-19 相對 DD-4 的刻意行為變更，非誤判——真實限價單不在乎後續股價暴漲，成交當下即已成交。
- **與 DD-15/16（watch 到期上限）的交互（記錄，非缺陷）**：一支收盤已跌破 EMA20（趨勢崩壞）但盤中最低價從未觸及買入區間上緣的動能股，在 DD-19 之後不會提前被 `_eval_status()` 判 `invalid`，而是持續 `watch` 直到 `_max_watch_days()` 到期。此為有界行為（受 watch 上限約束），且與使用者「僅依止損/停利區間出場、不依當下均線位置提前反應」的實際操作方式一致，故不額外補上「趨勢轉弱提前出場」機制（與 DD-17 對 active 部位的既有取捨呼應）。
- **既有 `data/watchlist.json` 存量條目立即套用新規則，不做 migration**：`status=="invalid"` 的短路嚴格位於觸價檢查之前，既有 invalid 條目（例如買入區間已遠低於現價的個股）不會被追溯認定「今日觸價成交」，不受影響（抗辯審查曾提出此疑慮，經確認為虛驚一場，但仍將順序要求明文寫入本規格與 `_eval_status()` 函式頂部註解，避免未來實作變更時因記憶而非約束而出錯）。
- **捨棄**：`min(today_open, buy_zone_upper)` 進場代理價（見上，換取的精確度不敵新增的異常值風險與額外欄位）；把觸價檢查改寫進 `_eval_status()` 既有分支結構內部（改為插入獨立前置檢查，改動面最小、既有 8 個回歸測試與規格全數不受影響）；刪除因觸價檢查而變成事實不可達的舊分支（保留作防禦網成本趨近於零，刪除需同步改規格與重寫測試，且失去異常參數防禦）。
- → 本設計經 skeptic/red-team/simplifier 三方抗辯審查（含 OHLC 恆等式前提驗證、`_fetch_latest` 列對齊缺陷、存量資料相容性、`hold_period` 邊界），最終方案為三方收斂後的最小化版本。

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
- [ ] **DD-14 信心過濾**：confidence=5 的新選股（MIN_AI_CONFIDENCE=6）→ 不加入 watchlist，log 顯示「AI 信心分數 5 < 6，跳過」
- [ ] **DD-14 信心通過**：confidence=6 的新選股 → 正常加入 watchlist
- [ ] **DD-14 信心缺失**：confidence=None/缺失 → 視為 0，不加入 watchlist
- [ ] **DD-15 反轉策略 watch**：strategy="反轉策略" 的 watch 股票追蹤 5 日 → 仍在 watchlist（未 expired）
- [ ] **DD-15 反轉策略 expired**：strategy="反轉策略" 的 watch 股票追蹤 10 日 → 進入 expired
- [ ] **DD-15 突破策略 expired**：strategy="突破策略" 的 watch 股票追蹤 5 日 → 進入 expired（行為不變）
- [ ] **DD-16 高波動突破縮短**：strategy="突破策略"、entry_regime="CONSOLIDATION_VOLATILE" 的 watch 股票追蹤 3 日 → 進入 expired
- [ ] **DD-16 VIX 暴噴反轉縮短**：strategy="反轉策略"、entry_regime="PANIC_REVERSAL"、vix_value=36 的 watch 股票追蹤 5 日 → 進入 expired
- [ ] **DD-16 邊界不變**：strategy="反轉策略"、entry_regime="PANIC_REVERSAL"、vix_value=28 的 watch 股票追蹤 9 日 → 仍在 watchlist；追蹤 10 日 → 進入 expired（維持 DD-15 行為）
- [ ] **DD-17 active 不再被 _eval_status 判定失效**：`_eval_status()` 傳入 `status="active"` 的條目，無論 price/ema20/ema50 為何值，一律回傳 `("active", None)`
- [ ] **DD-17 反轉股虧損不再漏記**：active 反轉策略部位 `price < stop_loss` 且 `today_low ≤ effective_stop_loss` → `run_tracker()` 應產生 `CLOSED_LOSS` 結算並寫入 `performance_history.json`，不應只落入 `invalid` 分類
- [ ] **DD-17 動能股止損未觸時維持 active**：動能策略 active 部位 `close < ema20` 但 `today_low > effective_stop_loss` → 維持 `active`，不進入 `invalid`/`expired`
- [ ] **DD-17 signal_date_close 訊號日即時寫入**：`run_tracker()` 處理新訊號（B/C 步驟）當輪，新條目的 `signal_date_close` 應等於該股 L2 訊號日 `price`，不為 `None`
- [ ] **DD-17 訊號日正常漲跌不誤判拆股**：訊號日與次一評估日之間股價正常波動（漲跌 ≤ 若干 %，未實際拆股），`split_factor` 應 ≈ 1.0，不觸發拆股平移、不影響 active 判定
- [ ] **DD-18 同日重跑不重複遞增 watch_days**：同一 `market_date` 對同一 watch 條目連續呼叫兩次 `run_tracker()`，`watch_days` 只增加 1，不是 2
- [ ] **DD-18 同日重跑不重複遞增 active_days**：同一 `market_date` 對同一 active 條目連續呼叫兩次 `run_tracker()`，`active_days` 只增加 1，不是 2
- [ ] **DD-18 跨日仍正常遞增**：不同 `market_date` 呼叫 `run_tracker()`，`watch_days`/`active_days` 各自正常遞增 1（確認守衛只擋同日重跑，不影響跨日累積）
- [ ] **DD-19 觸價優先於收盤失效判定**：`today_low <= buy_zone_upper` 時，無論收盤價是否已跌破反轉止損、動能 EMA20，或高於追高門檻，`_eval_status()` 皆回傳 `("active", None)`
- [ ] **DD-19 invalid 條目免疫**：`status=="invalid"` 的條目傳入任意 `today_low`，皆回傳原有 invalid 原因，不被觸價檢查追溯復活
- [ ] **DD-19 未提供 today_low 向下相容**：呼叫 `_eval_status()` 不傳 `today_low`（預設 `None`），行為與 DD-19 之前逐字元相同
- [ ] **DD-19 進場代理價為 buy_zone_upper**：`run_tracker()` 首次轉 active 時，`active_entry_price` 應等於 `buy_zone_upper`，不等於當日收盤價
- [ ] **DD-19 同日跳空穿越止損結算為 CLOSED_LOSS**：watch 條目當日 `today_low` 同時 `<= buy_zone_upper` 與 `<= stop_loss`，`run_tracker()` 應產生 `CLOSED_LOSS` 結算並寫入 `performance_history.json`，不應落入 `invalid` 分類
- [ ] **DD-19 前置修正：High/Low 與 Close 同列對齊**：`_fetch_latest()` 遇最後一列 Close 為 NaN 的殘缺列時，`today_high`/`today_low` 應取自 `price` 所屬的同一列，不得誤用殘缺列的異常值
- [ ] **DD-19 hold_period 下界**：`_parse_hold_period(0)` 與 `_parse_hold_period(-5)` 皆回傳 `1`，不回傳 `0` 或負數
