# L3 AI 選股加入基本面多維度判斷（ranker.py DD-14）

> 狀態：已執行完成，對應分支 `feat/l3-fundamental-dimensions`

## Context

使用者觀察到：`src/ranker.py` 的 L3 階段（DeepSeek AI 精選）送給 AI 的候選池表格（25 欄）與
`SYSTEM_PROMPT` 幾乎 100% 是技術指標（均線、量能、RSI、MACD、動能、RS_vs_Sector 等）。這在架構上
是有道理的問題：L1（流動性硬篩）與 L2（技術評分 100 分制）已經把候選股篩到只剩「技術面強勢」的股票，
AI 在 L3 拿到的這批股票本來就已經技術面同質化很高，此時如果 AI 判斷依據仍然只有技術面，等於是在同一批
已經技術達標的股票裡，比誰的技術指標數字更漂亮一點，缺乏「這間公司體質到底好不好、值不值得買」的另一
個維度。

目標：讓 L3 從「技術指標裁判」升級為「技術面已達標前提下，用基本面做最終取捨」。

探索確認的關鍵事實：
- `fetcher.py:fetch_info()`（217-245 行）已經對每支股票呼叫 `yf.Ticker(sym).info` 抓回完整字典，原本
  只挑出 `market_cap`/`sector`/`name`/`fifty_two_week_high`/`fifty_two_week_low`/`earnings_date` 六個
  欄位存入 `info_data`。新增欄位不需要多打任何 API，只是從已經下載的同一個 dict 多挑幾個 key。
- `filter.py` 的 L1 已經用 `market_cap` 做硬篩（$300M 門檻），這是既有基本面篩選，本次不重複、不更動。
- `ranker.py:_generate_candidates_markdown_table()`（293-410 行）是唯一組表格的地方；`info_data` 原本
  只被拿來取 `sector` 與 `fifty_two_week_high`，其餘完全沒用。
- `specs/ranker.md` 的 DD-12（動能策略買進區間結構化）、DD-13（突破/反轉策略買進區間結構化）已經把每個
  策略的 `buy_zone` 算法錨定在具體的技術指標數值上（EMA10/20、High_20D、EMA50、Low_20D 等）——本次修改
  不能動這些邏輯，基本面只作為技術面已成立後的取捨依據，不推翻策略型買進區間規則。
- `MAX_CANDIDATES_TO_AI = 40`（ranker.py:21），加 3 個基本面欄位後表格膨脹約 12%，DeepSeek 的 context
  window 綽綽有餘，不是問題。
- 既有的 `Beta_60D = N/A → 不排除` 是可複用的先例：基本面缺值時應該「保留、標記 N/A、讓 AI 自行降權」，
  不應該比照 `Earnings_Days_Left = N/A → 直接排除` 的硬性排除（yfinance 的估值/成長率欄位缺值率遠高於
  財報日期，硬性排除會不合理地淘汰太多股票）。

## 考慮過的方案

**選項 A（採用）**：在 L3 候選池表格新增三個基本面欄位（估值 `Fwd_PE`、獲利品質 `Profit_Margin`、成長性
`Rev_Growth_YoY`），並在 `SYSTEM_PROMPT` 新增一條選股原則，把基本面定位成「技術面強度相近時的取捨依據
與風險旗標」，折疊進既有的 `reason`/`risk`/`confidence` 欄位，不新增 JSON 輸出欄位、不修改 DD-12/DD-13
的買進區間算法。

**選項 B（討論後排除）**：加入分析師共識維度（`targetMeanPrice` 換算隱含漲幅、`recommendationKey` 評等
方向）。使用者在方案討論階段明確表示不需要參考這項資訊，故從範圍中移除。

