# Ranker — L3 AI 精選規格

## Purpose

將 L2 候選股透過結構化 XML Prompt 送給 DeepSeek AI，依當日 Regime 主推策略選出最多 3 支（DD-20），附帶具體的買入區間、目標價、止損、持有週期與策略理由。

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

### Markdown 候選池表格欄位（30 欄）

| 欄位 | 說明 |
|------|------|
| Ticker | 股票代號 |
| Close_Price | 最新收盤價 |
| Sector | 產業（縮寫後） |
| L2_Score | L2 總分 |
| Strategy_Tag | 系統預判策略（僅供參考） |
| MA_Trend | BULL_1/BULL_2/MIXED/BEAR |
| EMA5 | 5 日指數移動均線價位（美元）；DD-12 |
| EMA10 | 10 日指數移動均線價位（美元）；動能策略淺回檔帶的下緣底線（DD-19）；已回檔加分情境的區間上緣（DD-12） |
| EMA20 | 20 日指數移動均線價位（美元）；動能策略已回檔加分情境的區間下緣（DD-12）；該情境的止損基準 |
| Vol_vs_5DAvg | 當日成交量 ÷ 5 日均量；`.round(2)`；<0.7 代表回檔量縮確認；DD-12 |
| High_20D | 20 日最高價（美元）；突破策略的壓力位/回測支撐基準；DD-13 |
| Vol_vs_20DAvg | 當日成交量 ÷ 20 日均量；`.round(2)`；>=1.5 代表攻擊量確認突破，<1.2 假突破機率高；DD-13 |
| RSI | RSI 數值 |
| MACD_Hist | POS_INC/POS_DEC/NEG_INC/NEG_DEC |
| VTF_Score | 量能推進因子：`max(-5.0, Vol_Ratio × (2×K_pos − 1))`，下限 -5.0、上限不設；`.round(2)`；分母為零時安全降級 `vol_ratio = 1.0`；正值=帶量推進（> 5.0 為史詩級機構建倉），負值=高檔派發；缺值填 `N/A` |
| Price_5D_Pct | 5 日漲跌幅（短線爆發力） |
| Momentum_ATR | ATR 標準化動能：20 日價格位移 ÷ 14 日 ATR；`.round(2)`；缺值填 `N/A` |
| ATR14 | 14 日平均真實波幅（美元，Wilder 平滑）；動能策略買入區間深度與止損距離的波動基準；缺值填 `N/A`（此時 `Momentum_ATR` 必同為 `N/A`，既有排除規則已涵蓋，不需獨立 N/A 規則）；DD-19 |
| EMA50 | 50 日均線價位（美元）；反轉策略的支撐區判斷基準；DD-13 |
| Low_20D | 20 日最低價（美元）；反轉策略的左側關鍵支撐與止損基準；DD-13 |
| Stoch_K | 隨機指標 KD 的 K 值；`.round(1)`；<25 代表超賣區；DD-13 |
| RSI_5D_Ago | 5 日前的 RSI 數值；`.round(1)`；RSI > RSI_5D_Ago 代表底背離訊號；DD-13 |
| RS_vs_Sector | 個股 5 日報酬率 − 板塊 ETF 5 日報酬率（百分比）；`.round(1)`；板塊 ETF 來自 `SECTOR_ETF_MAP`，缺資料 fallback SPY；ETF 數據不足 5 日填 `N/A` |
| 52W_High_Dist | 距 52 週高點百分比 |
| Beta_60D | 個股 60 日 Beta vs SPY；完整序列 inner join + NaN 清洗後取末 60 日計算；`.round(2)`；**缺值填 `N/A`，不觸發 AI 排除** |
| Earnings_Days_Left | 距下次財報日曆天數（基準日 = SPY 最後交易日）；安全無近期財報填 `99`；數據斷裂（完全查不到財報歷史）填 `N/A` |
| Fwd_PE | 預估本益比（`forwardPE`，缺值 fallback `trailingPE`）；`.round(1)`；缺值填 `N/A`，**不觸發 AI 排除**；DD-14 |
| Profit_Margin | 淨利率（`profitMargins`）；`.round(1)%`；缺值填 `N/A`，**不觸發 AI 排除**；DD-14 |
| Rev_Growth_YoY | 營收年增率（`revenueGrowth`）；`.round(1)%`；缺值填 `N/A`，**不觸發 AI 排除**；DD-14 |
| Short_Float_Pct | 空頭持股佔流通股比例（`shortPercentOfFloat`）；`.round(1)%`；缺值填 `N/A`，**不觸發 AI 排除**，僅作軋空風險旗標；DD-17 |

### 策略指引（System Prompt 約束）

