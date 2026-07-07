# Market — 大盤感知規格

## Purpose

計算市場廣度與 VIX，分類當日 Regime（五象限），並提供供 AI Prompt 使用的完整產業 ETF 背景資料。

## Behavior

### 五象限 Regime 分類矩陣

| Regime | 廣度（S&P 500 > 50 SMA 比例） | VIX | L2 門檻 | 主推策略 |
|--------|-------------------------------|-----|---------|---------|
| `BULL_TREND` | ≥ 60% | < 20 | 60 分 | 動能策略 |
| `CONSOLIDATION` | 35% ~ 60% | **< 20** | 60 分 | 突破策略（積極） |
| `CONSOLIDATION_VOLATILE` | 35% ~ 60% | **≥ 20** | **65 分** | 突破策略（保守） |
| `PANIC_REVERSAL` | < 35% | ≥ 25 | 40 分 | 反轉策略 |
| `BEAR_DISTRIBUTION` | < 35% | < 25 | — | 全面防禦 |

- 廣度 35~60% 時，VIX < 20 → `CONSOLIDATION`；VIX ≥ 20 → `CONSOLIDATION_VOLATILE`
- `CONSOLIDATION_VOLATILE` 的 L2 門檻已在 `scorer.py` 的 `score_all()` 中實作（65 分）
- VIX 取最近交易日收盤價；下載失敗時預設 20.0，並設 `vix_ok=False` 通知 pipeline 跳過 L3

### 市場廣度計算

- 對 `all_stocks_data`（price_data）中每支股票，判斷最新 Close > 50 日 SMA
- 有效計算需至少 50 筆日 K；不足者跳過（不計入分母）
- 廣度 = 符合條件支數 / 有效支數 × 100

### fetch_regime_quick vs fetch_market_context

| 函數 | 執行時機 | 用途 |
|------|----------|------|
| `fetch_regime_quick()` | Step 2.5（L2 之前） | 快速取得 Regime，供 scorer 動態門檻 |
| `fetch_market_context()` | Step 5.5（L3 之前） | 完整 ETF 資料，供 AI Prompt |

- **必須**：`fetch_regime_quick()` 只使用已下載的 `price_data`（不額外發網路請求取廣度），只下載 VIX
- `fetch_market_context()` 才下載 SPY 和各產業 ETF 歷史數據

### 產業 ETF

`SECTOR_ETF_MAP`：11 個產業 → 對應 ETF 代號（XLK、XLF、XLV 等）。

每個產業 ETF 計算：
- `change_5d_pct`：5 日漲跌幅
- `change_20d_pct`：20 日漲跌幅
- `above_ema20`：最新收盤是否在 EMA20 之上（方向指示箭頭 ↑/↓）

## Interface

```python
def calculate_market_breadth(all_stocks_data: dict) -> float:
    """回傳百分比（0~100）。"""

def determine_market_regime(breadth_pct: float, vix_value: float) -> dict:
    """
    回傳 {
      "regime": str,               # BULL_TREND / CONSOLIDATION / CONSOLIDATION_VOLATILE / PANIC_REVERSAL / BEAR_DISTRIBUTION
      "ai_prompt_hint": str,       # 給 AI 的文字說明
      "primary_strategy": str,     # 動能策略 / 突破策略（積極）/ 突破策略（保守）/ 反轉策略 / ""（防禦）
    }
    """

def fetch_regime_quick(
    all_stocks_data: dict,
    last_run_path: str = "docs/data/last_run.json",
) -> tuple[str, float, float, bool]:
    """
    回傳 (regime, breadth_pct, vix_value, vix_ok)。只下載 VIX，廣度用 all_stocks_data。
    vix_ok=False 表示下載失敗，pipeline 應在 L3 前中斷。
    讀取 last_run.json 取得前一日 Regime，在廣度邊界附近啟用遲滯帶（DD-4）。
    """

def fetch_market_context(
    candidate_sectors: set[str],
    all_stocks_data: dict,
) -> dict:
    """
    回傳完整大盤背景，供 _build_prompt() 使用。
    含 regime, market_breadth_pct, vix, sp500, sectors。
    SPY/產業 ETF 優先從 all_stocks_data（Step 2 已下載）複用，不重複下載；
    僅缺失的 ticker 才個別補抓，^VIX 一律單獨下載（DD-6）。
    """
```

