# Ranker — L3 AI 精選規格

## Purpose

將 L2 候選股透過結構化 XML Prompt 送給 DeepSeek AI，依當日 Regime 主推策略選出最多 5 支，附帶具體的買入區間、目標價、止損、持有週期與策略理由。

## Behavior

### Prompt 結構（XML 三區塊）

```xml
<Market_Regime>
  大盤環境描述（Regime、廣度、VIX、SPY 位置、產業 ETF 漲跌）
</Market_Regime>

<Candidate_Pool>
  Markdown 表格（L2 候選股）
  欄位定義
</Candidate_Pool>

<Output_Constraint>
  主推策略、選股數量上限、輸出格式要求
</Output_Constraint>
```

### Markdown 候選池表格欄位（12 欄）

| 欄位 | 說明 |
|------|------|
| Ticker | 股票代號 |
| Close_Price | 最新收盤價 |
| Sector | 產業（縮寫後） |
| L2_Score | L2 總分 |
| Strategy_Tag | 系統預判策略（僅供參考） |
| MA_Trend | BULL_1/BULL_2/MIXED/BEAR |
| RSI | RSI 數值 |
| MACD_Hist | POS_INC/POS_DEC/NEG_INC/NEG_DEC |
| Vol_Ratio | 當日量 ÷ 30 日均量 |
| Price_5D_Pct | 5 日漲跌幅（短線爆發力） |
| Price_20D_Pct | 20 日漲跌幅（中線趨勢） |
| 52W_High_Dist | 距 52 週高點百分比 |

### 產業 ETF 格式

```
産業ETF（5日/20日漲跌）：Technology(XLK) 5日=+1.2% 20日=+3.5%↑  ...
```

- **必須**：同時顯示 5 日與 20 日漲跌，讓 AI 判斷短中線方向是否一致
- **不得**：只顯示 5 日（單一時間維度無法判斷動能是否延續）

### BEAR_DISTRIBUTION 特殊行為

- **若** Regime = `BEAR_DISTRIBUTION`：直接回傳空列表 `[]`，不送 AI 請求
- 輸出到 publisher 的 `ranked` 為空列表，報告顯示「全面防禦」提示

### AI 輸出格式

AI 必須回傳 JSON array，每筆含：

```json
{
  "symbol": "AAPL",
  "name": "Apple Inc.",
  "sector": "Technology",
  "buy_zone": "$185~$188",
  "target": "$200",
  "stop_loss": "$180",
  "hold_period": "5-10 個交易日",
  "strategy": "動能策略",
  "reason": "..."
}
```

### 候選股傳送上限

- 最多傳送 `MAX_CANDIDATES_TO_AI`（預設 30）支給 AI，避免超過 context limit

## Interface

```python
def rank_candidates(
    candidates: list[dict],       # scorer 輸出，含 total_score
    price_data: dict[str, pd.DataFrame],
    info_data: dict[str, dict],
    top_n: int = 5,
    market_context: dict | None = None,
) -> list[dict]:
    """回傳 AI 精選結果，BEAR_DISTRIBUTION 時回傳 []。"""

def compute_indicators(sym: str, df: pd.DataFrame) -> dict:
    """
    計算單支股票指標供 Markdown 表格使用。
    回傳含 price, ema5/10/20/50, rsi, volume_ratio,
    change_1d_pct, change_5d_pct, change_20d_pct。
    """

def _build_prompt(...) -> str:
    """組裝 XML Prompt。"""

def _generate_candidates_markdown_table(...) -> str:
    """生成 12 欄 Markdown 表格字串。"""
```

## Design Decisions

### DD-1: XML 三區塊結構

- **選擇**：`<Market_Regime>` / `<Candidate_Pool>` / `<Output_Constraint>` 分離
- **原因**：讓 AI 先理解市場環境，再看候選股，最後看約束條件，符合人類分析推理的自然順序。XML 標籤讓 AI 能精確定位每個區塊的語意，減少混淆。
- **捨棄**：純文字描述（AI 難以區分哪段是背景、哪段是數據、哪段是指令）

### DD-2: Price_5D_Pct 與 Price_20D_Pct 同時提供

- **選擇**：12 欄表格同時含 5 日和 20 日漲跌
- **原因**：5 日看短線爆發力（是否正在啟動），20 日看中線趨勢（大方向對不對）。只有 20 日無法判斷是否剛開始啟動；只有 5 日無法判斷大趨勢方向。
- **捨棄**：只有 Price_20D_Pct（無法判斷近期啟動動能）

### DD-3: Strategy_Tag 為參考，非強制

- **選擇**：`Strategy_Tag` 欄標記「系統預判策略（僅供參考）」
- **原因**：L2 系統預判可能不準，AI 有更多脈絡可做更好的判斷。強制 AI 只選系統標記的策略會降低 AI 的推理空間。
- **捨棄**：只傳送符合主推策略的候選股（過濾過嚴，AI 無法發現例外）

## Acceptance Criteria

- [ ] Prompt 中 `<Market_Regime>` 區塊含有 `5日` 與 `20日` 漲跌的產業 ETF 資訊
- [ ] Markdown 表格 header 含 `Price_5D_Pct` 欄（在 `Price_20D_Pct` 之前）
- [ ] BEAR_DISTRIBUTION Regime → `rank_candidates()` 回傳 `[]`，不發出 API 請求
- [ ] AI 輸出包含 `buy_zone`（格式 `$X~$Y`）、`stop_loss`、`hold_period`
- [ ] `compute_indicators()` 回傳 dict 含 `change_5d_pct` key