**動能策略（Momentum）**：優先挑選 `Momentum_ATR >= 2.0` 且 `VTF_Score > 1.0` 的標的，跨行業公平挑選，不以絕對漲幅百分比作為依據。買入區間以個股自身 `ATR14` 為深度基準三段式判斷（DD-19，取代 DD-12 的 EMA 帶預設）：標準進場（預設）設在 `Close_Price − 1×ATR14 ～ Close_Price − 0.25×ATR14` 淺回檔帶（下緣不低於 `EMA10`）；已回檔加分情境——股價已自然回落至 `EMA20~EMA10` 且 `Vol_vs_5DAvg < 0.7`（量縮）→ 直接用該區間，信心分數可上調；過熱例外——`RSI > 78` 或 `VTF_Score < 0` → 大幅降低信心分數，不宜進場。止損：買入區間下緣 − 1×ATR14（加分情境可改用 `EMA20` 下方 2%，兩者取較高者，不得寬於進場價 −10%）。

**突破策略（Breakout）**：強烈關注 `VTF_Score >= 1.5` 且 `Close_Price` 距 `High_20D` 在 -2%~+2% 內的標的。買入區間依 `High_20D`/`Vol_vs_20DAvg` 四段式判斷（DD-13）：優先選回測確認（曾站上 `High_20D` 後回落至 `High_20D~High_20D×1.02` 企穩）；次選標準突破緩衝（`High_20D` 之上 +0.5%~+1.5%）；距 `High_20D` 超過 +3% 視為追高；`Vol_vs_20DAvg >= 1.5` 才視為攻擊量確認。`VTF_Score < 0` 一律視為假突破派發陷阱，禁止入選。

**反轉策略（Oversold Reversal）**：優先挑選 `Momentum_ATR <= -2.0`（代表個股跌幅跨越 2 個標準真實波幅，極端超賣）且 `VTF_Score` 由負轉正或向 0 軸收斂（拋壓衰竭或低檔主力承接信號）的標的。底背離確認依 `Stoch_K`/`RSI_5D_Ago` 判斷（DD-13）：`Stoch_K < 25` 且 `RSI > RSI_5D_Ago`。買入區間優先設在 `EMA50` 附近（±3%），且 `Close_Price` 須明顯高於 `Low_20D`（代表右側反彈已確立）；止損設在 `Low_20D` 下方，不得設在 `EMA50` 之上。

**VTF_Score 解讀**：VTF_Score 無上限，數值越大代表機構推進力越強；`> 5.0` 應視為史詩級建倉訊號，必須認真對待。

**PANIC_REVERSAL 低分豁免**：PANIC_REVERSAL 環境下，帶有 REVERSAL 標籤的個股 L2_Score 偏低是**正常且符合預期**的（暴跌期均線、MACD、RSI 各項指標本就偏低）。AI 必須忽略低分偏見，專注審查 Momentum_ATR 是否呈現拋壓衰竭（從深度負值向 0 軸收斂）。

**N/A 差異化處理規則**：
- `Earnings_Days_Left = N/A` → **直接排除**（數據斷裂，財報時間未知，黑天鵝風險無法評估）
- `Momentum_ATR = N/A` 或 `VTF_Score = N/A` → **直接排除**（技術數據不足）
- `Beta_60D = N/A` → **不排除**，忽略 Beta 限制，以 Momentum_ATR 與 VTF_Score 作為核心多空判斷依據
- `Fwd_PE`/`Profit_Margin`/`Rev_Growth_YoY` 任一為 `N/A` → **不排除**，僅代表該基本面維度無法評估，改倚重其餘維度判斷（DD-14）
- `Short_Float_Pct = N/A` → **不排除**，僅代表放空數據缺失，不影響其餘判斷（DD-17）

**基本面取捨規則（DD-14）**：技術面強度相近的候選股之間，優先選擇 `Profit_Margin` 為正、`Rev_Growth_YoY`
為正、`Fwd_PE` 相對同批候選股不過度偏貴的個股；若技術面強但基本面明顯空心（虧損、營收衰退、估值過高），
不直接排除，但應降低 `confidence` 分數並在 `risk` 中具體說明基本面疑慮。

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
    top_n: int = 3,
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
      momentum_atr   (float | None)  ATR 標準化 20 日動能
      vtf_score      (float | None)  量能推進因子，下限 -5.0、上限不設
      vol_vs_5d_avg  (float)         當日量 ÷ 5日均量（動能策略回檔量縮確認，DD-12）；分母為零時降級為 1.0
      vol_vs_20d_avg (float)         當日量 ÷ 20日均量（突破策略攻擊量確認，DD-13）；分母為零時降級為 1.0
      high_20d       (float)         20 日最高價（突破策略壓力位/回測支撐基準，DD-13）；已曝露於候選池表格
      low_20d        (float)         20 日最低價（反轉策略左側支撐與止損基準，DD-13）；已曝露於候選池表格
      stoch_k        (float)         隨機指標 K 值（反轉策略超賣確認，DD-13）；已曝露於候選池表格
      rsi_5d_ago     (float | None)  5 日前 RSI（反轉策略底背離確認，DD-13）；已曝露於候選池表格
      beta_60d       (float | None)  60 日 Beta；共同交易日不足 30 時 None
      earnings_days_left (int | None)
      change_1d_pct, change_5d_pct
      （以及供 _strategy_tag 使用的 dist_from_20d_high_pct、dist_from_ema50_pct 等）
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
    """生成 28 欄 Markdown 表格字串。浮點數一律 .round(2)；None → 'N/A'（earnings_days_left None → 'N/A'，99 → '99'）。"""
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