## Design Decisions

### DD-1: Step 2.5 快速 Regime 與 Step 5.5 完整大盤拆分

- **選擇**：Regime 判定分成兩個時機執行
- **原因**：Regime 必須在 L2 scorer 之前取得（供動態門檻用），但完整 ETF 下載耗時，且 L2 完成前不知道哪些產業 ETF 需要下載。`fetch_regime_quick()` 複用已下載的 price_data 算廣度，只額外下載 VIX，幾乎無額外耗時。
- **捨棄**：在 Step 1 就下載完整大盤（耗時、且阻塞 S&P 500 下載）；在 L3 才取 Regime（scorer 無法動態調整門檻）

### DD-2: VIX 下載失敗預設 20.0

- **選擇**：`^VIX` 下載失敗時使用 20.0 作為預設值
- **原因**：VIX 20 是「正常波動」的中間值，20 以下 BULL，25 以上 PANIC。預設 20 讓 Regime 退化至由廣度主導（廣度高→BULL_TREND；廣度低→BEAR_DISTRIBUTION），是最保守的選擇。
- **捨棄**：預設 0（誤判所有情境為 BULL）；預設 30（誤判為 PANIC，過度激進）

### DD-4: CONSOLIDATION_VOLATILE — 高 VIX 整理期獨立象限

- **選擇**：廣度 35~60% 且 VIX ≥ 20 時，不再歸類為 CONSOLIDATION，改為 `CONSOLIDATION_VOLATILE`
- **原因**：原始 CONSOLIDATION 無視 VIX，導致在 VIX=28 的高恐慌整理期仍套用「積極突破」策略。高 VIX 代表市場波動性高，假突破風險大，應要求更嚴格的確認訊號（L2 門檻提高至 65 分、AI 指引更保守）
- **L2 門檻影響**：scorer.py 的 `score_all()` 已實作：`CONSOLIDATION_VOLATILE → effective_min = 65.0`
- **AI Prompt 影響**：ranker.py 的 `_REGIME_EXTRA_HINT` 在 CONSOLIDATION_VOLATILE 時注入額外保守提示
- **邊界**：VIX = 20 為閾值（正常波動上限）；VIX 下降回 < 20 時，下一日自動恢復 CONSOLIDATION（若廣度未變）
- **捨棄**：廣度 35~60% 全部歸入 CONSOLIDATION（VIX 高時策略過於激進）；以 VIX=25 為界（太寬鬆，VIX 20~25 的震盪期就應保守）

### DD-5: Regime 廣度遲滯帶（Hysteresis）— 防止邊界翻轉

- **選擇**：`fetch_regime_quick()` 讀取前一日 `last_run.json` 的 `regime`，在廣度邊界附近（±2%）維持前日 Regime，防止每日翻轉
  - **校驗**：`last_run["market_date"] < current_market_date` 才生效（同日重複執行時忽略，防止污染）
  - **VIX 結構性跨界例外**：若新舊 Regime 的 VIX 屬性不同（一個在高 VIX 組 PANIC/CONSOLIDATION_VOLATILE，另一個在低 VIX 組），則 VIX 已跨越結構邊界，不得維持舊 Regime
  - **實作**：`HYSTERESIS = 2.0`；`abs(breadth - 60.0) <= 2.0 OR abs(breadth - 35.0) <= 2.0` 且 VIX 未跨界 → 沿用 `prev_regime`
- **原因**：廣度在 58%~62% 之間震盪時，BULL/CONSOLIDATION 每日切換，L2 門檻（60 vs 65 分）和 AI 策略文字跟著變，選股邏輯不穩定。遲滯帶讓系統「等廣度明確離開邊界後才切換」
- **last_run.json 新增欄位**：`publisher.py` 的 `publish()` 在每次執行結束後，在 `last_run.json` 內額外寫入 `regime`（str）與 `market_date`（str），供下次執行讀取
- **捨棄**：每日重算（邊界抖動）；用獨立狀態檔（增加 I/O 複雜度）；N 日廣度均值（DD-3 已實作，但均值平滑不夠強，邊界 ±1-2% 仍可能觸發切換）

### DD-3: 廣度使用 N 日滾動平均，防止邊界震盪

