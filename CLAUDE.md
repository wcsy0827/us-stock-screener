# 美股 AI 選股系統 — Claude Code 工作指南

## 專案概述

每日自動掃描 S&P 500，透過三層篩選 + 大盤 Regime 感知，找出符合當日市場環境的買入機會。結果發布至 GitHub Pages。

## 執行命令

```powershell
# 安裝
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # 填入 DEEPSEEK_API_KEY

# 本機測試（完整流程，不 push）
python main.py --dry-run

# CI 模式（跳過今日重複執行確認）
python main.py --dry-run --yes

# 強制忽略快取，重新下載所有數據（同時略過 AI 快取）
python main.py --dry-run --no-cache

# 僅略過 AI 快取，重新問 DeepSeek（price/info 快取仍複用）
python main.py --dry-run --yes --no-ai-cache

# 正式執行（生成並 push 至 GitHub Pages）
$env:PYTHONUTF8=1; python main.py

# 單元測試（tracker.py 純函式；不需連網、不觸碰 data/watchlist.json）
pip install -r requirements-dev.txt
pytest

# Windows 包裝腳本
.\run.ps1 --dry-run
.\run.ps1 --top 10

# 前端完整預覽（含 last_run.json，模擬 GitHub Pages 行為）
# --dry-run 後 docs/data/last_run.json 已寫入，但直接開 file:// 會因瀏覽器安全限制導致 fetch 失敗
# 用本地 server 才能完整預覽「上次執行時間」與「資料核實面板」
python main.py --dry-run --yes
cd docs && python -m http.server 8080   # 瀏覽器開 http://localhost:8080
```

## 架構速覽

```
S&P 500 (~503 支)
  ↓ Step 1   universe.py     爬取成份股
  ↓ Step 2   fetcher.py      下載 90 日日 K（.cache/ 快取）
                              同批次下載 11 支板塊 ETF（XLK/XLV/XLF 等）及 SPY，供 scorer RS 計算用
                              盤中執行時自動捨棄當日尚未收盤的殘缺K棒（trim_incomplete_session，美東 16:15 前判定未收盤）
  ↓ Step 2.5 market.py       快速 Regime 判定（近 3 日均廣度 + VIX）← 必須在 scorer 之前
                              五象限：BULL_TREND / CONSOLIDATION / CONSOLIDATION_VOLATILE / PANIC_REVERSAL / BEAR_DISTRIBUTION
                              回傳 (regime, breadth_pct, vix_value, vix_ok)，供 Step 5.5 複用
                              廣度邊界遲滯帶 ±2%（讀取 last_run.json，VIX 跨越結構邊界時強制放行）
  ↓ Step 3   fetcher.py      抓基本面（7 日快取），順帶提取 earningsDate 欄位
  ↓ Step 3.5 earnings.py     財報日查詢（Tier 1+2）→ earnings_registry.json（30 日快取）
  ↓ Step 4   filter.py       L1 流動性硬篩（股價/日成交額/市值/交易天數/ATR% 波動上限）
  ↓ Step 4.5 earnings.py     Tier 3 精準補抓（僅對流動性篩選後倖存個股）
             filter.py       財報防禦牆（排除 5 天內有財報的個股，cutoff 錨定 market_date）
  ↓ Step 5   scorer.py       L2 技術評分（六維度 100 分；動態門檻依 Regime；相對強度 RS 維度）
                              CONSOLIDATION_VOLATILE 門檻 65 分，PANIC_REVERSAL 40 分
  ↓ Step 5.5 market.py       完整大盤 ETF 背景（直接複用 Step 2.5 的廣度與 VIX，不重算）
  ↓ Step 5.7 analyzer.py     本地績效診斷（讀 performance_history.json，歸納 Regime×策略×產業賺賠關聯
                              → data/ai_hints.json；分組 <3 筆或總樣本 <5 筆不生成回饋；失敗不中斷流程）
  ↓ Step 6   ranker.py       L3 DeepSeek AI 精選（≤3 支；30 欄 Markdown 表含 RS_vs_Sector、基本面欄位、空頭比例、ATR14；每產業 ≤8 支）
                              發送前自動讀取 ai_hints.json，非空時在 Prompt 末尾附加 Historical_Performance_Review 區塊
             tracker.py      訊號追蹤（watchlist.json）→ 結算歸檔（performance_history.json）
                              組合層持倉上限 MAX_ACTIVE_POSITIONS=5：滿倉時觸價訊號依 AI 信心排序競爭名額（DD-20）
             publisher.py    HTML 報告 → GitHub Pages（個股浮損益、今日結算區段、策略 Tooltip、歷史績效儀表板）
```

## 模組 ↔ 規格對照表

修改任何模組**前**，先讀對應規格文件：

| 模組 | 規格文件 |
|------|----------|
| `src/scorer.py` | `specs/scorer.md` |
| `src/tracker.py` | `specs/tracker.md` |
| `src/ranker.py` | `specs/ranker.md` |
| `src/market.py` | `specs/market.md` |
| `src/pipeline.py` | `specs/pipeline.md` |
| `src/fetcher.py` | `specs/pipeline.md`（快取節） |
| `src/filter.py` | `specs/pipeline.md`（L1 節）、`specs/earnings.md`（財報防禦牆） |
| `src/earnings.py` | `specs/earnings.md` |
| `src/analyzer.py` | `specs/analyzer.md` |
| `src/publisher.py` | `specs/publisher.md` |

## Spec-First 工作流

```
需求 → 更新/新增 specs/<module>.md → 實作 → PR 引用規格節次
```

- 若需求與現有規格衝突，**先更新規格**，說明為何改變，再動程式碼
- 規格的 Design Decisions 節是已解決的設計爭議，不得在未取得用戶同意前繞過

## Plan 文件化規則

任何經 Plan Mode 核准並執行完成的 plan，必須文件化保存，不能只留在使用者本機的暫存 plan 檔案（`~/.claude/plans/`，不在版控範圍內，無法跨 session 保存）。同一個 commit 需同時完成以下兩份文件：