### DD-12: 動能策略買進區間結構化（EMA10~EMA20 回檔帶）

> **註**：本 DD 的三段式規則已被 DD-19 部分取代——EMA20~EMA10 回檔帶從「預設進場」降級為「已回檔加分情境」，預設改為 ATR 錨定淺回檔帶。表格四欄（EMA5/EMA10/EMA20/Vol_vs_5DAvg）與「buy_zone 由 AI 輸出」的架構取捨仍然有效。

- **選擇**：候選池表格新增 `EMA5`、`EMA10`、`EMA20`（美元原始價位）與 `Vol_vs_5DAvg`（當日量 ÷ 5日均量）四欄；System Prompt 動能策略段落改為三段式規則：
  1. 標準回檔進場：股價已回落至 `EMA20~EMA10` 之間，且 `Vol_vs_5DAvg < 0.7`（量縮無賣壓）→ `buy_zone` 設在該區間
  2. 極端強勢例外：股價緊貼 `EMA5`（尚未明顯回檔）但 `VTF_Score` 仍強 → `buy_zone` 可設在 `EMA5` 附近（5MA 探針帶）
  3. 股價距 `EMA5` 已超過 +5%（過度延伸、未回檔）→ 大幅降低信心分數，禁止以收盤價設為買入區間上限
- **原因**：實測觀察到動能策略 `buy_zone` 上限幾乎恆等於 `Close_Price`（例：KLAC 收盤 $301.71，`buy_zone` `$295~$302`；AMAT 收盤 $723.00，`buy_zone` `$710~$723`），與 Prompt 文字宣稱的「小幅回調至 EMA10」不符。根因是候選池表格只提供 `MA_Trend` 文字標籤（如 `BULL_1`），沒有任何 EMA 的實際數值，AI 無數字依據可用，只能以收盤價為錨猜測。`ema5/ema10/ema20` 其實已在 `compute_indicators()` 算出，只是未曝露於表格；本次僅需新增曝露與一個新指標 `vol_vs_5d_avg`。規則來源：使用者提供的《股票操作高勝率買進區間策略指南》動能策略章節（10EMA~20EMA 回檔帶、量縮確認 <0.7 倍 5 日均量、5MA 探針帶、遠離 5MA 超過 5% 拒絕追價）。
- **範圍**：本次僅處理動能策略；突破策略、反轉策略的買進區間同樣缺乏數字依據（如反轉策略引用 EMA50 附近支撐區，但表格未提供 EMA50 數值），列為後續獨立任務，不在本次變更範圍
- **不變**：`buy_zone` 仍由 AI 自行輸出最終字串（格式 `$X~$Y`），不改為 Python 端確定性計算；`tracker.py` 的 `_parse_buy_zone()` 解析邏輯與格式需求不變
- **捨棄**：Python 端直接算出確定性 buy_zone（偏離現有「AI 給出完整交易計畫」的架構精神，且需大改 tracker.py 假設）；只給 `Dist_EMA5_Pct` 衍生百分比而不給原始 EMA 價位（AI 無法據此寫出具體美元買進區間）
- → 詳見 `plans/2026-07-01-momentum-buy-zone-ema-anchor.md`

### DD-13: 突破/反轉策略買進區間結構化（High_20D 回測帶、EMA50/Low_20D 右側支撐）

- **選擇**：候選池表格新增 `High_20D`、`Vol_vs_20DAvg`（突破策略）與 `EMA50`、`Low_20D`、`Stoch_K`、`RSI_5D_Ago`（反轉策略）共六欄；System Prompt 對應段落改寫：
  - 突破策略四段式規則：優先回測確認（曾站上 `High_20D` 後回落至 `High_20D~High_20D×1.02` 企穩）；次選標準突破緩衝（`High_20D` 之上 +0.5%~+1.5%）；距 `High_20D` 超過 +3% 視為追高降信心；`Vol_vs_20DAvg >= 1.5` 才視為攻擊量確認，`< 1.2` 假突破機率高
  - 反轉策略三段式規則：`Close_Price` 落在 `EMA50` 附近（±3%）且 `Stoch_K < 25` 且 `RSI > RSI_5D_Ago`（底背離）才進場；`Close_Price` 須明顯高於 `Low_20D`（右側反彈已確立）；仍貼近或跌破 `Low_20D` 則維持觀望；止損設在 `Low_20D` 下方，不得設在 `EMA50` 之上
