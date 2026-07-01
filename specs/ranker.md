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

### Markdown 候選池表格欄位（15 欄）

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
| VTF_Score | 量能推進因子：`max(-5.0, Vol_Ratio × (2×K_pos − 1))`，下限 -5.0、上限不設；`.round(2)`；分母為零時安全降級 `vol_ratio = 1.0`；正值=帶量推進（> 5.0 為史詩級機構建倉），負值=高檔派發；缺值填 `N/A` |
| Price_5D_Pct | 5 日漲跌幅（短線爆發力） |
| Momentum_ATR | ATR 標準化動能：20 日價格位移 ÷ 14 日 ATR；`.round(2)`；缺值填 `N/A` |
| RS_vs_Sector | 個股 5 日報酬率 − 板塊 ETF 5 日報酬率（百分比）；`.round(1)`；板塊 ETF 來自 `SECTOR_ETF_MAP`，缺資料 fallback SPY；ETF 數據不足 5 日填 `N/A` |
| 52W_High_Dist | 距 52 週高點百分比 |
| Beta_60D | 個股 60 日 Beta vs SPY；完整序列 inner join + NaN 清洗後取末 60 日計算；`.round(2)`；**缺值填 `N/A`，不觸發 AI 排除** |
| Earnings_Days_Left | 距下次財報日曆天數（基準日 = SPY 最後交易日）；安全無近期財報填 `99`；數據斷裂（完全查不到財報歷史）填 `N/A` |

### 策略指引（System Prompt 約束）

**動能策略（Momentum）**：優先挑選 `Momentum_ATR >= 2.0` 且 `VTF_Score > 1.0` 的標的，跨行業公平挑選，不以絕對漲幅百分比作為依據。

**突破策略（Breakout）**：強烈關注 `VTF_Score >= 1.5` 且股價在 20 日高點附近的標的。`VTF_Score < 0` 一律視為假突破派發陷阱，禁止入選。

**反轉策略（Oversold Reversal）**：優先挑選 `Momentum_ATR <= -2.0`（代表個股跌幅跨越 2 個標準真實波幅，極端超賣）且 `VTF_Score` 由負轉正或向 0 軸收斂（拋壓衰竭或低檔主力承接信號）的標的。

**VTF_Score 解讀**：VTF_Score 無上限，數值越大代表機構推進力越強；`> 5.0` 應視為史詩級建倉訊號，必須認真對待。

**PANIC_REVERSAL 低分豁免**：PANIC_REVERSAL 環境下，帶有 REVERSAL 標籤的個股 L2_Score 偏低是**正常且符合預期**的（暴跌期均線、MACD、RSI 各項指標本就偏低）。AI 必須忽略低分偏見，專注審查 Momentum_ATR 是否呈現拋壓衰竭（從深度負值向 0 軸收斂）。

**N/A 差異化處理規則**：
- `Earnings_Days_Left = N/A` → **直接排除**（數據斷裂，財報時間未知，黑天鵝風險無法評估）
- `Momentum_ATR = N/A` 或 `VTF_Score = N/A` → **直接排除**（技術數據不足）
- `Beta_60D = N/A` → **不排除**，忽略 Beta 限制，以 Momentum_ATR 與 VTF_Score 作為核心多空判斷依據

**財報極端風控（所有策略）**：禁止選擇 `Earnings_Days_Left <= 3` 的任何個股，防止系統涉入財報賭博風險（filter.py 財報防禦牆為第一道攔截，此為 AI 側雙重保護）。

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
    candidates: list[dict],       # scorer 輸出，含 total_score、sector
    price_data: dict[str, pd.DataFrame],
    info_data: dict[str, dict],
    top_n: int = 5,
    market_context: dict | None = None,
    earnings_data: dict | None = None,  # {sym: date_str | None}，供 Earnings_Days_Left 計算
) -> list[dict]:
    """回傳 AI 精選結果，BEAR_DISTRIBUTION 時回傳 []。"""

def compute_indicators(
    sym: str,
    df: pd.DataFrame,
    r_spy_global: pd.Series | None,
    earnings_days_left: int | None = 99,
) -> dict:
    """
    計算單支股票指標供 Markdown 表格使用。

    r_spy_global: SPY 全量日報酬率序列，由呼叫端在外層預計算一次後傳入，
                  不在函式內重複計算；None 時 beta_60d 回傳 None。

    earnings_days_left: 由外層查財報快取後計算好的日曆天數（架構解耦）。
                        None = 數據斷裂（表格填 N/A，AI 排除）；
                        99   = 安全無近期財報。

    回傳 dict 含：
      price, ema5/10/20/50, rsi
      momentum_atr  (float | None)   ATR 標準化 20 日動能
      vtf_score     (float | None)   量能推進因子，下限 -5.0、上限不設
      beta_60d      (float | None)   60 日 Beta；共同交易日不足 30 時 None
      earnings_days_left (int | None)
      change_1d_pct, change_5d_pct
      （以及供 _strategy_tag 使用的 stoch_k、rsi_5d_ago、dist_from_20d_high_pct 等）
    """