1. **完整記錄**：把核准當下的 plan 全文（含 Context、探索過程、考慮過但捨棄的方案）存成 `plans/YYYY-MM-DD-<slug>.md`（repo 根目錄的 `plans/` 資料夾，檔名用當日日期 + 簡短英文 slug）
2. **精簡摘要**：在對應模組的 `specs/<module>.md` 補上一個新的 Design Decision（DD-N），只留最終選擇、原因、捨棄方案的濃縮版本（不含探索過程），並在該 DD 段落結尾加上 `→ 詳見 plans/YYYY-MM-DD-<slug>.md` 互相連結

不得只做其中一份——`plans/` 是完整歷史軌跡（給未來想知道「當初為什麼這樣選」的人），`specs/` 是給日常開發參考的精簡權威版本（DD 段落本身已符合現有慣例）。

## 核心設計決策（摘要）

1. **EMA50 悖論（tracker.py DD-1）**：反轉策略進場點本就在 EMA50 之下，不能以跌破 EMA50 作為失效門檻。反轉股失效條件改用 AI 設定的 `stop_loss` 絕對價。→ 詳見 `specs/tracker.md`

2. **拆股免疫（tracker.py DD-3）**：yfinance `auto_adjust=True` 在拆股後會回溯修改全部歷史收盤價，導致 watchlist 記錄的絕對止損價變成幽靈訊號。解法：記錄 `signal_date_close`，每日計算 split_factor 並平移所有門檻。adj 必須同時縮放 `stop_loss`、`target`、`planned_stop_loss`、`effective_stop_loss`、`active_entry_price`、`highest_close_since_active`；`_check_settlement` 呼叫時傳入 adj（非原始 entry）。→ 詳見 `specs/tracker.md`

3. **Step 2.5 指標複用（pipeline.py DD-1）**：市場廣度 + VIX 在 Step 2.5 計算後直接傳給 Step 5.5，不重算、不重下載。`fetch_regime_quick` 的四個回傳值（`regime`, `breadth_pct`, `vix_value`, `vix_ok`）均須保留；`vix_ok=False` 時 pipeline 在 L3 前中斷，不呼叫 DeepSeek API。→ 詳見 `specs/pipeline.md`

4. **開盤跳空安全攔截（tracker.py DD-7）**：`watch → active` 轉換時，除了 `price >= buy_zone_lower` 外，必須額外確認 `price > stop_loss`。防止 AI 誤設止損在買入區間內時，跳空進場卻已跌破止損，污染 performance_history.json。→ 詳見 `specs/tracker.md`

5. **績效結算狀態機（tracker.py DD-6）**：active 部位不由時間到期控制，改由 `_check_settlement()` 的四態結算（CLOSED_PROFIT / CLOSED_LOSS / CLOSED_TRAILING_STOP / FORCE_EXPIRED）觸發，結算後寫入 `data/performance_history.json` 並移出 watchlist。publisher 讀取此檔案時必須做冷啟動保護（檔案不存在或空陣列時回傳零值，不得拋 ZeroDivisionError）。→ 詳見 `specs/tracker.md`

6. **持倉天數用交易日（tracker.py DD-8）**：`_archive_to_performance_history` 的 `holding_days` 以 `active_days` 計數器（每個交易日 +1）為主，不得使用 `exit_date - active_start_date` 的日曆天差（包含週末，語意錯誤）。`_count_trading_days` 僅作為計數器缺失時的備援。→ 詳見 `specs/tracker.md`

7. **Active 持倉再入選時不重置（tracker.py DD-9）**：當 `existing[sym].status == "active"` 時，跳過 `update(base)`，不加入 `reset_symbols`，讓部位繼續出現在 `categories["active"]`。防止 `active_days` 歸零、`active_entry_price=None`、`hold_period` 永遠不觸發，進而污染 `performance_history.json`。→ 詳見 `specs/tracker.md`

8. **日內高低點實質結算（tracker.py DD-10）**：`_check_settlement()` 改用 `today_low ≤ effective_stop_loss` 觸發 CLOSED_LOSS、`today_high ≥ target` 觸發 CLOSED_PROFIT；移動停利（收盤回撤 5%）觸發 CLOSED_TRAILING_STOP；exit_price 為相應絕對值或收盤價。黑天鵝（同日雙觸發）保守判為 CLOSED_LOSS。High/Low NaN 時 fallback 為 close，避免停損免疫。→ 詳見 `specs/tracker.md`

9. **執行順序強制約束 + 基準日錨定（tracker.py DD-11）**：`run_tracker()` 執行順序固定為 D（下載現有）→ E（評估現有）→ B/C（處理新訊號）。新選股在當輪不被評估，自然形成 1-day lag。`today` 由 `market_date` 參數注入（pipeline 從 `price_data["SPY"].index[-1].date()` 提取），確保本地 CST 與 CI UTC 執行行為一致。→ 詳見 `specs/tracker.md`

10. **風控雙欄位（tracker.py DD-12）**：`planned_stop_loss`（float）為 AI 原始值，唯讀，專作 DD-3 拆股基底；`effective_stop_loss`（float）為動態止損，保本鎖定後上移至 `buy_zone_upper`；`is_breakeven_locked`（bool）為明示旗標，防止浮點抖動重複觸發。`highest_close_since_active` 以原生未拆股標尺存儲，避免逆向除法累積誤差。DD-3 縮放時所有風控欄位皆臨時縮放，但不寫回 watchlist。→ 詳見 `specs/tracker.md`

11. **全自動保本鎖定與移動停利（tracker.py DD-13）**：進場後收盤達目標距離 50% 時，`effective_stop_loss` 自動上移至 `buy_zone_upper`，`is_breakeven_locked` 鎖定為 True。動能/突破策略若峰值浮盈超過 10% 後收盤回撤 5%，觸發 `CLOSED_TRAILING_STOP` 結算（出場價 = 收盤）。反轉策略精確比對排除。`is_win` 判定純用 `return_pct > 0`，與出場原因完全解耦。→ 詳見 `specs/tracker.md`