- **原因**：程式碼稽核發現這兩個策略比動能策略（DD-12）問題更嚴重——`stoch_k`、`rsi_5d_ago`、`ema50`、`high_20d`、`low_20d` 全部只用於 `_strategy_tag()` 內部判斷，Prompt 卻直接引用這些指標名稱（如「`stoch_k < 25`」「買入：EMA50 附近支撐區」）要求 AI 判斷，但 AI 在候選池表格裡完全看不到這些數字，等於是在憑空回答 Prompt 裡點名的條件。規則來源：使用者提供的《股票操作高勝率買進區間策略指南》突破策略章節（回測確認區優於當日追單、20 日均量 1.5~2 倍攻擊量確認）與反轉策略章節（右側結構確認，本次僅取現有指標可表達的「EMA50 支撐 + 底背離 + Low_20D 止損」子集，完整的 W 底/BOS/斐波那契回撤形態辨識超出範圍，見下方捨棄）
- **不變**：`buy_zone` 仍由 AI 自行輸出最終字串（格式 `$X~$Y`），`tracker.py` 的 `_parse_buy_zone()` 解析邏輯與格式需求不變
- **捨棄**：套牢量檢查（需額外歷史成交量峰值追蹤邏輯，改動範圍超出「曝露既有指標」的最小化原則）；反轉策略完整形態辨識（W 底第二腳、BOS、50%~61.8% 斐波那契回撤——需偵測「第一波反彈」的高低點，現有資料模型是單筆技術指標快照，無法表達多筆歷史事件的形態序列）；Python 端直接算出確定性 buy_zone（偏離現有「AI 給出完整交易計畫」的架構精神）
- → 詳見 `plans/2026-07-01-breakout-reversal-buy-zone-anchor.md`

### DD-14: L3 候選池加入基本面維度（估值/獲利品質/成長性）

- **選擇**：候選池表格新增 `Fwd_PE`（估值）、`Profit_Margin`（獲利品質）、`Rev_Growth_YoY`（成長性）三欄，
  取自 `fetcher.fetch_info()` 已下載但未使用的 yfinance `info` 字典欄位（不需額外 API 呼叫）；System Prompt
  新增選股原則第 7 條，將基本面定位為「技術面強度相近時的取捨依據與風險旗標」；三欄缺值比照 `Beta_60D`
  先例（不排除，改用其餘維度判斷），**不**比照 `Earnings_Days_Left`（直接排除）
- **原因**：L1 已做流動性/市值硬篩、L2 是 100 分制純技術評分，L3 拿到的候選池技術面同質性已經很高，若 AI
  的判斷依據仍然 100% 是技術指標，等於在同一批「技術達標」的股票裡比誰的指標數字更漂亮，缺乏「公司體質
  好不好」的另一個維度。三個欄位覆蓋估值、獲利品質、成長性三個互補角度，且都能從既有 `fetch_info()` 呼叫
  免費取得，不增加下載成本
- **不變**：JSON 輸出 schema（`{"selections": [...]}`）不變，不新增欄位，基本面判斷折疊進既有的
  `reason`/`risk`/`confidence` 三欄；DD-12/DD-13 的策略型買進區間算法完全不受影響，基本面不推翻技術面
  結構確認邏輯
- **捨棄**：在 L2 加基本面分數維度（會混淆 L2「純技術評分」的既有定位，且需要重新校準 100 分權重）；
  新增獨立 JSON 欄位如 `fundamental_reason`（增加 `tracker.py`/`publisher.py` 需要適配的欄位，且與現有
  `reason`/`risk` 語意重疊）；納入分析師共識維度（目標價隱含漲幅、評等）——與使用者討論後排除，不在本次
  範圍內
- → 詳見 `plans/2026-07-02-fundamental-dimension-l3-prompt.md`

### DD-15: 動能/反轉策略止損改為明確百分比緩衝，避免等於買入區間下緣

> **註**：動能策略部分已被 DD-19 部分取代——預設路徑（ATR 淺回檔帶進場）的止損改為「買入區間下緣 − 1×ATR14」；「EMA20 下方 2%」僅保留於已回檔加分情境。「止損不得等於買入區間下緣」的核心原則不變。反轉策略部分完全不受影響。

- **選擇**：動能策略止損由「跌破 EMA20」改為「EMA20 下方 2%（不得設為等於買入區間下緣，須低於 EMA20）」；
  反轉策略止損由「Low_20D 下方」改為「Low_20D 下方 2%（不得設在 EMA50 之上，避免止損過寬；不得等於買入
  區間下緣）」。兩處都比照突破策略既有的「跌回 High_20D 下方 2%」寫法（明確百分比 + 顯式提醒不得等於
  買入區間下緣）