def _diversify_candidates(
    candidates: list[dict],
    regime: str = "",
    max_per_sector: int = 8,
) -> list[dict]:
    """
    對候選池做每產業上限截斷，防止同一板塊霸榜使 AI 無法輸出分散選股。
    PANIC_REVERSAL 環境下，得分 < 40 的強制放行反轉股不受產業上限限制。
    """

def _build_prompt(...) -> str:
    """組裝 XML Prompt。"""

def _generate_candidates_markdown_table(
    candidates: list[dict],
    price_data: dict[str, pd.DataFrame],
    info_data: dict[str, dict],
    earnings_data: dict | None = None,
    current_date: date | None = None,
) -> str:
    """生成 15 欄 Markdown 表格字串。浮點數一律 .round(2)；None → 'N/A'（earnings_days_left None → 'N/A'，99 → '99'）。"""
```

### price_data 傳入前置條件

- `price_data` 必須包含 `'SPY'` 完整 90 日 DataFrame（由 `pipeline.py` Step 5.5 `fetch_market_context` 負責寫入）
- `current_date`（財報天數計算基準日）由外層取 `price_data['SPY'].index[-1].date()`，不使用 `date.today()`（時區冪等，GitHub Actions UTC 與本機 UTC+8 結果一致）
- `r_spy_global`（SPY 報酬率序列）在候選股循環外計算一次：`spy_df['Close'].pct_change().dropna()`

### Beta_60D 計算步驟（必須依序）

1. `r_stock = df['Close'].pct_change().dropna()`
2. `r_s_aligned, r_spy_aligned = r_stock.align(r_spy_global, join='inner')`（先完整對齊）
3. `clean = pd.concat([r_s_aligned, r_spy_aligned], axis=1).dropna()`（聯集清洗 NaN，確保同長度）
4. `clean_60 = clean.tail(60)`（對齊後才截斷，確保最近 60 個共同交易日）
5. `if len(clean_60) >= 30` → `cov = np.cov(clean_60.iloc[:, 0], clean_60.iloc[:, 1])`
6. `if cov[1, 1] != 0` → `beta_60d = round(cov[0, 1] / cov[1, 1], 2)`

### VTF_Score 計算步驟

```
avg_vol = df['Volume'].tail(30).mean()
vol_ratio = df['Volume'].iloc[-1] / avg_vol  if avg_vol > 0  else 1.0  # 零除安全降級
vtf_score = round(max(-5.0, vol_ratio * (2 * k_pos - 1)), 2)           # 下限保護，上限不設
```

## Design Decisions

### DD-1: XML 三區塊結構

- **選擇**：`<Market_Regime>` / `<Candidate_Pool>` / `<Output_Constraint>` 分離
- **原因**：讓 AI 先理解市場環境，再看候選股，最後看約束條件，符合人類分析推理的自然順序。XML 標籤讓 AI 能精確定位每個區塊的語意，減少混淆。
- **捨棄**：純文字描述（AI 難以區分哪段是背景、哪段是數據、哪段是指令）

### DD-2: Price_5D_Pct 保留；Price_20D_Pct 以 Momentum_ATR 取代

- **選擇**：保留 `Price_5D_Pct`（5 日短線爆發力）；以 `Momentum_ATR` 取代 `Price_20D_Pct`（20 日中線趨勢）
- **原因**：`Price_20D_Pct` 的跨行業偏見問題——高 Beta 科技股絕對漲跌幅天生高於低 Beta 消費/公用事業股，AI 若直接比較會系統性偏愛科技股。`Momentum_ATR`（20 日價格位移 ÷ 14 日 ATR）以個股自身波動為基準做標準化，不同行業的動能可公平比較。5 日爆發力仍由 `Price_5D_Pct` 提供，維持短期動能視角。
- **捨棄**：只有 `Price_20D_Pct`（無法判斷近期啟動動能，且有高 Beta 偏見）

### DD-3: Strategy_Tag 為參考，非強制

- **選擇**：`Strategy_Tag` 欄標記「系統預判策略（僅供參考）」
- **原因**：L2 系統預判可能不準，AI 有更多脈絡可做更好的判斷。強制 AI 只選系統標記的策略會降低 AI 的推理空間。
- **捨棄**：只傳送符合主推策略的候選股（過濾過嚴，AI 無法發現例外）

### DD-9: L2-L3 特徵矩陣標準化與對齊

- **選擇**：以 `Momentum_ATR`（ATR 標準化動能）、`VTF_Score`（量能推進因子）、`Beta_60D`（60 日市場相關性）、`Earnings_Days_Left`（財報剩餘天數）取代 `Price_20D_Pct`、`Vol_Ratio`，並新增兩個風險維度
- **原因**：
  - `Momentum_ATR`：跨行業 Beta 差異問題 → ATR 標準化後可公平比較
  - `VTF_Score`：`Vol_Ratio` 無法辨別帶量推進與帶量出貨 → 乘以 K_pos 因子後，上影線爆量出貨呈負值、下影線放量承接呈正值；上限不設以保留機構掃貨的信號強度
  - `Beta_60D`：提供 AI 評估板塊輪動風險；窗口設為 60 日（符合 90 日快取限制）；采先對齊後截斷策略防止停牌造成矩陣錯位
  - `Earnings_Days_Left`：AI 側雙重財報防禦（filter.py 為第一道）；基準日錨定 SPY 最後交易日確保時區冪等；99 與 N/A 語意嚴格區分防止數據斷裂繞過風控
- **捨棄**：`Price_20D_Pct`（高 Beta 偏見）；`Vol_Ratio`（不含 K 線位置）；`Beta_120D`（超出 90 日快取窗口）；VTF 雙側裁切 `clip(-3, +3)`（壓制機構掃貨信號）；`date.today()`（時區依賴，非冪等）；Earnings 缺值一律填 99（掩蓋數據斷裂，風控漏洞）

### DD-10: RS_vs_Sector 欄位

- **選擇**：在 Markdown 候選池表格中新增 `RS_vs_Sector` 欄，插入於 `Momentum_ATR` 後
  - 計算：`個股 5 日報酬率 − SECTOR_ETF_MAP 對應 ETF 5 日報酬率`
  - 板塊 ETF 來自 market.py 的 `SECTOR_ETF_MAP`；板塊未知或 ETF 缺資料 → fallback SPY
  - ETF 數據不足 5 日 → 填 `N/A`（不影響 AI 排除，僅缺少相對強度資訊）
  - 格式：`.round(1)%`（保留一位小數）
  - AI 指引：`RS_vs_Sector > +2% = 板塊領頭羊，優先加分；< -2% = 板塊落後者，需額外確認`
- **原因**：AI 已有 VTF_Score（量能方向）和 Momentum_ATR（個股絕對動能），但缺少「相對板塊」的橫向比較維度。加入 RS_vs_Sector 後，AI 可以直接分辨「整個板塊都在漲但個股特別強」vs「個股只是被板塊帶動」，選股質量顯著提升。
- **數據依賴**：需要 Batch 0 已將板塊 ETF 納入 `price_data`（pipeline Step 2）；`SECTOR_ETF_MAP` 已從 market.py import
- **捨棄**：以個股對 SPY 計算 RS（忽略板塊輪動，無法辨識板塊領頭羊）；不加此欄（AI 缺少相對強度視角，板塊集中時難以分辨）

### DD-11: _diversify_candidates() 產業上限保護

- **選擇**：在候選池送給 AI 前，以每產業最多 `MAX_SECTOR_CANDIDATES=8` 支截斷
  - 按 L2 分數降序掃描，優先保留高分股，達到上限後跳過同產業後續個股
  - **PANIC_REVERSAL 強制放行例外**：`total_score < 40`（L2 典型強制放行分段）的個股不計入產業計數，直接加入（這些反轉股需要 AI 評估，不應被產業配額卡住）
- **原因**：L2 評分無跨產業限制，若某板塊當日強勢，可能 20-30 支候選都來自同一產業。AI 在 40 支候選全是科技股時，很難輸出分散的選股（即使有意選防禦股，候選池裡根本沒有）。每產業 8 支上限確保 AI 有足夠多元候選可選。
- **限制**：若候選池總數 < 24（3 個產業 × 8），此函式無任何效果，正常通過
- **捨棄**：每產業 5 支（過緊，少數超強板塊的機會被壓縮）；不做截斷（同一板塊霸榜，AI 輸出集中度高）

## Acceptance Criteria

- [ ] Prompt 中 `<Market_Regime>` 區塊含有 `5日` 與 `20日` 漲跌的產業 ETF 資訊
- [ ] Markdown 表格 header 含 `VTF_Score`、`Momentum_ATR`、`Beta_60D`、`Earnings_Days_Left`（原 `Vol_Ratio`、`Price_20D_Pct` 已移除）
- [ ] BEAR_DISTRIBUTION Regime → `rank_candidates()` 回傳 `[]`，不發出 API 請求
- [ ] AI 輸出包含 `buy_zone`（格式 `$X~$Y`）、`stop_loss`、`hold_period`
- [ ] `compute_indicators()` 回傳 dict 含 `momentum_atr`、`vtf_score`、`beta_60d`、`earnings_days_left` keys
- [ ] `avg_vol_30d = 0` → `vtf_score` 不崩潰（vol_ratio 降級為 1.0）
- [ ] 停牌個股與 SPY 共同交易日不足 30 → `beta_60d = None`，表格顯示 `N/A`，AI 不排除
- [ ] 數據斷裂無財報歷史 → `earnings_days_left = None`，表格 `N/A`，AI 排除
- [ ] 無近期財報但查到遠期日期 → `earnings_days_left = 99`，表格顯示 `99`，AI 不排除
- [ ] 爆量突破（VTF_raw = 10.0）→ 表格顯示 `10.00`（未被雙側裁切壓制）
- [ ] 極端出貨（VTF_raw = -8.0）→ 表格顯示 `-5.00`（下限保護）
- [ ] 浮點數欄位（Momentum_ATR、VTF_Score、Beta_60D）均保留 2 位小數