13. **L2 新增相對強度 RS 維度（scorer.py DD-9）**：L2 由五維度升級為六維度（MA=20, RSI=18, MACD=17, Volume=15, Momentum=15, RS=15），個股 5 日報酬率 − 板塊 ETF 5 日報酬率 = `rs_5d`，≥+2%→15 分，≥+0.5%→8 分，≥-0.5%→3 分，否則 0 分；板塊 ETF 數據在 Step 2 批次下載，scorer 直接讀 price_data。BULL_TREND 環境 RSI 健康區間擴大至 80；量能評分加入 5 日斜率係數（polyfit）；動能改為 20 日主趨勢 × 5 日方向確認雙期同步。→ 詳見 `specs/scorer.md`

14. **Regime 五象限：CONSOLIDATION_VOLATILE 獨立分區（market.py DD-4）**：廣度 35~60% 時依 VIX 細分：VIX < 20 → `CONSOLIDATION`（60 分門檻）；VIX ≥ 20 → `CONSOLIDATION_VOLATILE`（65 分門檻，AI 指引更保守）。`last_run.json` 新增 `regime`、`market_date` 欄位，供 `market.py` 的遲滯帶讀取。→ 詳見 `specs/market.md`

15. **廣度遲滯帶防邊界翻轉（market.py DD-5）**：`fetch_regime_quick()` 讀取前一日 `last_run.json` 的 `regime`，廣度在邊界 ±2%（60% 或 35%）內時維持前日 Regime；VIX 跨越結構邊界（高 VIX 組 PANIC/CONSOLIDATION_VOLATILE ↔ 低 VIX 組）時強制放行，不套用遲滯。`last_market_date < current_market_date` 嚴格校驗，防止同日重複執行污染。→ 詳見 `specs/market.md`

16. **動能策略買進區間結構化（ranker.py DD-12）**：候選池表格新增 `EMA5`/`EMA10`/`EMA20`（美元原始價位）與 `Vol_vs_5DAvg`（當日量 ÷ 5日均量）四欄，解決 AI 過去只有 `MA_Trend` 文字標籤、無實際 EMA 數值可用而把 `buy_zone` 上限退化成收盤價的問題。動能策略 Prompt 改為三段式規則：標準回檔進場設在 `EMA20~EMA10` 且 `Vol_vs_5DAvg < 0.7`（量縮確認）；極端強勢例外可用 `EMA5` 附近（5MA 探針帶）；股價距 `EMA5` 超過 +5% 視為過度延伸禁止追價。`buy_zone` 仍由 AI 自行輸出字串，不改為 Python 端確定性計算。→ 詳見 `specs/ranker.md`

17. **突破/反轉策略買進區間結構化（ranker.py DD-13）**：候選池表格新增 `High_20D`/`Vol_vs_20DAvg`（突破策略）與 `EMA50`/`Low_20D`/`Stoch_K`/`RSI_5D_Ago`（反轉策略）共六欄。根因比 DD-12 更嚴重：Prompt 原本就直接引用 `stoch_k`、`rsi_5d_ago`、`ema50` 等指標名稱要求 AI 判斷，但這些值只用於 `_strategy_tag()` 內部計算，AI 在表格裡完全看不到。突破策略 Prompt 改為四段式規則（回測確認優先於當日追單、`Vol_vs_20DAvg >= 1.5` 攻擊量確認）；反轉策略改為三段式規則（`EMA50` 支撐 + `Stoch_K`/`RSI_5D_Ago` 底背離確認 + `Low_20D` 止損基準）。完整的 W 底/BOS/斐波那契回撤形態辨識超出「單筆技術指標快照」的資料設計範疇，本次不做。→ 詳見 `specs/ranker.md`

18. **盤中執行自動捨棄殘缺當日 K 棒（fetcher.py，`specs/pipeline.md` DD-6）**：`yf.download(interval="1d")` 在美股盤中會回傳「今天」的殘缺 OHLCV（非最終收盤值），第 12 點原本假設「盤中觸發＝自動拿到前一日完整報告」只是文件描述，程式碼並未實際保證。`fetcher.trim_incomplete_session()` 在 Step 2 完成後執行：比對 `price_data["SPY"]` 最後一列日期是否等於美東（`America/New_York`）當下日期，且美東現在時間早於 `16:15`（收盤 16:00 + 15 分鐘 settle buffer），成立則逐股捨棄該殘缺列（過濾後 `< 20` 列的股票整支移除），讓 `market_date` 自動回退到前一個完整交易日，補上第 12 點文件假設與程式碼行為之間的落差。→ 詳見 `specs/pipeline.md`

19. **L3 候選池加入基本面維度（ranker.py DD-14）**：L1 硬篩流動性、L2 是純技術評分，AI 在 L3 拿到的候選股技術面已經高度同質化，若判斷依據仍 100% 是技術指標，等於在同一批技術達標股票裡比誰的指標更漂亮。候選池表格新增 `Fwd_PE`（估值）、`Profit_Margin`（獲利品質）、`Rev_Growth_YoY`（成長性）三欄，取自 `fetcher.fetch_info()` 已下載但未使用的 yfinance `info` 字典欄位（不需額外 API 呼叫）。三欄缺值比照既有 `Beta_60D` 先例不排除（僅標記 N/A、讓 AI 自行降權判斷），不比照 `Earnings_Days_Left` 直接排除。JSON 輸出 schema 不變，基本面判斷折疊進既有 `reason`/`risk`/`confidence` 三欄；`buy_zone` 相關的 DD-12/DD-13 策略型買進區間算法完全不受影響。分析師共識維度（目標價/評等）經與使用者討論後排除，不在本次範圍內。→ 詳見 `specs/ranker.md`

