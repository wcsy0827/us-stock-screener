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
- VIX 取最近交易日收盤價；下載失敗時預設 20.0

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

def fetch_regime_quick(all_stocks_data: dict) -> tuple[str, float, float]:
    """回傳 (regime, breadth_pct, vix_value)。只下載 VIX，廣度用 all_stocks_data。"""

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

## Acceptance Criteria

- [ ] 廣度=70%、VIX=15 → `BULL_TREND`
- [ ] 廣度=50%、VIX=30 → `CONSOLIDATION`（廣度在 35-60% 區間，無視 VIX）
- [ ] 廣度=25%、VIX=28 → `PANIC_REVERSAL`
- [ ] 廣度=25%、VIX=18 → `BEAR_DISTRIBUTION`
- [ ] `fetch_regime_quick()` 不對廣度計算發出任何 yfinance 請求（廣度只用 price_data）
- [ ] 市場背景中產業 ETF 含 `change_5d_pct` 和 `change_20d_pct` 兩個欄位