- **原因**：用 `data/watchlist.json` 實測數據驗證，動能策略的買入區間下緣本身就是 EMA20，止損規則只給
  「跌破」這個方向詞卻沒有明確百分比，AI 有時會照字面把止損直接設成等於 EMA20（即等於買入區間下緣），
  造成一買進去就已經觸發止損、沒有容錯空間的不合理風控（實測案例：CB/KHC/V/AJG/LIN 五支候選股止損價與
  買入區間下緣完全相等；對照較早的 KLAC/AMAT 有自然緩衝，證明這不是必然發生、而是 Prompt 措辭模糊導致
  AI 解讀不一致）。反轉策略雖然買入區間錨點（EMA50）與止損錨點（Low_20D）不同、正常情況天生有緩衝，但
  「buy_zone 下緣不得低於 Low_20D」這個地板條件被觸發時（EMA50 已貼近或低於 Low_20D 的長期陰跌情境）
  會重演同一種措辭模糊問題，故一併修正，堵住整類「方向詞止損無緩衝」的設計缺陷
- **不變**：純 Prompt 文字修正，JSON 輸出 schema 不變，`tracker.py` 的 `_parse_stop_loss()`／
  `_parse_buy_zone()` 不需要任何改動（兩者只解析 `"$X"`／`"$X~$Y"` 字串格式，不假設兩者的數值關係）
- **捨棄**：維持方向詞不加緩衝（已證實會被 AI 照字面解讀出問題）；在 Python 端強制修正 AI 回傳的
  `stop_loss`（例如自動下修 2%）——偏離「AI 給出完整交易計畫」的既有架構精神（DD-12 已在此點做過取捨），
  且會讓 AI 給出的止損理由與實際存入的止損價不一致，治標不治本
- → 詳見 `plans/2026-07-02-stop-loss-buffer-fix.md`

### DD-16: Historical_Performance_Review 第四區塊——注入本地績效回饋

- **選擇**：`rank_candidates()` 在 `_build_prompt()` 前呼叫新函式 `_load_ai_hints()`（讀 `data/ai_hints.json` 的 `prompt_lines`，檔案不存在、損壞或欄位缺失時靜默回傳 `[]`），將行清單以新參數 `ai_hints: list[str] | None` 傳入 `_build_prompt()`。非空時在 Prompt 末尾（`</Output_Constraint>` 之後）附加第四區塊 `<Historical_Performance_Review>`：首行說明「以下為本系統過往已結算訊號的實戰統計回饋」，尾行固定加「樣本數有限，僅供權衡參考，不得覆蓋 Market_Regime 的策略方向」警語（analyzer DD-3）。空清單時 Prompt 與現狀逐字元相同（零回歸）。
- **原因**：L3 過去沒有從歷史績效學習的回饋迴路；hints 由 `analyzer.py` 統計並渲染（analyzer DD-4），ranker 只盲讀附加，統計邏輯不外溢。放在 Prompt 末尾而非 `<Output_Constraint>` 之前，維持既有三區塊順序完全不動、輸出約束的解析位置不變。
- **不變**：`SYSTEM_PROMPT` 不改動；JSON 輸出 schema 不變；DD-12/DD-13/DD-15 的策略買進區間與止損規則完全不受影響。
- **捨棄**：ranker 端讀 `dimensions` 自行渲染（統計與呈現分散兩模組）；改寫 `SYSTEM_PROMPT`（靜態字串放動態統計，語意錯位）；hints 放進 `<Market_Regime>`（該區塊語意是「當日大盤環境」，混入歷史統計會稀釋 Regime 服從性指令的權威）。→ 詳見 `plans/2026-07-03-analyzer-ai-hints.md`

### DD-17: 候選池新增 Short_Float_Pct 空頭比例標記（不排除）

- **選擇**：候選池表格新增 `Short_Float_Pct` 欄，插入於 `Rev_Growth_YoY` 之後；取自 `fetcher.fetch_info()` 新增的
  `short_percent_float`（yfinance `shortPercentOfFloat`，不需額外 API 呼叫）；比照 `Beta_60D`/DD-14 三欄先例，
  缺值填 `N/A` 且**不觸發 AI 排除**；System Prompt 的 N/A 差異化處理規則同步新增一條
- **原因**：高空頭比例（實務上 >15% 視為高軋空風險）個股的價格行為容易被空頭回補（short squeeze）或持續施壓
  兩種極端情境主導，波動特性與一般個股不同，AI 目前完全看不到這個維度。比照 DD-14 的基本面欄位定位——不是
  用來排除股票，而是讓 AI 在給 `confidence`/`risk` 時能額外納入這個風險旗標
- **不變**：JSON 輸出 schema 不變，`buy_zone`/`stop_loss` 相關的 DD-12/13/15 策略算法完全不受影響；`tracker.py`
  零改動
- **捨棄**：以此欄位做 L1 硬排除（高空頭比例不代表個股體質差，貿然排除會誤殺被錯殺的優質股）；新增
  `sharesShort`/`shortRatio`（Days-to-Cover）等衍生欄位——`shortPercentOfFloat` 單一欄位已足夠表達風險方向，
  避免表格欄位過度膨脹

### DD-18: `_enrich_fallback()` 結果標記 `is_fallback: True`，與真實 AI 判斷明確區分