20. **L2 候選池排名上限，穩定輸出數量至 50~60 支（scorer.py DD-10）**：原本固定分數門檻（`min_score`，Regime 感知調整）的通過數量完全跟著大盤強弱擺動（BULL_TREND 廣度 63.9% 時 119 支通過 70 分門檻，弱勢盤面可能剩不到 20 支，強勢盤面可能破 200 支），無法穩定收斂到期望區間。`score_all()` 篩選出品質門檻候選 `qualified` 後，若數量 `> L2_TARGET_COUNT`（55），取第 55 名分數為 `cutoff_score`，只保留 `total_score >= cutoff_score` 的股票（同分邊界一律保留，不引入 tie-breaker，允許小幅超出目標區間）；`force_pass`（PANIC_REVERSAL 強制放行股）不受排名上限排除；`qualified` 數量本來就 `<= 55` 時不觸發，不硬湊數量。既有 Regime 感知分數門檻與 DD-1/DD-2/DD-3 強制放行機制完全保留，排名上限是疊加在品質門檻之上的天花板，不是取代品質門檻。→ 詳見 `specs/scorer.md`

21. **動能/反轉策略止損改為明確百分比緩衝（ranker.py DD-15）**：用 `data/watchlist.json` 實測數據發現動能策略止損常等於買入區間下緣（CB/KHC/V/AJG/LIN 五支候選股完全相等）——根因是買入區間下緣本身就是 EMA20，止損規則卻只寫「跌破 EMA20」這個方向詞、沒有明確百分比，AI 有時照字面解讀成止損=EMA20，等於一買進去就已觸發止損、沒有容錯空間。止損改為「EMA20 下方 2%（不得設為等於買入區間下緣，須低於 EMA20）」，比照突破策略既有的「跌回 High_20D 下方 2%」寫法。反轉策略「止損：Low_20D 下方」有同一類措辭缺陷（雖因買入區間錨點 EMA50 與止損錨點 Low_20D 不同、正常情況天生有緩衝而風險較低，但「`buy_zone` 下緣不得低於 `Low_20D`」地板條件被觸發時仍可能重演），一併改為「Low_20D 下方 2%」並加註不得等於買入區間下緣。純 Prompt 文字修正，`tracker.py` 的止損/買入區間解析邏輯零改動。→ 詳見 `specs/ranker.md`

22. **watch 天數上限疊加訊號當下 regime/VIX 條件（tracker.py DD-16）**：`_max_watch_days()` 在 DD-15 策略查表之上疊加：`突破策略` 且訊號當下 `entry_regime == "CONSOLIDATION_VOLATILE"` → 3 日（高波動整理市假突破機率高，縮短觀察期）；`反轉策略` 且訊號當下 `entry_regime == "PANIC_REVERSAL"` 且 `vix_value > 35` → 5 日（VIX 暴噴級尖底，V 型反彈應快速兌現，遲遲不進場視為真黑天鵝而非錯殺）；其餘沿用 DD-15 查表結果（反轉策略 VIX 25~30 維持 10 日）。判斷用訊號建立當下鎖定的 `entry_regime`/`vix_value`（早於本次改動即已寫入 watchlist 條目），不用每日重新查詢的當下 regime，與 `buy_zone`/`stop_loss` 訊號鎖定慣例一致。既有 watchlist 條目立即套用新規則，不做版本判斷或遷移。→ 詳見 `specs/tracker.md`、`plans/2026-07-03-watch-days-regime-vix.md`

24. **每日報告顯示動態止損與策略差異化剩餘天數（publisher.py DD-7）**：使用者實際操作方式是收盤後跑篩選、次一交易日盤中依報告上的買入區間/止損手動下單，但 `_tracking_row()` 過去只顯示 AI 原始 `stop_loss` 字串，與 `tracker.py`（DD-12/13）內部早已自動執行的保本鎖定、移動停利完全脫節——系統已把止損上移到 `buy_zone_upper`，報告卻仍顯示舊止損，使用者跟單等於用錯門檻。修法：active 條目優先顯示 `effective_stop_loss`（缺失時 fallback 原始 `stop_loss`），`is_breakeven_locked` 時附加「🔒保本」；峰值浮盈達 `TRAILING_ACTIVATION_PCT`（10%）門檻的動能/突破策略額外顯示移動停利觸發線（反轉策略精確排除，與 DD-13 口徑一致）。同時修正 watch/invalid 狀態「剩 N 天自動移除」原本寫死 `5 - days` 的問題，改呼叫 `tracker._max_watch_days()`（單一事實來源，不在 publisher.py 內重複維護一份查表），正確反映反轉策略 10 日、高波動整理市突破策略 3 日等 DD-15/16 差異化上限。→ 詳見 `specs/publisher.md`

23. **active 部位不再被 _eval_status 判定失效 + signal_date_close 訊號日即時寫入（tracker.py DD-17）**：`run_tracker()` 的 E 步驟原本在同一輪迭代中先呼叫 `_eval_status()` 並立即寫回 `status`，再呼叫 `_check_settlement()`——後者對非 active 狀態直接跳過，導致 active 持倉一旦被 `_eval_status()` 判定失效（反轉策略跌破止損、動能/突破跌破 EMA20），會被翻成 `invalid` 並在結算前繞過歸檔，虧損交易從未寫入 `performance_history.json` 就被 `_is_expired()` 無聲移除，`analyzer.py` 的勝率統計因此系統性向上偏差。修法：`_eval_status()` 對 `status == "active"` 一律短路回傳 `("active", None)`，生命週期完全交給 `_check_settlement()` 的四態結算（原狀態機圖「active → invalid」轉換已移除，該轉換在 DD-10 盤中觸價結算下本就邏輯不可達）。同批修正 `signal_date_close`：原本延遲到訊號日次一評估日才回填收盤價，但拆股比對錨定的是訊號日（`tracked_dates[0]`），兩者錯位一個交易日，日漲跌超過 ±1% 就會誤判拆股、把 `buy_zone`/`stop_loss`/`effective_stop_loss` 全部錯誤縮放（可能讓已進場部位被誤翻回 watch，或讓真實止損因門檻被縮小而漏判）。改為建立條目當下直接寫入 `stock.get("price")`（訊號日 L2 收盤價，`ranker.py` 已提供不需額外下載），新增與 watch/invalid 覆寫展期（reset）兩條路徑因共用 `base` 字典同步修復；次一評估日的舊回填邏輯保留作存量條目 fallback。目前系統處於冷啟動期（無 active 部位、`performance_history.json` 尚未產生），不做存量資料 migration。→ 詳見 `specs/tracker.md`

