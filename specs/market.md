# Market — 大盤感知規格

## Purpose

計算市場廣度與 VIX，分類當日 Regime（四象限），並提供供 AI Prompt 使用的完整產業 ETF 背景資料。

## Behavior

### 四象限 Regime 分類矩陣

| Regime | 廣度（S&P 500 > 50 SMA 比例） | VIX |
|--------|-------------------------------|-----|
| `BULL_TREND` | ≥ 60% | < 20 |
| `CONSOLIDATION` | 35% ~ 60%（任意 VIX） | — |
| `PANIC_REVERSAL` | < 35% | ≥ 25 |
| `BEAR_DISTRIBUTION` | < 35% | < 25 |

- **CONSOLIDATION** 優先級高於另兩個廣度 < 35% 的象限（當廣度在 35-60% 時，不論 VIX 高低都是 CONSOLIDATION）
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
      "regime": str,               # BULL_TREND / CONSOLIDATION / PANIC_REVERSAL / BEAR_DISTRIBUTION
      "ai_prompt_hint": str,       # 給 AI 的文字說明
      "primary_strategy": str,     # 動能策略 / 突破策略 / 反轉策略 / ""（防禦）
    }
    """

def fetch_regime_quick(all_stocks_data: dict) -> tuple[str, float, float, bool]:
    """回傳 (regime, breadth_pct, vix_value, vix_ok)。只下載 VIX，廣度用 all_stocks_data。
    vix_ok=False 表示下載失敗，pipeline 應在 L3 前中斷。"""

def fetch_market_context(
    candidate_sectors: set[str],
    all_stocks_data: dict,
) -> dict:
    """
    回傳完整大盤背景，供 _build_prompt() 使用。
    含 regime, market_breadth_pct, vix, sp500, sectors。
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

### DD-3: 廣度使用 N 日滾動平均，防止邊界震盪

- **選擇**：`calculate_market_breadth()` 新增 `smoothing_days`（預設 `BREADTH_SMOOTHING_DAYS=3`），回傳近 N 個交易日廣度的算術平均，而非單日值
- **原因**：廣度在 35%/60% 邊界附近每日小幅震盪（如 34%→36%→34%），會導致 Regime 日日翻轉，L2 門檻（40 vs 60 分）與 L3 主推策略跟著變，選股邏輯不穩定。N 日平均以現有 price_data 的歷史切片計算，不增加任何額外 API 請求。
- **計算方式**：對 `offset = 0, 1, ..., N-1`，各取 `close.iloc[: len(close) - offset]`（模擬 N 天前視角），以最後 50 根算 SMA50，判定該日廣度，最後取算術平均。今日原始廣度（offset=0）仍單獨 print 供觀察。
- **回傳值不變**：`fetch_regime_quick()` 仍回傳 `(regime, breadth_pct, vix_value, vix_ok)`；`breadth_pct` 改為 N 日均值（更穩定），Step 5.5 照常複用
- **捨棄**：單日廣度（邊界震盪）；外部狀態檔儲存歷史廣度（增加 I/O，且現有 price_data 已含所需 90 日歷史）；滯後帶（hysteresis band）——需記憶「前次 Regime」狀態，引入狀態依賴，實作更複雜且直覺性低

## Acceptance Criteria

- [ ] 廣度=70%、VIX=15 → `BULL_TREND`
- [ ] 廣度=50%、VIX=30 → `CONSOLIDATION`（廣度在 35-60% 區間，無視 VIX）
- [ ] 廣度=25%、VIX=28 → `PANIC_REVERSAL`
- [ ] 廣度=25%、VIX=18 → `BEAR_DISTRIBUTION`
- [ ] `fetch_regime_quick()` 不對廣度計算發出任何 yfinance 請求（廣度只用 price_data）
- [ ] 市場背景中產業 ETF 含 `change_5d_pct` 和 `change_20d_pct` 兩個欄位
- [ ] 廣度今日=34%、昨日=37%、前日=38% → 3日均=36.3% → Regime=CONSOLIDATION（不誤切換至 BEAR）
- [ ] 廣度連續 3 日均 < 35% → Regime 切換至 BEAR_DISTRIBUTION 或 PANIC_REVERSAL（依 VIX）
- [ ] print 訊息同時顯示今日原始廣度（含 above/total）與 N 日均值