- **選擇**：`calculate_market_breadth()` 新增 `smoothing_days`（預設 `BREADTH_SMOOTHING_DAYS=3`），回傳近 N 個交易日廣度的算術平均，而非單日值
- **原因**：廣度在 35%/60% 邊界附近每日小幅震盪（如 34%→36%→34%），會導致 Regime 日日翻轉，L2 門檻（40 vs 60 分）與 L3 主推策略跟著變，選股邏輯不穩定。N 日平均以現有 price_data 的歷史切片計算，不增加任何額外 API 請求。
- **計算方式**：對 `offset = 0, 1, ..., N-1`，各取 `close.iloc[: len(close) - offset]`（模擬 N 天前視角），以最後 50 根算 SMA50，判定該日廣度，最後取算術平均。今日原始廣度（offset=0）仍單獨 print 供觀察。
- **回傳值不變**：`fetch_regime_quick()` 仍回傳 `(regime, breadth_pct, vix_value, vix_ok)`；`breadth_pct` 改為 N 日均值（更穩定），Step 5.5 照常複用
- **捨棄**：單日廣度（邊界震盪）；外部狀態檔儲存歷史廣度（增加 I/O，且現有 price_data 已含所需 90 日歷史）；滯後帶（hysteresis band）——需記憶「前次 Regime」狀態，引入狀態依賴，實作更複雜且直覺性低

### DD-6: fetch_market_context 複用 Step 2 已下載的 SPY/產業 ETF 資料，不重複下載

- **選擇**：`fetch_market_context()` 優先從 `all_stocks_data`（pipeline Step 2 批次下載的 `price_data`，含 SPY 與全部 11 個板塊 ETF、90 日歷史）讀取 SPY 與候選產業 ETF 的走勢，只有當某個 ticker 不在 `all_stocks_data` 中才個別發出下載請求；`^VIX` 因為從未包含在 Step 2 的下載範圍內，一律單獨下載。
- **原因**：Step 2（`fetcher.fetch_batch`）已經下載過 SPY 與全部板塊 ETF（90 日），`fetch_market_context()` 原本卻又對同一批 ticker 重新發出一次 60 日的 `yf.download`，純屬重複下載、浪費 API 配額與執行時間，且與 pipeline 呼叫端唯一的傳入值 `all_stocks_data=price_data`（見 `specs/pipeline.md`）已經提供了所需資料互相矛盾。
- **實作**：`missing_tickers = [t for t in ({"SPY"} | set(sectors_to_fetch.values())) if t not in all_stocks_data]`；下載清單改為 `["^VIX"] + missing_tickers`；`_get(ticker)` 優先回傳 `all_stocks_data[ticker]`，找不到才退化為原本的下載結果查詢。
- **向下相容**：`all_stocks_data` 為 `None` 或未提供時，`missing_tickers` 等於全部 SPY/ETF，行為與 DD-6 之前完全相同（一律下載）。
- **捨棄**：完全移除獨立下載、要求呼叫端保證 `all_stocks_data` 一定含有全部所需 ticker（過於僵化，任何未來新增產業或 Step 2 下載失敗的邊界情況都會直接壞掉；保留「缺失才補抓」的退化路徑更穩健）。

### DD-7: `_analyze()` 對缺失 Close 欄位防呆，避免單一 ETF 異常拖垮整個大盤背景