25. **同日重跑不得重複遞增 watch_days/active_days（tracker.py DD-18）**：`run_tracker()` 的 `tracked_dates` 早已有「今日未記錄才附加」的去重判斷，但緊接著的 `watch_days`/`active_days` 遞增沒有比照守衛，導致同一天內手動重跑並選擇繼續執行（`main.py` 的重跑確認詢問，或 CI 的 `--yes`）時，兩個計數器會被重複累加。後果：`active_days` 可能提前抵達 `hold_period` 而在同一天內就觸發 `FORCE_EXPIRED`，`_max_watch_days()` 的到期判斷同樣受影響，`_archive_to_performance_history()` 的 `holding_days`（DD-8）因而失真。修法：在 E 步驟附加 `tracked_dates` 前先讀出 `already_tracked_today` 旗標，`tracked_dates.append` 與計數器遞增共用同一判斷式，維持單一事實來源；`_apply_risk_controls()` 與 `_check_settlement()` 本身冪等（以「是否創新高」/「是否已鎖定」判斷），不需要疊加此守衛。→ 詳見 `specs/tracker.md`

26. **盤中限價單模擬進場，觸價優先於收盤價失效判定（tracker.py DD-19）**：使用者實際操作方式是收盤後跑選股、次一交易日盤中依買入區間**上緣**掛限價單，但 `_eval_status()` 原本只認收盤價——股價盤中回落到區間、限價單已成交，收盤卻彈出區間之外時，系統仍判 `watch` 甚至「已追高」而移除，使用者手上的真實部位從未被追蹤。修法：`_eval_status()` 新增 `today_low` 參數，在 invalid/active 短路之後插入一行檢查 `today_low <= buy_zone_upper → active`，優先於下方所有收盤價判定；未觸價或未提供 `today_low` 時完全退化為原邏輯（既有 8 個回歸測試與規格分支逐字元不變，僅新增測試涵蓋新路徑）。同日觸價又跌破止損（跳空急殺）時不再直接判 `invalid` 拒絕進場（原 DD-7 機制，改列為 dormant），而是回傳 `active` 讓既有 `_check_settlement()` 立即以 `today_low<=effective_stop_loss` 結算 `CLOSED_LOSS`，比照 DD-10 黑天鵝原則同日歸檔，不再讓真實虧損消失不留紀錄。進場代理價改為 `buy_zone_upper`（使用者實際掛單價，經抗辯審查排除 `min(今日開盤, upper)` 方案——多抓開盤價換來的精確度有限，卻引入開盤異常值污染 `return_pct` 的風險）。同批修正兩個前置/關聯缺陷：`_fetch_latest()` 的 High/Low 原本不論 Close 是否為 NaN 一律取最後一列，與 `price`（`dropna()` 後可能落在前一列）日期錯位，破壞 `today_low<=price<=today_high` 恆等式，改為與 `price` 同列讀取；`_parse_hold_period()` 加下界 1，避免 AI 給出 `hold_period<=0` 時同日觸價成交即被誤判 `FORCE_EXPIRED`。本設計經 skeptic/red-team/simplifier 三方抗辯審查後採用最小化版本（不刪除任何舊分支、不新增 `today_open` 欄位）。→ 詳見 `specs/tracker.md`

27. **財報防禦牆判斷基準日錨定 market_date（filter.py DD-E5）**：`apply_earnings_filter()` 原本用 `date.today()` 判斷「未來 5 天內是否有財報」，與專案其餘模組（`main.py`、`tracker.py`）一律錨定 `market_date` 的原則不一致，台灣本地執行時系統時鐘與 `market_date` 可能相差一天，讓財報黑名單窗口偏移。修法：新增 `today: date | None = None` 參數，由 `pipeline.py` 傳入 `market_date`（未提供時 fallback 為 `date.today()` 以保持向下相容）。→ 詳見 `specs/earnings.md`

28. **fetch_market_context 複用 Step 2 已下載的 SPY/產業 ETF 資料，不重複下載（market.py DD-6）**：Step 2（`fetcher.fetch_batch`）已下載過 SPY 與全部板塊 ETF（90 日），`fetch_market_context()` 原本卻對同一批 ticker 重新發出一次 60 日下載，純屬浪費 API 配額與執行時間（無正確性風險，純效率問題）。修法：優先從 `all_stocks_data`（pipeline 呼叫端已傳入 `price_data`）讀取 SPY/產業 ETF，只有缺失的 ticker 才個別補抓；`^VIX` 從未包含在 Step 2 範圍內，一律單獨下載。未提供 `all_stocks_data` 時行為與修法前相同（向下相容）。→ 詳見 `specs/market.md`

29. **L1 新增 ATR% 波動上限風控過濾（filter.py DD-8）**：`ranker.py` DD-15 已把三策略止損統一收斂為「錨點下方 2%」固定緩衝，但緩衝寬度沒有對照個股自身波動——日均 ATR% 達 6~8% 以上的個股，2% 止損形同虛設，正常雜訊就會掃損。新增 `_atr_pct()`（ATR14/收盤價百分比）在 L1 排除 `> MAX_ATR_PCT`（預設 8%，`env: MAX_ATR_PCT`）的個股；歷史數據不足 15 筆無法計算時不排除。→ 詳見 `specs/pipeline.md`

30. **L3 候選池新增 Short_Float_Pct 空頭比例標記（ranker.py DD-17）**：候選池表格新增 `Short_Float_Pct` 欄（`shortPercentOfFloat`，`fetcher.fetch_info()` 免費附帶），比照 DD-14 基本面三欄先例，缺值填 `N/A` 且**不觸發 AI 排除**，僅供 AI 在 `risk`/`confidence` 中納入軋空風險旗標（實務上 >15% 視為高風險）。JSON 輸出 schema 不變，`buy_zone`/止損相關的 DD-12/13/15 策略算法不受影響。→ 詳見 `specs/ranker.md`

