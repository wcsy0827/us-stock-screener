---
name: tracker-state-machine
description: 觀察到以下任一狀態時載入：diff 涉及 src/tracker.py；watchlist 條目狀態轉換不符預期；需要新增結算/風控/進場規則；績效歸檔數字可疑；要為 tracker 補測試。
---

# tracker.py 深水區導航

事實時間戳：2026-07-13（函式行號以當日原始碼驗證）。改任何判斷邏輯前先讀 `specs/tracker.md` 完整版；此檔是地圖，不是規格替代品。

## 函式地圖（src/tracker.py）

| 函式 | 行 | 職責 | 關鍵 DD |
|---|---|---|---|
| `check_already_run_today()` | 57 | UTC 日期防重跑 | 決策 12 |
| `_parse_hold_period()` | 67 | AI 輸出解析，**下界 1**（≤0 會同日進場即 FORCE_EXPIRED） | DD-19 |
| `_eval_status()` | 207 | watch 期狀態機；active 短路；`today_low<=buy_zone_upper` 觸價優先於一切收盤判定 | DD-17/19 |
| `_check_settlement()` | 289 | 四態結算（日內 High/Low 實質觸價；黑天鵝同日雙觸發判 LOSS） | DD-10/13 |
| `_slot_priority_key()` | 489 | `(-ai_confidence, -l2_score, symbol)`，缺值視 0 | DD-20 |
| `compute_order_plan()` | 501 | 純函式：free_slots/roster/eligible；不持久化 | DD-20 v2 |
| `_max_watch_days()` | 523 | 策略×entry_regime×vix 查表（5/10/3/5 日）；publisher 直接 import | DD-15/16 |
| `run_tracker()` | 552 | 順序固定 D→E→B/C；`today` 由 market_date 注入 | DD-11 |

## 修改檢查清單（每一項都對應一次真實迴歸）

1. **執行順序 D→E→B/C 不可調換**：新選股當輪不評估（1-day lag 是刻意的），E 開頭算一次 `eligible` 名單。
2. **同日冪等**：`tracked_dates` 的 `already_tracked_today` 旗標與 `watch_days`/`active_days` 遞增**共用同一判斷式**（DD-18）。新增任何「每日 +1」的計數器都要掛進同一守衛，否則同日重跑虛增。
3. **新增絕對價格欄位 → 進拆股縮放清單**（見 architecture-contract 第 7 條）。臨時縮放不寫回。
4. **DD-19 的 dormant 分支不得清理**：`_eval_status()` 內「同日觸價又跌破止損 → 回傳 active 交給結算判 CLOSED_LOSS」路徑，以及原 DD-7 跳空攔截分支，因 DD-20 blocked 重跑（`today_low=None`）而**重新可達**——靜態分析看似死碼，實際是活的。
   - 反例（清理時會出現的合理化）：「這個分支在 DD-19 之後不可達了，刪掉讓函式更短。」——DD-20 名單外條目會以 `today_low=None` 重跑 `_eval_status()`，走的正是這些「舊」分支。
5. **`_fetch_latest()` 的 High/Low 必須與 price 同列讀取**（Close 為 NaN 時 `dropna()` 後 price 可能落在前一列），否則 `today_low<=price<=today_high` 恆等式破裂（DD-19 同批修正）。
6. **每條新判斷邏輯 → `tests/test_tracker.py` 新案例**（fail-then-pass）。既有 108 個測試函式全離線、經 `isolate_data_dir` 隔離，不碰真實 data/。
7. **不與 scorer.py 同 PR**。

## 風控欄位語意（混用 = 污染帳本）

- `planned_stop_loss`：AI 原始值，唯讀，拆股基底。
- `effective_stop_loss`：動態止損，保本鎖定後上移至 `buy_zone_upper`；結算與報告都用它。
- `is_breakeven_locked`：明示旗標，防浮點抖動重複觸發。
- `highest_close_since_active`：**原生未拆股標尺**存儲。
- 進場代理價 = `buy_zone_upper`（使用者實際掛單價；`min(開盤, upper)` 方案經抗辯審查否決——開盤異常值會污染 return_pct）。
- `holding_days` 用 `active_days` 交易日計數器，不用日曆天差（DD-8）。
- `is_win` 純看 `return_pct > 0`，與出場原因解耦（DD-13）。

## 進場資格 = 事前名單（DD-20 v2）

只有 `compute_order_plan()` 的 `eligible`（優先序前 free_slots 名）觸價才轉 active；名單外觸價 → 以收盤價重跑 `_eval_status(..., today_low=None)`：失效即作廢（沒掛單=無真實交易），否則維持 watch + `slot_blocked_today=True`（迴圈頂端每日重置）。當日結算不退還名額；超額持倉不強平。**任何「讓更多股票進場」的想法先過帳實一致原則**（architecture-contract 第 4 條）。

再驗證：`python -m pytest tests/test_tracker.py -q`（預期全綠）；行號漂移時用 Grep 工具或 Git Bash 跑 `grep -n "^def " src/tracker.py`