- **選擇**：`_analyze(df)` 開頭新增 `if df is None or df.empty or "Close" not in df.columns: return {}`；VIX 區塊的 `vix_df["Close"]` 存取前也加上 `"Close" in vix_df.columns` 檢查。
- **原因**：2026-07-06 CI 執行時，515 支請求 ticker 中有 1 支（板塊 ETF）在 Step 2 下載失敗，`fetch_market_context()` 補抓時該 ticker 再次失敗，`_get()` 依既有防呆回傳完全無欄位的空 `pd.DataFrame()`；但 `_analyze()` 內的 `df["Close"].dropna()` 沒有任何防呆，直接拋出 `KeyError: 'Close'`，且此例外未被 `_analyze()` 自己捕捉，而是一路往外傳到 `fetch_market_context()` 唯一的外層 `try/except`，導致當天已成功抓到的 SPY、VIX、其餘 10 支正常板塊 ETF 資料全數被丟棄，`market_context` 整個退化為 `{}`。後果連鎖：`last_run.json` 的 `regime` 欄位變成空字串；更嚴重的是這個空 `market_context` 被原封不動送進 Step 6 的 AI Prompt，讓 DeepSeek 在完全缺少大盤環境背景的情況下做判斷，當天 AI 回傳空選股清單（間接觸發 `ranker.py` DD-18 的 fallback 降級）。
- **設計原則**：任何單一 ticker 的資料異常只應影響該 ticker 自身的欄位（`sp500`/`vix`/`sectors[X]` 其中之一缺席），不得讓整個 `fetch_market_context()` 函式層級的例外吞掉其他 ticker 已經抓到的正常資料。
- **捨棄**：在 `fetch_market_context()` 內對每個 `_analyze()` 呼叫個別包 `try/except`（治標不治本，`_analyze()` 本身仍是一個對外部輸入零防禦的函式，未來任何新呼叫點都會重踩同樣的坑）；改讓 `_get()` 保證回傳值一定含 `Close` 欄位（需要對 `yf.download` 各種失敗形態做窮舉分類，複雜度遠高於在消費端做一行防呆）。
- → 詳見 [plans/2026-07-07-market-context-single-etf-resilience.md](../plans/2026-07-07-market-context-single-etf-resilience.md)

## Acceptance Criteria

- [ ] 廣度=70%、VIX=15 → `BULL_TREND`
- [ ] 廣度=50%、VIX=18 → `CONSOLIDATION`（廣度 35~60%、VIX < 20）
- [ ] 廣度=50%、VIX=22 → `CONSOLIDATION_VOLATILE`（廣度 35~60%、VIX ≥ 20）
- [ ] 廣度=50%、VIX=30 → `CONSOLIDATION_VOLATILE`（廣度 35~60%、VIX ≥ 20，即便 VIX 高也不切換到 PANIC）
- [ ] 廣度=25%、VIX=28 → `PANIC_REVERSAL`
- [ ] 廣度=25%、VIX=18 → `BEAR_DISTRIBUTION`
- [ ] `fetch_regime_quick()` 不對廣度計算發出任何 yfinance 請求（廣度只用 price_data）
- [ ] 市場背景中產業 ETF 含 `change_5d_pct` 和 `change_20d_pct` 兩個欄位
- [ ] 廣度今日=34%、昨日=37%、前日=38% → 3日均=36.3% → Regime=CONSOLIDATION（不誤切換至 BEAR）
- [ ] 廣度連續 3 日均 < 35% → Regime 切換至 BEAR_DISTRIBUTION 或 PANIC_REVERSAL（依 VIX）
- [ ] print 訊息同時顯示今日原始廣度（含 above/total）與 N 日均值
- [ ] 廣度=61%（前日 BULL_TREND）→ 廣度=59%（今日，落在遲滯帶 ±2%）→ 不切換，維持 BULL_TREND
- [ ] 廣度=59%（前日 BULL_TREND）→ VIX 從 18 升至 28（跨越結構邊界）→ 不套用遲滯，重新計算 Regime
- [ ] last_run.json 不存在或 market_date 非昨日 → prev_regime=None，不啟用遲滯
- [ ] **DD-6 複用已下載資料**：`all_stocks_data` 含 SPY 與候選產業 ETF 時，`fetch_market_context()` 觸發的 `yf.download` 只請求 `^VIX`
- [ ] **DD-6 部分缺失時補抓**：`all_stocks_data` 缺少某個候選產業 ETF 時，下載清單應包含 `^VIX` 與該缺失的 ticker，不含已存在的 SPY
- [ ] **DD-6 向下相容**：未提供 `all_stocks_data` 時，下載清單應包含 `^VIX`、`SPY` 與全部候選產業 ETF（行為與 DD-6 之前相同）
- [ ] **DD-7 單一 ETF 失敗不拖垮大盤背景**：某支候選產業 ETF 補抓後仍回傳無 `Close` 欄位的空 DataFrame → `fetch_market_context()` 不拋錯，`sp500`/`vix`/其他正常 ETF 與 `regime` 判定皆正常回傳，僅該支 ETF 缺席於 `context["sectors"]`
