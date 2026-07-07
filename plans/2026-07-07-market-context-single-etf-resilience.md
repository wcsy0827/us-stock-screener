# 2026-07-06 報告 0 支新增排查 → 大盤背景單點失敗防呆 + fallback 標記（Spec-First）

> 核准日期：2026-07-07。對應規格：`specs/market.md` DD-7、`specs/ranker.md` DD-18、`specs/tracker.md` DD-20（沿用 DD-14 既有過濾行為，未新增獨立 DD 編號於 tracker.md，理由見下）。

## Context

使用者回報：2026-07-06 的報告「一支篩選出來的都沒有」，但 `docs/data/last_run.json` 顯示 `ai_count: 5`，兩者不一致，需要查明原因。

**排查過程（依序確認的事實）**：

1. `docs/reports/2026-07-06.html` 統計區塊確認：`c-active=0`、`c-watch=5`（皆為 07-01/07-02 舊訊號）、`c-invalid=8`（同為舊訊號）、`c-new=0`、`c-reset=0`——當天沒有任何新股或重新入選的股票進入 watchlist。
2. `data/watchlist.json` 的 diff 確認：全部 13 筆條目的 `date_added` 都是 06-30/07-01/07-02，沒有任何 `date_added: 2026-07-06` 的條目。
3. 用 `gh run view <run_id> --log` 拉出當天 CI 完整日誌，找到關鍵行：
   ```
   [ranker] API 回傳 22 字元，finish_reason=stop
   [ranker] 解析成功，取得 0 筆結果
   [ranker] AI 排序失敗，改用 L2 分數直接輸出 Top N
   [tracker] GPC AI 信心分數 5 < 6，跳過   （× 5，5 支候選股全數如此）
   [publisher] last_run.json 已更新：...（regime=）
   ```
4. 往前追 `regime=` 空字串的成因，找到 Step 5.5 的真正錯誤：
   ```
   [fetcher] 成功取得 514 支股票數據   ← 515 支請求（503 成份股 + SPY + 11 板塊 ETF），少 1 支
   [pipeline] ── Step 5.5：抓大盤與產業 ETF 數據、計算市場廣度 ──
   [pipeline] 警告：大盤數據抓取失敗，繼續執行：'Close'
   ```
5. 讀原始碼確認完整因果鏈（見下方「根本原因鏈」）。

## 根本原因鏈

1. Step 2（`fetcher.fetch_batch`）515 支請求中有 1 支（某板塊 ETF）下載失敗；失敗的 ticker 依既有邏輯（`if len(df) >= 20: result[sym] = df`）直接從字典整支消失，不留空 DataFrame 佔位。
2. Step 5.5（`market.fetch_market_context`）嘗試補抓這支缺失 ticker，同樣失敗；`_get()` 的既有防呆把下載例外轉成一個**完全無欄位**的 `pd.DataFrame()`。
3. `_analyze(df)`（`src/market.py:70`）對 `df["Close"]` 沒有任何防呆，直接 `KeyError: 'Close'`，且此例外沒有被 `_analyze()` 自己截住，一路往外傳到 `fetch_market_context()` 唯一的外層 `try/except`。
4. 該 `try/except` 是函式層級的，一個 ETF 的資料異常導致**已經抓到的 SPY、VIX、其餘 10 支正常 ETF 全部一起被丟棄**，`market_context` 整個退化為 `{}`。
5. 空的 `market_context` 有兩個下游影響：(a) 寫入 `last_run.json` 的 `regime` 變成空字串；(b) 被原封不動送進 Step 6 `rank_candidates()` 的 AI Prompt，讓 DeepSeek 在完全缺少大盤環境描述的情況下做判斷。
6. DeepSeek 當天回傳合法但空的 JSON（`取得 0 筆結果`），`rank_candidates()` 依既有邏輯降級為 `_enrich_fallback()`——把 L2 分數前 5 名包成「AI 精選」格式，`confidence` 寫死為 5、`buy_zone`/`target`/`stop_loss` 皆為佔位字串 `"-"`。
7. `tracker.py` 既有的 `MIN_AI_CONFIDENCE=6` 門檻（DD-14）正確地把這 5 支 fallback 個股全部濾掉（`confidence=5 < 6`）——**這部分行為本身沒有錯**，fallback 個股本來就沒有真實的 `buy_zone` 可用，不該進 watchlist。
8. 但 `main.py` 的 `stats["ai_count"] = len(ranked)` 沒有區分「真實 AI 判斷」與「fallback 佔位」，把這 5 支寫死信心分數的候選股當成「5 支 AI 精選」記進 `last_run.json`。報告本身「0 支新增」是正確的，錯的是統計數字誤導使用者以為系統漏選了本該出現的股票。

## 兩個獨立缺陷

| 缺陷 | 位置 | 修法 |
|---|---|---|
| A | `market.py` `_analyze()` 對缺失 `Close` 欄位無防呆，單點失敗拖垮整個函式回傳值 | `_analyze()` 開頭防呆返回 `{}`；VIX 區塊比照加防呆 |
| B | `ranker.py` fallback 結果與真實 AI 判斷在下游（tracker 的 log、main.py 的 `ai_count`）無法區分 | fallback 結果標記 `is_fallback: True`；`tracker.py` 用獨立路徑跳過（明確 log，不再套用「信心分數不足」措辭）；`main.py` 的 `ai_count` 排除 fallback 條目 |

