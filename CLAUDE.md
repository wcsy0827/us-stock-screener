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
  ↓ Step 4   filter.py       L1 流動性硬篩（股價/日成交額/市值/交易天數）
  ↓ Step 4.5 earnings.py     Tier 3 精準補抓（僅對流動性篩選後倖存個股）
             filter.py       財報防禦牆（排除 5 天內有財報的個股）
  ↓ Step 5   scorer.py       L2 技術評分（六維度 100 分；動態門檻依 Regime；相對強度 RS 維度）
                              CONSOLIDATION_VOLATILE 門檻 65 分，PANIC_REVERSAL 40 分
  ↓ Step 5.5 market.py       完整大盤 ETF 背景（直接複用 Step 2.5 的廣度與 VIX，不重算）
  ↓ Step 6   ranker.py       L3 DeepSeek AI 精選（≤5 支；28 欄 Markdown 表含 RS_vs_Sector 與基本面欄位；每產業 ≤8 支）
             tracker.py      訊號追蹤（watchlist.json）→ 結算歸檔（performance_history.json）
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

## 十五個最重要的設計決策（摘要）

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

## 禁止事項

- **不要直接修改 `docs/` 下的 HTML**（由 `publisher.py` 生成，手動改會被下次執行覆蓋）
- **修改篩選流程、評分邏輯或策略定義後，必須判斷是否需要更新 `publisher.py` 的 `_INFO_HTML`**：前端系統說明卡片（篩選流程、L2 評分表、Regime 表、訊號追蹤狀態）是靜態字串，不會自動反映程式碼改動。凡是影響「L1/L2/L3 定義、評分條件、Regime 邊界、狀態機轉換規則」的修改，都須同步更新 `_INFO_HTML`，並手動同步 `docs/index.html`（同一 commit）。
- **修改 `publisher.py` 的靜態文字（如 `_INFO_HTML`）後，必須同步手動更新 `docs/index.html`**：pipeline 只在執行時才重新生成 HTML，修改 `publisher.py` 不會自動更新已存在的 `docs/` 檔案，GitHub Pages 畫面不會立即反映。例外：若能馬上執行 `python main.py --dry-run --yes` 並將產出的 `docs/` 一起 commit，則不需要手動改。
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

`.cache/` 的 GitHub Actions cache key 也以 `date -u`（UTC 日期）為鍵，與 Python 的 `market_date` 在正常情境下一致。

## CI 注意事項

- **CI 一律加 `--no-ai-cache`**：`daily-screener.yml` 的 `Run screener` 步驟固定帶 `--no-ai-cache`（price/info 快取不受影響，只跳過 AI 精選結果快取）。原因：`.cache/` 的 GitHub Actions cache key 只用日期（`screener-data-YYYY-MM-DD`），沒有綁定程式碼版本。若同一天內「先跑一次 → 修改 `ranker.py` 的 Prompt → 合併 → 手動重跑」，第二次執行會直接撿到第一次用舊 Prompt 產生的 `ranked_YYYYMMDD.json`，導致 hotfix 合併後仍發布舊結果（實際發生過：PR #46 止損緩衝修正合併後，同日重跑仍輸出止損=買入區間下緣的舊資料）。一天正常只排程跑一次，AI 快取本來就是當日首次 miss，加這個旗標對正常排程執行零差異，只在同日重跑時強制拿到當前程式碼版本的最新判斷。→ 詳見 `plans/2026-07-02-ci-ai-cache-staleness.md`
- **pandas-ta 已從專案移除**：pandas-ta 0.4.x 依賴 numba/llvmlite，numba 的 LLVM 初始化在 GitHub Actions Ubuntu 環境觸發 Segmentation Fault（exit 139）。`scorer.py` 已改用純 pandas 實作所有指標（EMA、RSI、MACD、ATR），**不得重新引入 pandas-ta**。
- **`yfinance<1.0.0` + `curl_cffi<0.15.0`**：`curl_cffi 0.15.0` 在 GitHub Actions Ubuntu 環境中與 Python toolchain 的 `LD_LIBRARY_PATH` 衝突，導致 Segmentation Fault。`yfinance 1.x` 要求 `curl_cffi>=0.15`，故鎖定 `yfinance<1.0.0`（0.2.66）。yfinance 0.2.66 API 完全相容（`download(group_by="ticker")`、`ticker.info`、`ticker.calendar`）。**不得移除這兩個上限**。
