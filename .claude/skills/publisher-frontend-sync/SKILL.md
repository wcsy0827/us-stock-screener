---
name: publisher-frontend-sync
description: 觀察到以下任一狀態時載入：diff 涉及 src/publisher.py 或任何會改變 L1/L2/L3 定義、Regime 邊界、狀態機規則的模組；docs/ 下檔案出現在 git diff；test_publisher_info_sync 紅燈；報告頁面顯示與系統實際行為不符。
---

# publisher 與前端同步義務

事實時間戳：2026-07-13。

## 鐵律：docs/ 是生成物

`docs/index.html`、`docs/reports/*.html`、`docs/data/*.json` 全部由 `publisher.py` 生成，**手改必被下次執行覆蓋**。改前端 = 改 `publisher.py` 的模板（`_CSS` `src/publisher.py:148`、`_INFO_HTML` `src/publisher.py:865`、各 section 函式）。

## 規則：改了篩選/評分/策略/狀態機 → 檢查 _INFO_HTML

**觸發**：diff 影響 L1/L2/L3 定義、評分條件、Regime 邊界、狀態機轉換規則任一項（即使你只改了 `scorer.py` 或 `tracker.py`，沒碰 publisher）。
**步驟**：①讀 `_INFO_HTML` 找對應描述（篩選流程、L2 評分表、Regime 表、訊號追蹤狀態）；②過時就同 commit 更新；③執行 `python src/publisher.py` 再生成 `docs/index.html`；④`pytest` 確認全等守門綠。
- 正例（DD-19 實例）：ranker 加了 ATR14 欄 → 原以為「不動 _INFO_HTML」，經使用者檢視發現 L3 輸入矩陣清單已不完整 → 追加同步。**教訓：判斷「要不要同步」時去讀 _INFO_HTML 實際列了什麼，不要憑印象。**
- 反例（DD-19 當時的原始判斷，後被修正）：「這次只是候選池加一欄，前端說明不受影響。」——_INFO_HTML 恰好逐欄列舉了輸入矩陣。
**完成定義**：`git show --stat` 同時含來源模組、publisher.py（若有改）、docs/index.html；pytest 綠。

## 規則：sync_index 是唯一的再生成入口

`python src/publisher.py`（觸發 `sync_index()`，`src/publisher.py:1047`）是離線確定性再生成——只嵌靜態字串，不下載、不呼叫 API、輸出固定 LF。改了 `_INFO_HTML`/`_CSS`/script 邏輯後跑這一條 + 同 commit。全等守門測試（`tests/test_publisher_info_sync.py`，整檔逐字元比對）會攔下漏做的同步。

## 報告呈現的行為契約（改顯示邏輯前確認）

- active 條目顯示 `effective_stop_loss`（動態止損，非 AI 原始值），保本鎖定附「🔒保本」；動能/突破峰值浮盈 ≥10% 另顯示移動停利線，反轉策略精確排除（DD-7，與 tracker DD-13 口徑一致）。**使用者照報告數字下單，顯示錯的止損 = 使用者用錯門檻**。
- watch/invalid 的「剩 N 天」呼叫 `tracker._max_watch_days()`，不得在 publisher 內重抄查表。
- 明日掛單計畫區段（DD-9）= `compute_order_plan()` 的前端視圖：排名、✅建議掛單/⏸備援、掛單價（buy_zone_upper）、止損、目標、信心、L2、剩餘天數；名額 0 顯示「持倉已滿，明日不建議掛新單」。
- 冷啟動保護：`performance_history.json` 不存在或空陣列時績效區塊回零值隱藏，不得 ZeroDivisionError（DD-6 tracker 側要求）。
- `_INFO_HTML` 只寫靜態文字，不插值 runtime 常數（全等測試在不同 `.env` 環境會誤紅）。

## 驗證方式

模板改動的最低證據：`python src/publisher.py` + `pytest` + 實際開頁面（`cd docs; python -m http.server 8080`）看渲染結果——tooltip div→span 事故（commit a7bb69b）證明無效 HTML 只有瀏覽器會告訴你。

再驗證（PowerShell）：`python src/publisher.py; python -m pytest tests/test_publisher_info_sync.py -q`（預期：無 diff、測試綠）