## 設計要點（濃縮版見各 spec 的 DD）

### market DD-7：`_analyze()` 防呆，缺失 Close 欄位回傳 `{}`
- 捨棄：在 `fetch_market_context()` 內對每個 `_analyze()` 呼叫個別包 `try/except`（治標不治本）；改讓 `_get()` 保證回傳值一定含 `Close` 欄位（需窮舉 `yf.download` 各種失敗形態，複雜度過高）。

### ranker DD-18：`_enrich_fallback()` 標記 `is_fallback: True`
- `tracker.py` 的 B/C 步驟優先檢查 `is_fallback`，獨立於 `confidence < MIN_AI_CONFIDENCE`（DD-14）之外直接跳過並印出區分明確的 log；`main.py` 計算 `ai_count` 時排除這些條目。
- **不變**：DD-14 的排除結果不變——fallback 個股本來就不該、也仍然不會進入 watchlist，這次沒有改變任何一支股票最終是否出現在報告上，只修正了統計數字與 log 措辭的準確性。
- 捨棄：只修 `market.py` 不修 `ranker.py`/`main.py`（DeepSeek 本身偶發回傳空清單、或未設 API Key 時走 `_enrich_fallback()` 的既有分支，與這次 `market_context={}` 是兩條獨立成因，任一條路徑觸發 fallback 都會重現同一種誤導性統計）；讓 `tracker.py` 用 `confidence == 5` 猜測是否為 fallback（脆弱，日後調整 `MIN_AI_CONFIDENCE` 或 fallback 預設分數任一方就會誤判）。

### tracker（沿用 DD-14，程式碼註解標 DD-20）
- `tracker.py` 已存在 `DD-18: 同日重跑不得重複遞增 watch_days/active_days`，為避免與 `specs/tracker.md` 既有編號衝突，程式碼註解中新增的 fallback 跳過邏輯改標記為 `DD-20`（`specs/tracker.md` 目前最新為 DD-19）。這段邏輯的完整敘述放在 `specs/ranker.md` DD-18（因為 `is_fallback` 欄位的定義與語意屬於 ranker 的輸出契約），tracker 端只是消費端，不另立獨立 DD 內文。

## 程式碼改動

1. `src/market.py`：`_analyze()` 開頭加防呆（`df is None or df.empty or "Close" not in df.columns`）；VIX 區塊 `if not vix_df.empty and "Close" in vix_df.columns:`。
2. `src/ranker.py`：`_enrich_fallback()` 回傳的每筆結果新增 `"is_fallback": True`。
3. `src/tracker.py`：B/C 迴圈在既有信心分數檢查之前，新增 `if stock.get("is_fallback"): print(...); continue` 獨立分支。
4. `main.py`：`stats["ai_count"]` 改為 `sum(1 for r in ranked if not r.get("is_fallback"))`。
5. `tests/test_market.py`：新增 `TestFetchMarketContextResilientToSingleTickerFailure`，模擬一支 ETF 補抓回傳無 `Close` 欄位的空 DataFrame，驗證其餘資料與 Regime 判定仍正常回傳。
6. `tests/test_ranker.py`（新檔）：驗證 `_enrich_fallback()` 回傳結果含 `is_fallback: True`。
7. `tests/test_tracker.py`：新增 `test_fallback_result_skipped_via_distinct_path_not_confidence_gate`，驗證即使 `confidence` 剛好達到門檻，`is_fallback=True` 仍會被跳過。

## 明確不做（範疇約束）

- 不改 DD-14 的過濾結果（fallback 個股本來就不進 watchlist，這是正確行為，不是本次要修的問題）。
- 不追查「為什麼 Step 2 那支 ETF 當天下載失敗」（yfinance/Yahoo 端的暫時性問題，重跑通常會自癒；本次修的是「單點失敗不該拖垮全局」的架構韌性，而非消除失敗本身）。
- 不修改 `_INFO_HTML`／`docs/index.html`（未變更 L1/L2/L3 定義、評分條件或 Regime 邊界，只修資料層防呆與統計準確性）。
- 不新增 CLI 參數、不改 CI workflow。

## 驗證

1. `pytest tests/` — 全數通過（含新增 3 項測試）。
2. `test_missing_close_column_on_one_etf_does_not_crash_whole_context`：模擬 XLV 補抓回傳空 DataFrame，確認 `sp500`/`Technology` 正常回傳、`Healthcare` 缺席但不拋錯、`regime` 正常判定。
3. `test_fallback_result_skipped_via_distinct_path_not_confidence_gate`：`is_fallback=True` 且 `confidence` 達標時仍被跳過，證明兩條判斷路徑互相獨立。
4. `test_enrich_fallback_tags_results_as_fallback`：確認 fallback 輸出含 `is_fallback: True`。