- **選擇**：`_enrich_fallback()`（`DEEPSEEK_API_KEY` 未設定，或 `_call_deepseek()` 回傳空清單時觸發的 L2 分數退化輸出）產生的每個結果新增 `"is_fallback": True` 欄位；真實 AI 排序結果不含此欄位（`stock.get("is_fallback")` 自然為 falsy）。`tracker.py` 的 B/C 步驟改為優先檢查 `is_fallback`，獨立於既有的 `confidence < MIN_AI_CONFIDENCE`（DD-14）門檻之外直接跳過，並印出明確區分的 log（「為 L2 分數 fallback 結果，非信心不足」），不再與真實 AI 給出的低信心分數混用同一句「AI 信心分數 X < 6，跳過」。`main.py` 計算 `stats["ai_count"]` 時排除 `is_fallback` 條目。
- **原因**：2026-07-06 report 觸發的真實案例——`market.py` DD-7 的大盤背景抓取失敗導致 `market_context={}`，Step 6 送給 DeepSeek 的 Prompt 因而缺少大盤環境背景，AI 當天回傳空結果（`API 回傳 22 字元...取得 0 筆結果`），`rank_candidates()` 依既有邏輯降級為 `_enrich_fallback()`，把 L2 分數前 5 名包成「AI 精選」格式，`confidence` 寫死為 5。DD-14 原本就設計 `MIN_AI_CONFIDENCE=6` 讓這些 fallback 個股（`buy_zone="-"` 等佔位字串，本來就無法解析出真實交易參數）不進入 watchlist——這部分行為正確，不需改變。但 `main.py` 的 `stats["ai_count"] = len(ranked)` 沒有區分 fallback 與真實 AI 結果，把這 5 支 fallback 佔位股當成「5 支 AI 精選」寫入 `last_run.json`；報告卻正確顯示「0 支新增」（fallback 個股全被 DD-14 濾除）。兩者不一致造成使用者誤以為選股流程漏掉了本該出現的個股，實際上當天 AI 完全沒有產出任何真實判斷。
- **不變**：DD-14 的排除結果不變（fallback 個股本來就不該、也仍然不會進入 watchlist）；JSON 輸出 schema 只新增一個布林欄位，其餘欄位不動。
- **捨棄**：讓 `stats["ai_count"]` 保持 `len(ranked)` 不變、只修 `market.py` 的資料來源問題（治標不治本——DeepSeek API 本身偶發回傳空清單、或未設定 API Key 時走 `_enrich_fallback()` 的既有分支，與這次 `market_context={}` 是兩條獨立成因，日後任何一條路徑觸發 fallback 都會重現同一種誤導性統計）；把 fallback 判斷邏輯下放到 `tracker.py` 用 `confidence == 5` 猜測（脆弱，未來若 `MIN_AI_CONFIDENCE` 或 fallback 預設分數任一方調整就會誤判，且無法與 AI 剛好給 5 分的真實判斷區分）。
- → 詳見 [plans/2026-07-07-market-context-single-etf-resilience.md](../plans/2026-07-07-market-context-single-etf-resilience.md)

### DD-19: 動能策略買進區間改為 ATR 錨定淺回檔帶（部分取代 DD-12/DD-15 的動能段落）

- **選擇**：候選池表格新增 `ATR14` 欄（14 日平均真實波幅美元值，Wilder 平滑；`compute_indicators()` 原本已計算此值供 `Momentum_ATR` 使用，只是計算後即丟棄，本次僅需曝露）；System Prompt 動能策略買進區間改為三段式新規則：
  1. 標準進場（預設）：`buy_zone` 設在 `Close_Price − 1×ATR14 ～ Close_Price − 0.25×ATR14` 的淺回檔帶；若區間下緣低於 `EMA10`，改以 `EMA10` 為下緣（不追求超過趨勢結構的深度回檔）
  2. 已回檔加分情境：股價已自然回落至 `EMA20~EMA10` 之間且 `Vol_vs_5DAvg < 0.7`（量縮無賣壓）→ `buy_zone` 直接設在該區間，信心分數可上調（原 DD-12 的「標準回檔進場」降級至此）
  3. 過熱例外：`RSI > 78` 或 `VTF_Score < 0`（高檔出貨跡象）→ 大幅降低信心分數，不宜進場（取代原 DD-12 的「距 EMA5 超過 +5% 一律禁止」——距離均線遠近不等於危險，量價結構與超買程度才是）

  止損同步改為波動基準：買入區間下緣 − 1×ATR14（不得等於買入區間下緣，DD-15 核心原則不變）；已回檔加分情境可改用 `EMA20` 下方 2%（兩者取較高者，止損不得寬於進場價 −10%）。
