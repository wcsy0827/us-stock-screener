---
name: architecture-contract
description: 觀察到以下任一狀態時載入：準備改動 pipeline 步驟順序、日期/時區處理、tracker 生命週期、或任何跨模組資料流；設計新功能需要決定「這個值該在哪裡算、由誰擁有」；review 一份涉及多模組的 diff。
---

# 架構契約：載重不變量

事實時間戳：2026-07-13。每條不變量背後都有事故或明確 DD，違反任一條即是迴歸。

## 1. 日期錨定：market_date 是唯一時間標籤

- 報告日期、財報牆 cutoff、tracker 的 `today` 全部錨定 `market_date`（= `price_data["SPY"].index[-1].date()`），**絕不用 `datetime.now()`**；防重複執行用 `datetime.utcnow().date()`。本機台灣 UTC+8 與 CI UTC 在**報告日期與防重複判斷**上的行為差異由此消解（注意：`.cache/` 快取 key 用本機系統日期，不在此保證範圍，見 data-and-caches）（CLAUDE.md 決策 12、filter DD-E5、tracker DD-11）。
- 盤中執行時 `trim_incomplete_session()`（`src/fetcher.py:185`）先剪掉殘缺當日 K 棒，market_date 自動回退——所有下游因此拿到的一定是完整收盤數據。

## 2. 步驟順序與指標複用

- Step 2.5（快速 Regime）**必須在 scorer 之前**（L2 門檻依 Regime 動態調整）；其回傳的 `(regime, breadth_pct, vix_value, vix_ok)` 四值直接傳給 Step 5.5 複用，不重算不重下載（pipeline DD-1、market DD-6）。
- `vix_ok=False` 時 pipeline 在 L3 前中斷，不呼叫 DeepSeek——省 API 錢也防在數據殘缺時做決策。

## 3. tracker 生命週期：active 只由結算出場

- `_eval_status()` 對 `status=="active"` 一律短路回傳 active；出場只走 `_check_settlement()` 四態（CLOSED_PROFIT / CLOSED_LOSS / CLOSED_TRAILING_STOP / FORCE_EXPIRED）並歸檔。**「active → invalid」轉換已被刻意移除**（DD-17），恢復它會讓虧損交易在歸檔前被攔截、無聲消失，勝率統計系統性虛高——這是實際發生過的缺陷，不是理論風險。

## 4. 帳實一致原則（本 repo 最高階的產品不變量）

`data/performance_history.json` 必須等於「使用者照報告操作會得到的真實帳本」。使用者流程：收盤後看報告 → 次日盤中依買入區間上緣掛限價單、人工執行停損停利。三次修正全是同一原則的執行：
- DD-17：虧損不得漏記（active 繞過結算）
- DD-19：真實成交不得漏追蹤（盤中觸價、收盤彈出區間）
- DD-20 v2：**沒掛單的交易不得多記**（名單外觸價擋下；v1 事後擇優因此被同日推翻）

**觸發**：任何改動影響「什麼算進場/出場/績效」。**步驟**：先問「使用者照報告操作，真實帳戶會發生什麼？」系統行為必須與那個答案一致。**完成定義**：新行為下不存在「系統記了一筆使用者不可能有的交易」或反之。

## 5. AI 擁有交易計畫，Python 不代算

`buy_zone`/`target`/`stop_loss`/`hold_period` 由 L3 AI 依 Prompt 規則輸出並於訊號日**鎖定**，Python 端只解析（`_parse_buy_zone` 等只認 `"$X"`/`"$X~$Y"`），不確定性計算、不事後修正（ranker DD-12/15 兩度確認的取捨）。修 AI 輸出品質 = 修 Prompt，不是加後處理。進場後的**動態**風控（保本鎖定、移動停利）才是 tracker 的地盤（DD-12/13）。

## 6. 單一事實來源

- publisher 需要 tracker 的常數/邏輯（`MAX_ACTIVE_POSITIONS`、`TRAILING_ACTIVATION_PCT`、`_max_watch_days()`）時**直接 import**，不得抄一份（publisher DD-7/DD-8 先例）。
- `docs/index.html` 是 `_build_index()` 的純函數輸出，由全等測試守門；掛單名單 `order_plan` 是 `compute_order_plan()` 純函式的確定性輸出，**不持久化**——「報告名單 = 次日進場資格」靠純函式重算保證，不靠存檔。

## 7. 拆股免疫（改 tracker 門檻欄位前必讀）

yfinance `auto_adjust=True` 拆股後回溯改寫全部歷史價。tracker 以 `signal_date_close`（訊號日當下寫入，DD-17 修正過時序）算 split_factor，**臨時**縮放全部門檻欄位（`stop_loss`、`target`、`planned_stop_loss`、`effective_stop_loss`、`active_entry_price`、`highest_close_since_active`）但不寫回 watchlist（DD-3/DD-12）。新增任何絕對價格欄位時，必須決定它要不要進縮放清單——漏掉 = 拆股日幽靈觸發。

## 8. 韌性邊界

- 單一 ticker/ETF 資料異常只影響自己，不得拖垮批次或整個 market_context（market DD-7；2026-07-06 事故：一支 ETF 缺 Close 欄位讓全部大盤背景退化成 `{}`）。
- AI 失敗時 `_enrich_fallback()` 降級輸出必須帶 `is_fallback: True`，下游（tracker 跳過、main.py 統計排除）依此區分，**不得用 confidence 數值猜測**（ranker DD-18）。
- 錯誤處理只加在系統邊界（外部 API/使用者輸入）；內部函式信任呼叫端（CLAUDE.md 慣例）——不要在內部函式間加防禦性 try/except。

## 9. 隔離紀律

tracker.py 與 scorer.py 不同 PR 修改（CLAUDE.md 禁止事項）。兩者分屬「選股」與「記帳」，同時動難以歸因。

再驗證：`grep -n "datetime.now()" src/*.py main.py`（預期恰好 3 個命中且**全部合法**：`src/earnings.py:38`、`src/earnings.py:97` 是快取 TTL 用牆鐘時間——正確用法，勿「修正」成 market_date；`src/filter.py:118` 是 docstring 文字。出現在 main.py、tracker.py、publisher.py 報告日期路徑上的任何新命中才是違規）