32. **動能策略買進區間改為 ATR 錨定淺回檔帶（ranker.py DD-19）**：使用者實測觀察「篩選出來的股票太強勢、等不到回檔」（2026-07-06 報告：4 支死於「已追高」）——根因是 L2 的職責就是挑強勢股（均線多頭、還沒回檔），DD-12 卻要求買入區間設在 `EMA20~EMA10` 深度回檔帶（現價下方 4~8%），5 日 watch 窗口內等到的機率極低，越強的股票越買不到；且固定 EMA 帶不看個股波動，低波動股回檔 1~2% 就續漲卻被要求等 4~8%。修法（對應機構「ATR 比例回檔 + ATR 動態止損」實務）：候選池表格新增 `ATR14` 欄（`compute_indicators()` 原本已算出、僅供 `Momentum_ATR` 除法後即丟棄，本次曝露）；買入區間預設改為 `Close − 1×ATR14 ～ Close − 0.25×ATR14` 淺回檔帶（下緣不低於 EMA10），深度自適應個股波動；原 EMA20~EMA10 帶降級為「已回檔加分情境」；「距 EMA5 超過 +5% 一律禁止」改為「RSI > 78 或 VTF < 0 過熱熔斷」（距離均線遠近不等於危險，量價結構才是）；止損改為買入區間下緣 − 1×ATR14（加分情境可用 EMA20 下方 2%，取較高者）。`scorer.py`/`tracker.py`/突破/反轉策略零改動；L1 既有 `MAX_ATR_PCT=8%` 上限保證 ATR 錨定寬度有天然上界。→ 詳見 `specs/ranker.md`、`plans/2026-07-07-momentum-atr-anchored-buy-zone.md`

31. **單一 ETF 資料異常不得拖垮整個大盤背景 + fallback 標記不再誤記為 AI 精選（market.py DD-7、ranker.py DD-18）**：2026-07-06 report 排查發現：Step 2 一支板塊 ETF 下載失敗、Step 5.5 補抓再度失敗後，`market.py` 的 `_analyze()` 對缺失 `Close` 欄位無防呆直接拋 `KeyError`，導致該例外一路傳到 `fetch_market_context()` 唯一的外層 `try/except`，把當天已抓到的 SPY/VIX/其餘 10 支正常 ETF 全部一併丟棄，`market_context` 整個退化為 `{}`（`last_run.json` 的 `regime` 因此變空字串），且這個空背景被原封不動送進 Step 6 的 AI Prompt，疑似導致 DeepSeek 當天回傳空結果、觸發 `ranker.py` 既有的 `_enrich_fallback()` 降級（L2 分數前 N 名包成「AI 精選」格式，`confidence` 寫死 5）。`tracker.py` 既有 `MIN_AI_CONFIDENCE=6` 門檻（DD-14）正確濾掉這些 fallback 個股（不進 watchlist，這部分行為本來就對），但 `main.py` 的 `ai_count = len(ranked)` 沒有區分 fallback 與真實 AI 判斷，把 5 支佔位股計入「AI 精選」寫進 `last_run.json`，與報告「0 支新增」互相矛盾，誤導使用者以為選股流程漏掉了東西。修法：(1) `_analyze()` 開頭加防呆，缺失 `Close` 欄位回傳 `{}`，單一 ETF 異常只影響該 ETF 自己的欄位；(2) `_enrich_fallback()` 結果標記 `"is_fallback": True`，`tracker.py` 用獨立路徑（非「信心分數不足」措辭）明確跳過，`main.py` 的 `ai_count` 排除這些條目。不改變任何一支股票最終是否出現在報告上，只修正資料層韌性與統計數字的準確性。→ 詳見 `specs/market.md`、`specs/ranker.md`、`plans/2026-07-07-market-context-single-etf-resilience.md`

34. **組合層級 active 持倉上限（tracker.py DD-20、publisher.py DD-8）**：watchlist 持倉數原本無組合層上限（每日 L3 流入 ≤3 支 × DD-19 淺回檔帶 ~100% 觸價成交率 × 常見 15 日持有期 ≈ 穩態 45 支同時持倉），與使用者真實資金操作脫節，績效統計隱含「資金無限」假設。新增 `MAX_ACTIVE_POSITIONS`（env，預設 5）：`run_tracker()` E 步驟迴圈前鎖定 `free_slots = max(0, 上限 − active 數)`，迴圈改依 `_slot_priority_key`（`-ai_confidence` → `-l2_score` → `symbol`，缺值視為 0）排序迭代，watch→active 轉換需扣名額；滿倉被擋者以 `today_low=None` 重跑 `_eval_status()` 取收盤價判定——失效（跌破止損/已追高）直接作廢（沒掛單即無真實交易，不記績效），否則維持 watch 並設 `slot_blocked_today` 旗標（迴圈頂端每日重置且早於 `sym not in latest` 的 continue；B/C `base` 字典含 `False` 預設，reset 路徑同步清除）。當日結算不退還名額（1-day lag，與 DD-11 口徑一致）；既有超額持倉不強平，由結算自然收斂；B/C 新訊號照常入 watch。DD-19 宣告 dormant 的收盤價分支因 blocked 重跑而重新可達，不得以 dormant 為由清理。publisher 端（DD-8）：被擋 watch 條目標註「今日觸價但持倉已滿，未進場」、有效追蹤清單標題附「上限 N 支」、今日統計改顯示「持倉/上限」，常數自 `tracker` import（單一事實來源）；`_INFO_HTML` 僅寫靜態文字不插值 runtime 常數（避免 `.env` 不同的環境跑 index.html 全等守門測試誤紅）。注意：`specs/ranker.md` 另有編號相同但無關的 DD-20（L3 精選上限 5→3，見第 33 條）。→ 詳見 `specs/tracker.md`、`specs/publisher.md`、`plans/2026-07-10-max-active-positions-cap.md`