- **原因**：使用者實測觀察（2026-07-06 報告驗證：13 支追蹤股中 4 支死於「已追高，錯過買點」，另有多支 watch 到期）——L2 的職責就是挑「均線多頭、RSI 健康、板塊領先」的強勢股，這批股票的共同特徵正是「正在噴出、還沒回檔」；DD-12 卻要求買入區間設在 `EMA20~EMA10`（對強勢股而言是現價下方 4~8% 的深度回檔），5 個交易日 watch 窗口內等到的機率極低，**越強的股票越買不到**，與系統目的（找出可操作的買入機會）直接矛盾。固定 EMA 帶的第二個缺陷是不看個股波動：低波動強勢股可能只回檔 1~2% 就續漲，卻被要求等 4~8%。改用 ATR 錨定後，區間深度自動適應個股節奏（ATR 2% 的股票等 0.5~2% 回檔、ATR 6% 的股票等 1.5~6%），對應專業機構「ATR 比例回檔進場 + ATR 動態止損」的實務做法。上緣保留 0.25×ATR 緩衝而非直接用 `Close_Price`，避免訊號日收盤價本身就是進場價（盲目追價）；L1 既有 `MAX_ATR_PCT=8%` 上限（filter.py DD-8）保證 ATR 錨定的區間與止損寬度有天然上界。
- **不變**：`buy_zone`/`stop_loss` 仍由 AI 輸出字串（DD-12 架構取捨不變）；`tracker.py` 解析邏輯與「已追高」1.08 門檻、5 日 watch 上限全部不動（買入區間貼近現價後，這些機制的誤殺率自然下降）；突破/反轉策略買進區間規則不受影響（突破本就錨定 High_20D 貼近現價，反轉本質是等超跌，無此問題）；`scorer.py` L2 選股邏輯不動（挑強勢股是正確職責，問題在 L3 進場邏輯不匹配）。
- **N/A 連帶涵蓋**：`atr14=None` 時 `momentum_atr` 必為 `None`（後者以前者為分母），既有「`Momentum_ATR = N/A` → 直接排除」規則已連帶涵蓋，不需為 `ATR14` 新增獨立 N/A 排除規則。
- **捨棄**：無條件允許以現價追進（違反「拒絕盲目追價」原始精神，失去任何結構確認）；只加「極端強勢例外」而保留 EMA 帶為預設（治標——大部分強勢股仍會先撞上預設規則等回檔，watch 到期問題依舊）；改由 Python 端確定性計算 `buy_zone`（偏離「AI 給出完整交易計畫」架構，DD-12 已做過同樣取捨）；調整 tracker 的 watch 天數或「已追高」門檻（治標——區間錨點不改，等再久還是等不到）。
- → 詳見 [plans/2026-07-07-momentum-atr-anchored-buy-zone.md](../plans/2026-07-07-momentum-atr-anchored-buy-zone.md)

### DD-20: L3 精選上限由 5 支調降為 3 支

- **選擇**：`<Output_Constraint>` 與 System Prompt 的選股數量上限文字由「最多 5 支」改為「最多 3 支」；`main.py --top` 的預設值（env `MAX_OUTPUT`）由 5 改為 3；`pipeline.run()` 與 `rank_candidates()` 的 `top_n` 預設同步改為 3（`.env.example` 一併更新）。既有「不足時只輸出實際符合條件的數量、不勉強湊數」規則沿用不變。
- **原因**：使用者要求聚焦——每日手動跟單操作下 5 支偏多；上限收緊也迫使 AI 在同質化候選池中做更嚴格的橫向取捨，與 DD-21 的理由深化方向互補（支數減少，單支理由可以寫得更充分而不撐爆 token 預算）。
- **不變**：`MAX_CANDIDATES_TO_AI = 40`、`MAX_SECTOR_CANDIDATES = 8`、L2 排名上限 55（scorer DD-10）均不動——候選池廣度不變，只收緊最終輸出；`_enrich_fallback()` 沿用 `[:top_n]` 截斷，自然變 3 支，無需獨立修改。

### DD-21: reason 欄位敘事化——技術指標數值下放 strategy_reason，reason 聚焦非技術論述

- **選擇**：`reason` 欄位字數上限由 50 字放寬至 80~120 字，內容指引改為聚焦技術指標數值**以外**的論述——基本面體質（`Fwd_PE`/`Profit_Margin`/`Rev_Growth_YoY` 相對同批候選股的優劣與意涵）、產業 ETF 趨勢對個股的支撐或壓制、與當日 Regime 主推策略的契合度、`Short_Float_Pct` 等特殊風險背景——並明文禁止在 reason 中重複羅列技術指標數字（RSI/VTF/EMA 等數值的引用職責已由 `strategy_reason` 承載），技術面最多以一句定性描述帶過。`risk`/`strategy_reason`/`confidence_reason` 維持 50 字上限不變。
- **原因**：使用者回饋——實際報告中 reason 幾乎被技術指標數字複述佔滿，與 `strategy_reason` 高度重複，「為什麼是這家公司」的論述缺乏。DD-14 已把基本面三欄、DD-17 已把空頭比例送進候選池，但輸出端只給 50 字、且指引以「聚焦策略依據」開頭，AI 自然優先塞技術數字，非技術維度沒有敘事空間。
- **不變**：JSON 輸出 schema 不變（純 Prompt 文字修正）；`tracker.py`/`publisher.py` 零改動（reason 僅展示用、無解析邏輯，`reason-box` 版面不限長度）；`max_tokens=6000` 不需調整（DD-20 支數減少已釋放預算）。
- **捨棄**：新增獨立 `fundamental_reason` 欄位（DD-14 已捨棄過同構方案，欄位膨脹且與 reason 語意重疊）；要求 AI 引用候選池以外的新聞/事件/公司業務細節（資料不在 Prompt 內，幻覺風險高於敘事收益）。