**選項 C（捨棄）**：在 L2 加一個基本面分數維度，讓基本面直接影響 100 分制的技術評分。
捨棄原因：L2 目前是純技術評分（六維度：MA/RSI/MACD/Volume/Momentum/RS），語意單純、門檻動態調整
（scorer.py 依 Regime 調整 60/40/65 分門檻）都是圍繞「技術強度」設計的。硬塞基本面分數進去，需要重新
校準六個維度的權重分配，且會混淆 L2「純技術篩選」的既有定位——L2 應該只負責「技術面夠不夠格」，基本面
取捨留給 L3 的 AI 綜合判斷更合理。

**選項 D（捨棄）**：新增獨立 JSON 輸出欄位，例如 `fundamental_reason`。
捨棄原因：現有 `reason`（選股理由）與 `risk`（風險提示）已經足以承載基本面判斷依據，沒必要為了「基本面」
這個新維度單獨開一個欄位。新增欄位會牽動 `tracker.py`/`publisher.py` 對這份 JSON 的既有解析假設，且與
`reason`/`risk` 語意重疊，維護成本大於效益。

選擇選項 A 的原因：三個欄位（估值/獲利品質/成長性）互補、不重疊，且都能從既有 `fetch_info()` 呼叫免費
取得，不增加下載成本；改動範圍集中在 `fetcher.py` 的欄位抽取與 `ranker.py` 的表格/Prompt 文字，完全不
碰 JSON schema 與 DD-12/DD-13 的買進區間邏輯，風險最低。

## 執行內容

### 1. `src/fetcher.py` — `fetch_info()`

在 `info_map[sym] = {...}` 字典裡新增 `forward_pe`（`forwardPE` 缺值 fallback `trailingPE`）、
`profit_margin`（`profitMargins`）、`revenue_growth`（`revenueGrowth`）三個欄位。API 失敗的 fallback
分支維持現狀不動，缺值時下游 `.get()` 自然回傳 `None`，沿用現有慣例。

### 2. `src/ranker.py` — `_generate_candidates_markdown_table()`

在現有 25 欄最後（`Earnings_Days_Left` 之後）追加 3 欄：`Fwd_PE | Profit_Margin | Rev_Growth_YoY`。
缺值一律印 `"N/A"`，不拋錯、不排除該股。`Profit_Margin`/`Rev_Growth_YoY` 因 yfinance 回傳小數（如
0.23），格式化為百分比字串。函式 docstring 的欄位數量描述同步修正（15→28，原本的「15 欄」本來就已經是
過時文件債務，這次一併修正）。

### 3. `src/ranker.py` — `SYSTEM_PROMPT` 與 `_build_prompt()` 欄位定義

- 「欄位定義」段落新增三個新欄位的說明
- 開場白調整：明確告知 AI 候選池已經過技術強度篩選、應疊加基本面做最終取捨
- 「選股原則」新增第 7 條：技術面同等強度時優先選基本面健康的個股；基本面空心不直接排除，而是降低
  `confidence` 並在 `risk` 中說明
- `N/A 差異化處理` 新增一條：三個基本面欄位缺值不排除
- `reason` 欄位說明文字調整為「綜合技術面與基本面優勢」
- DD-12/DD-13 的策略買進區間規則段落**未修改**

### 4. 文件化

- `specs/ranker.md`：新增三欄定義、N/A 規則、DD-14
- 本文件（`plans/2026-07-02-fundamental-dimension-l3-prompt.md`）
- `CLAUDE.md`：新增設計決策摘要，修正過時的「15 欄」欄位數描述
- `README.md`：Step 6 說明段落補一句基本面欄位說明

## 驗證

沒有 `tests/` 目錄，延續現有手動驗證慣例：
1. 語法檢查（`ast.parse`）確認 `fetcher.py`/`ranker.py` 無語法錯誤
2. 執行 `python main.py --dry-run --yes --no-ai-cache`，確認流程不中斷、`selections` JSON 正常解析，
   `buy_zone`/`stop_loss`/`hold_period` 格式與數值合理（DD-12/13 邏輯未受基本面干擾）
3. 驗證產生的測試副作用（`docs/`、`data/watchlist.json`、`.cache/ranked_*.json`）已還原，不納入 commit