33. **L3 精選上限 5→3 + reason 欄位敘事化（ranker.py DD-20、DD-21）**：使用者要求聚焦：精選上限由 5 支調降為 3 支（`<Output_Constraint>`/System Prompt 文字、`main.py --top` 預設（env `MAX_OUTPUT`）、`pipeline.run()`/`rank_candidates()` 的 `top_n` 預設、`.env.example` 全部同步改 3；候選池廣度 `MAX_CANDIDATES_TO_AI=40`、每產業 ≤8、L2 Top 55 均不動）。同批修正選股理由品質：實際報告中 `reason`（原 50 字上限）幾乎被技術指標數字複述佔滿、與 `strategy_reason` 高度重複，改為 80~120 字並聚焦技術指標數值以外的論述（基本面三欄相對優劣、產業 ETF 趨勢、Regime 契合度、Short_Float_Pct 風險背景），明文禁止在 reason 重複羅列指標數字（數值引用職責歸 `strategy_reason`）。JSON schema 不變，`tracker.py`/`publisher.py` 解析零改動。→ 詳見 `specs/ranker.md`

12. **報告日期與防重複執行皆錨定 UTC（main.py / tracker.py）**：CI 在 UTC 時區執行；台灣時間 7/1 08:00 = UTC 6/30 24:00，yfinance 此時拿到的最後數據仍是 6/30——報告正確標示 6/30 是預期行為，不是 bug。規則如下：
    - **`main.py`**：`stats["date"]` 必須用 `datetime.strptime(market_date_str, "%Y-%m-%d")`（`market_date_str` 來自 `summary["market_date"]` = `price_data["SPY"].index[-1].date()`），**不得用 `datetime.now()`**。`datetime.now()` 在非 UTC 時區執行時，與 market_date 不一致，會產生「報告標題 7/1、數據內容 6/30」的誤導標籤。
    - **`tracker.py`**：`check_already_run_today()` 使用 `datetime.utcnow().date()` 而非 `date.today()`，確保台灣本地執行時防重複邏輯與 CI 時區一致。`run_tracker()` 內的 `today` 繼續由 `market_date` 注入（DD-11 不變）。
    - **何時才會出現次日報告**：美股 7/1 的完整收盤數據要等到 UTC 20:00+（台灣 7/2 04:00）才可用，自動 CI 在 UTC 21:30（台灣 7/2 05:30）抓取並產出 7/1 報告。在此之前觸發的任何手動執行，拿到的都是 6/30 數據，結果一樣是 6/30 報告。
    - **`.github/workflows/daily-screener.yml` 的 commit 訊息必須取自 `market_date`，不得用 `date -u`（執行當下系統日期）**：若在 UTC 20:00 前手動觸發 workflow_dispatch，系統日期已經是隔天，但 `market_date` 仍是前一交易日，用系統日期當 commit 訊息會產生「commit 寫 7/1、內容卻是 6/30」的誤導標籤。commit 步驟改為讀取剛產出的 `docs/data/last_run.json` 裡的 `market_date` 欄位組成訊息，與報告內容日期保持一致。

## 程式碼慣例

- `print()` 訊息用繁體中文，格式 `[module] 說明`
- 不寫無謂注釋；只在 WHY 非顯而易見時加一行注釋
- 不新增 feature flag 或向後相容 shim，直接改程式碼
- 錯誤處理只在系統邊界（外部 API / 使用者輸入）加；內部函式信任呼叫端
- 常數用全大寫，放在模組頂部
- AI 輸出欄位的型態：`hold_period` 必須解析為整數（`_parse_hold_period` 已支援 int/float/str 輸入），Prompt 應要求 AI 直接輸出整數天數
- `tests/test_tracker.py` 覆蓋 `tracker.py` 純函式（解析、狀態機、結算、風控、watch 天數上限、B/C 新訊號處理），全數不連網、不觸碰 `data/watchlist.json`（透過 `isolate_data_dir` fixture 隔離至 `tmp_path`）。修改 `tracker.py` 的判斷邏輯或新增 DD 後，須同步補上對應測試案例並確保 `pytest` 全數通過
- `tests/test_analyzer.py` 覆蓋 `analyzer.py` 純函式（冷啟動、聚合統計、樣本門檻抑制、損壞 JSON 容錯），全數不連網、不觸碰 `data/`（透過 `isolate_data_dir` fixture 隔離至 `tmp_path`）
- `tests/test_publisher_info_sync.py` 守門 `docs/index.html` 與 `publisher._build_index()` 輸出的整檔全等（publisher.py DD-6）：任何模板改動（`_INFO_HTML`、`_CSS`、script 邏輯）漂移即測試失敗，修復方式是執行 `python src/publisher.py` 一鍵重新生成後一起 commit
- 需求不明確或有多種合理解讀時，先向用戶提問，不得臆測意圖自行擴充範圍
- 驗證程式改動用 `pytest` 或 `python main.py --dry-run --yes` 實跑，不要跑 `ast.parse` 之類的純語法檢查迴圈
- 回報 PR / issue 編號前必須先 `gh pr list` 確認實際存在，不得憑記憶引用

## 禁止事項

- **不要直接修改 `docs/` 下的 HTML**（由 `publisher.py` 生成，手動改會被下次執行覆蓋）
- **修改篩選流程、評分邏輯或策略定義後，必須判斷是否需要更新 `publisher.py` 的 `_INFO_HTML`**：前端系統說明卡片（篩選流程、L2 評分表、Regime 表、訊號追蹤狀態）是靜態字串，不會自動反映程式碼改動。凡是影響「L1/L2/L3 定義、評分條件、Regime 邊界、狀態機轉換規則」的修改，都須同步更新 `_INFO_HTML`，並執行 `python src/publisher.py` 重新生成 `docs/index.html`（同一 commit）。
- **修改 `publisher.py` 的靜態文字（如 `_INFO_HTML`、`_CSS`）後，必須執行 `python src/publisher.py` 重新生成 `docs/index.html` 並一起 commit**：pipeline 只在執行時才重新生成 HTML，修改 `publisher.py` 不會自動更新已存在的 `docs/` 檔案。`sync_index()` 是離線確定性再生成（publisher.py DD-6），不觸發任何下載或 API；`pytest` 的全等比對守門測試會攔下漏做的同步。
- **每次修改程式碼或規格後，必須同步更新 `CLAUDE.md` 與 `README.md`**：架構速覽、模組對照表、快取說明、L2 評分表、專案結構等章節若有變動，須在同一個 commit 內一併更新，不得遺留過時描述。
- **不要 commit** `.env`、`.cache/`、`.venv/`（`.gitignore` 已排除）
- **不要在 CI workflow 移除 `--dry-run`**（workflow 已設計成執行後自己 git push）
- **不得用 `datetime.now()` 決定報告日期**：`main.py` 的 `stats["date"]` 必須來自 `market_date`（SPY 最後收盤日），而非執行時的系統時鐘。CI 在 UTC 時區，台灣本地在 UTC+8，兩者 `datetime.now()` 可能不同，唯有 `market_date` 才是數據的正確時間標籤。→ 詳見設計決策 12
- **不要同時修改 `tracker.py` 和 `scorer.py`**（難以隔離問題，分次修改）
- **不要繞過規格的 Design Decisions**（DD 是已解決的設計爭議，見上方十五大決策）