## Acceptance Criteria

- [ ] Prompt 中 `<Market_Regime>` 區塊含有 `5日` 與 `20日` 漲跌的產業 ETF 資訊
- [ ] Markdown 表格 header 含 `VTF_Score`、`Momentum_ATR`、`Beta_60D`、`Earnings_Days_Left`（原 `Vol_Ratio`、`Price_20D_Pct` 已移除）
- [ ] Markdown 表格 header 含 `Short_Float_Pct`；缺值填 `N/A` 且不觸發 AI 排除
- [ ] BEAR_DISTRIBUTION Regime → `rank_candidates()` 回傳 `[]`，不發出 API 請求
- [ ] AI 輸出包含 `buy_zone`（格式 `$X~$Y`）、`stop_loss`、`hold_period`
- [ ] `compute_indicators()` 回傳 dict 含 `momentum_atr`、`vtf_score`、`beta_60d`、`earnings_days_left` keys
- [ ] `avg_vol_30d = 0` → `vtf_score` 不崩潰（vol_ratio 降級為 1.0）
- [ ] 停牌個股與 SPY 共同交易日不足 30 → `beta_60d = None`，表格顯示 `N/A`，AI 不排除
- [ ] 數據斷裂無財報歷史 → `earnings_days_left = None`，表格 `N/A`，AI 排除
- [ ] `data/ai_hints.json` 缺失/損壞/`prompt_lines` 為空 → Prompt 無第四區塊，與現狀逐字元相同
- [ ] `prompt_lines` 非空 → Prompt 末尾出現 `<Historical_Performance_Review>` 區塊，含樣本數警語
- [ ] 無近期財報但查到遠期日期 → `earnings_days_left = 99`，表格顯示 `99`，AI 不排除
- [ ] 爆量突破（VTF_raw = 10.0）→ 表格顯示 `10.00`（未被雙側裁切壓制）
- [ ] 極端出貨（VTF_raw = -8.0）→ 表格顯示 `-5.00`（下限保護）
- [ ] 浮點數欄位（Momentum_ATR、VTF_Score、Beta_60D）均保留 2 位小數
- [ ] 表格 header 含 `EMA5`、`EMA10`、`EMA20`、`Vol_vs_5DAvg`
- [ ] `<Output_Constraint>` 與 System Prompt 的選股數量上限為 3 支（DD-20）
- [ ] reason 欄位指引聚焦非技術論述（基本面/產業趨勢/Regime 契合度）且禁止重複羅列技術指標數字；`strategy_reason` 仍要求引用指標數值（DD-21）
- [ ] `compute_indicators()` 回傳 dict 含 `vol_vs_5d_avg` key
- [ ] `avg_vol_5 = 0`（新股或數據不足）→ `vol_vs_5d_avg` 不崩潰，降級為 1.0
- [ ] 表格 header 含 `High_20D`、`Vol_vs_20DAvg`、`EMA50`、`Low_20D`、`Stoch_K`、`RSI_5D_Ago`
- [ ] `compute_indicators()` 回傳 dict 含 `vol_vs_20d_avg` key
- [ ] `avg_vol_20 = 0`（新股或數據不足）→ `vol_vs_20d_avg` 不崩潰，降級為 1.0
- [ ] **DD-19 ATR14 欄位**：表格 header 含 `ATR14`，資料列為 `$` 格式美元值；`compute_indicators()` 回傳 dict 含 `atr14` key（>=15 筆歷史為正 float，不足時為 `None`）
- [ ] **DD-19 新規則生效**：SYSTEM_PROMPT 含「淺回檔」與 ATR 錨定買進區間規則，不再含「距 EMA5 已超過 +5%」「5MA 探針帶」舊規則字樣
- [ ] **DD-18 fallback 標記**：`_enrich_fallback()` 回傳的每筆結果含 `"is_fallback": True`；真實 AI 排序結果不含此鍵
- [ ] **DD-18 fallback 不進 watchlist**：`tracker.py` 對 `is_fallback=True` 的個股一律跳過，即使 `confidence` 剛好達到 `MIN_AI_CONFIDENCE` 門檻
- [ ] **DD-18 ai_count 誠實反映**：`main.py` 的 `stats["ai_count"]` 排除 `is_fallback` 條目；當日全數為 fallback 時 `ai_count == 0`