## 快取說明

| 快取類型 | 路徑 | 有效期 | 略過方式 |
|----------|------|--------|----------|
| 日 K 數據 | `.cache/price_YYYYMMDD.pkl` | 當日 | `--no-cache` |
| 基本面資訊 | `.cache/info_YYYYMMDD.json` | 7 日（取最近一份） | `--no-cache` |
| AI 精選結果 | `.cache/ranked_YYYYMMDD.json` | 當日 | `--no-ai-cache` 或 `--no-cache`；CI 一律強制略過，只有本機手動執行才會複用 |
| 財報日期 | `.cache/earnings_registry.json` | 30 日（per-symbol TTL，獨立管理） | — |
| 追蹤清單 | `data/watchlist.json` | 永久（持久化） | 手動刪除 |
| 歷史績效 | `data/performance_history.json` | 永久（只增不刪） | 手動刪除 |
| AI 歷史回饋 | `data/ai_hints.json` | 每輪 Step 5.7 全量重寫（可再生衍生檔，刪除後自動重建；CI 的 `git add data/` 自動 commit） | — |
| 執行記錄 | `docs/data/last_run.json` | 每次 publish() 覆寫；含 `regime`、`market_date` 欄位供 market.py 遲滯帶讀取；前端 fetch 用於顯示「上次執行時間」與資料核實 | — |

## GitHub Actions

- 排程：週一至五 UTC 21:30（台灣時間隔日 05:30）
- Secrets：`DEEPSEEK_API_KEY` 設在 repo Settings → Secrets and variables → Actions
- 手動觸發：Actions 頁 → Daily Stock Screener → Run workflow

### 時區行為（重要）

CI 執行環境為 **UTC 時區**。這決定了報告日期的一切：

| 台灣時間（UTC+8）觸發 | 對應 UTC | yfinance 最後數據 | 報告日期 |
|----------------------|---------|-----------------|---------|
| 7/1 05:30（自動 CI）  | 6/30 21:30 | 6/30 | **6/30** ✓ |
| 7/1 07:49（手動）     | 6/30 23:49 | 6/30 | **6/30** ✓ |
| 7/2 05:30（自動 CI）  | 7/1 21:30  | 7/1  | **7/1** ✓ |

**「在台灣 7/1 觸發，卻看到 6/30 報告」是正確行為。** 美股 7/1 數據要等美股收盤後（UTC 20:00+，台灣 7/2 04:00 後）才存在；7/1 報告會由 UTC 21:30 自動 CI 生成，在台灣 7/2 05:30 才出現。

CI 不再快取 `.cache/`（見 CI 注意事項）：`daily-screener.yml` 一律 `--no-cache` 重下 price/info，同日早跑不會汙染晚間排程。

## CI 注意事項

- **CI 一律加 `--no-cache`，且不再快取 `.cache/`**：`daily-screener.yml` 的 `Run screener` 步驟固定帶 `--no-cache`（price／info／AI 精選結果快取全部跳過），workflow 也已移除 `.cache/` 的 `actions/cache` 步驟（原本以日期為鍵 `screener-data-YYYY-MM-DD`）。原因有兩層：①若同一天內先手動觸發一次（例如台灣下午 workflow_dispatch），此時 US 市場未開盤，price 快取只含前日收盤數據；若晚間 21:30 排程再跑時複用同一天的舊快取，會拿到前日數據，報告日期卡在前一交易日（實際發生：2026-07-02 早間手動跑建立含 7/1 數據的 `price_20260702.pkl`，21:30 排程跑仍輸出 7/1 報告，沒有產生 7/2 report commit）。②若同日修改 Prompt 合併後重跑，AI 快取也會汙染（同 PR #46 事件）。一天正常只排程跑一次，`--no-cache` 的額外下載成本只在同日重跑時才顯現，對正常排程執行實質差異僅是 info 快取需重取（約多數秒，可接受）；移除 `actions/cache` 步驟純粹是因為快取內容永遠用不到（`--no-cache` 已跳過讀取），保留只會佔用 Actions cache 空間。→ 詳見 `plans/2026-07-02-ci-ai-cache-staleness.md` 與 `plans/2026-07-03-ci-price-cache-staleness.md`
- **pandas-ta 已從專案移除**：pandas-ta 0.4.x 依賴 numba/llvmlite，numba 的 LLVM 初始化在 GitHub Actions Ubuntu 環境觸發 Segmentation Fault（exit 139）。`scorer.py` 已改用純 pandas 實作所有指標（EMA、RSI、MACD、ATR），**不得重新引入 pandas-ta**。
- **`yfinance<1.0.0` + `curl_cffi<0.15.0`**：`curl_cffi 0.15.0` 在 GitHub Actions Ubuntu 環境中與 Python toolchain 的 `LD_LIBRARY_PATH` 衝突，導致 Segmentation Fault。`yfinance 1.x` 要求 `curl_cffi>=0.15`，故鎖定 `yfinance<1.0.0`（0.2.66）。yfinance 0.2.66 API 完全相容（`download(group_by="ticker")`、`ticker.info`、`ticker.calendar`）。**不得移除這兩個上限**。
