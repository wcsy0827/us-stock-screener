# Scorer — L2 技術評分規格

## Purpose

對通過 L1 硬篩的股票進行技術指標評分（100 分制），並依當日 Regime 動態調整通過門檻，輸出進入 L3 AI 精選的候選池。

## Behavior

### 評分維度

| 項目 | 滿分 | 條件 |
|------|------|------|
| MA 多頭排列 | 20 | EMA5>EMA10>EMA20>EMA50，每條件 +6.67 分（3 條件） |
| RSI 健康區間 | 18 | 50~70=滿；40~50 或 70~80=半分；其他=0（BULL_TREND 下 50~80=滿，>80=半分，見 DD-6） |
| MACD 柱狀體 | 17 | 正且遞增=滿；正但遞減=半分；負=0 |
| 量能放大（含趨勢） | 15 | VTF 基礎分數 × 量能趨勢係數（見 DD-4/DD-7） |
| 多週期動能 | 15 | 20 日 ATR 倍數 × 5 日方向確認（見 DD-5/DD-8） |
| 相對強度 RS | 15 | 個股 5 日報酬率 − 板塊 ETF 5 日報酬率（見 DD-9） |

- **RSI > 80**：軟過濾，RSI 分數 = 0（或 BULL_TREND 下半分），但股票**不驅逐**（仍可憑其他項目過門檻）
- **不得**：以 RSI > 80 作為硬條件把股票完全排除（見 DD-1）

### 動態門檻（依 Regime）

- 預設門檻：`min_score`（CLI 參數，預設 60）
- `PANIC_REVERSAL` 環境：門檻降至 **40 分**
- `CONSOLIDATION_VOLATILE` 環境：門檻提高至 **max(min_score, 65) 分**（高 VIX 整理期至少 65 分，若 min_score 更高則以 min_score 為準）
- `PANIC_REVERSAL` 環境額外：RSI < 35 **且** 20 日跌幅 > 15% 的股票**強制放行**（不受分數限制）

### 排名上限（DD-10）

- 品質門檻篩選後，若通過數量 `> L2_TARGET_COUNT`（55），依總分取第 55 名的分數為 `cutoff_score`，只保留 `total_score >= cutoff_score` 的股票
- 同分邊界一律保留，不引入額外 tie-breaker 排序鍵，允許小幅超出 50~60 目標區間
- `force_pass`（PANIC_REVERSAL 強制放行股）不受排名上限排除，無條件保留
- 通過數量本來就 `<= L2_TARGET_COUNT` 時（弱勢盤面）不觸發排名上限，維持原樣，不硬湊數量

### 強制放行條件（`_is_oversold_reversal_candidate`）

```
RSI < 35
AND 20 日漲跌幅 <= -15%
→ 加入 force_pass 集合，不論分數直接進入 L3
```

- **若非** `PANIC_REVERSAL` Regime：不啟用強制放行，force_pass 為空集合
- 強制放行股票**仍然會計分**，只是不被分數門檻擋住

## Interface

```python
def score_all(
    symbols: list[str],
    price_data: dict[str, pd.DataFrame],
    min_score: float = 60.0,
    regime: str = "",                  # 空字串 = 使用預設門檻
    sector_map: dict[str, str] | None = None,  # {symbol: sector}，供 RS 計算
) -> list[dict]:
    """
    回傳依總分降序排列的候選股列表。
    每筆含 symbol, price, sector, total_score, ma_score, rsi_score,
           macd_score, volume_score, momentum_score, rs_score。

    品質門檻篩選後，若通過數量 > L2_TARGET_COUNT（55），疊加排名上限只保留
    Top N（同分邊界保留，不引入 tie-breaker），force_pass 股票不受排名上限排除（DD-10）。
    """

def score_stock(
    sym: str,
    df: pd.DataFrame,
    regime: str = "",
    sector: str = "",
    price_data: dict | None = None,
) -> dict:
    """計算單支股票分數。df 需含至少 20 筆 Close 與 Volume。"""

def _is_oversold_reversal_candidate(sym: str, df: pd.DataFrame) -> bool:
    """RSI < 35 且 20 日跌幅 > 15% → True。"""

def _calc_rs_score(sym: str, df: pd.DataFrame, sector: str, price_data: dict) -> float:
    """計算相對強度分數（個股 5 日報酬率 − 板塊 ETF 5 日報酬率）。"""
```

## Design Decisions

### DD-6: BULL_TREND 下 RSI 健康區間擴大至 80

- **選擇**：Regime = BULL_TREND 時，RSI 50~80 均給滿分（18 分）；RSI > 80 給半分（9 分，軟過濾維持）
- **原因**：在 BULL_TREND 中，RSI 長期維持 70~80 是強勢多頭的健康表現（"momentum overbought"），並非超買警告。以半分懲罰會讓系統在牛市中過度排斥最強勢股票，違背動能策略的核心精神
- **限制**：只在 BULL_TREND Regime 下生效；CONSOLIDATION/PANIC/BEAR 環境維持原有 70~80=半分邏輯，避免在不確定市場中放水
- **捨棄**：所有 Regime 都擴大區間（非 BULL 環境下 RSI 70-80 確實是超買警告）

### DD-7: 量能評分加入 5 日趨勢係數

- **選擇**：量能分數 = VTF 基礎分數 × 量能趨勢係數（`vol_trend_5d`）
  - `vol_trend_5d = np.polyfit(range(5), vol[-5:], 1)[0] / avg_vol_30d`，clip 至 [-1.0, 1.0]
  - `vol_trend_5d > +0.2`（持續放量）→ × 1.0；`-0.1~0.2`（平穩）→ × 0.85；`< -0.1`（縮量）→ × 0.65
  - 防禦：`len(vol[-5:]) < 5` 或 `avg_vol_30d == 0` → `vol_trend_5d = 0`（套用平穩係數 × 0.85）
- **原因**：連續 5 日放量（機構累積模式）遠比單日爆量更能確認主力意圖。反過來，今日爆量但過去 5 天都在縮量，可能是末日衝量的前兆
- **捨棄**：只用今日量 / 30 日均量（單日爆量雜訊高，縮量趨勢中的單日爆量尤其危險）

### DD-8: 多週期動能（20 日 + 5 日方向確認）

- **選擇**：`_score_momentum()` 加入 5 日短期動能（`momentum_5d_atr`）作為方向確認
  - `momentum_20d_atr >= 2.0 AND momentum_5d_atr >= 0.5` → 15 分（中短線一致，最強）
  - `momentum_20d_atr >= 2.0 AND momentum_5d_atr < 0` → 7.5 分（中期強但短線回調中）
  - `1.0 <= momentum_20d_atr < 2.0 AND momentum_5d_atr >= 0.3` → 7.5 分
  - `momentum_20d_atr >= 1.0 AND momentum_5d_atr < 0` → 3.75 分
  - `0 < momentum_20d_atr < 1.0` → 3.75 分
  - 其他（含負值）→ 0 分
- **原因**：防止選到「20 日漲很多但最近 5 日已反轉」的標的。20 日動能強但 5 日動能轉負，代表股票已進入短線回調，此時買入風險明顯較高
- **捨棄**：只用 20 日單一窗口（忽略近期方向）

### DD-9: 相對強度 RS 維度

- **選擇**：新增 15 分 RS 分項，計算公式：`rs_5d = 個股 5 日報酬率 − 板塊 ETF 5 日報酬率`
  - `rs_5d >= +2%` → 15 分；`+0.5% ≤ rs_5d < +2%` → 8 分；`-0.5% ≤ rs_5d < +0.5%` → 3 分；`< -0.5%` → 0 分
  - 板塊 ETF 從 `SECTOR_ETF_MAP`（market.py）查找；板塊未知或 ETF 缺資料 → fallback 至 SPY
  - 需要 Batch 0 已將板塊 ETF 納入 `price_data`
- **原因**：學術研究（Jegadeesh & Titman 1993）與 Fama-French 動能因子均支持相對強度是短線最強預測因子。板塊內的領頭羊比板塊平均表現更可能持續上漲，是比絕對動能更精準的訊號
- **權重替換**：MA 25→20；RSI 20→18；MACD 20→17；Volume 20→15；Momentum 15→15；RS 0→15（合計維持 100 分）
- **sector 欄位**：score_all() 回傳 dict 中必須包含 `sector`，供 ranker.py `_diversify_candidates()` 使用
- **捨棄**：以個股對 SPY 計算 RS（忽略板塊輪動效果，無法區分「整個板塊都在漲」vs「板塊內個別強股」）

### DD-10: L2 候選池排名上限，穩定輸出數量至 50~60 支

- **選擇**：品質門檻篩選出 `qualified` 後，若數量 `> L2_TARGET_COUNT`（55），取第 55 名的分數為 `cutoff_score`，只保留 `total_score >= cutoff_score` 的股票（同分邊界一律保留，不引入 tie-breaker）；`force_pass`（PANIC_REVERSAL 強制放行股）不受此排名上限排除；`qualified` 數量本來就 `<= 55` 時不觸發，維持原樣
- **原因**：原本的固定分數門檻（`min_score`，Regime 感知調整）通過數量完全跟著大盤強弱擺動——BULL_TREND 廣度 63.9% 時 119 支通過 70 分門檻，換成弱勢盤面可能剩不到 20 支，強勢盤面可能破 200 支，無法穩定收斂到使用者期望的 50~60 支範圍
- **不變**：既有 Regime 感知的分數門檻（`effective_min`）與 DD-1/DD-2/DD-3 的強制放行機制完全保留，排名上限是疊加在品質門檻之上的「天花板」，不是取代品質門檻
- **捨棄**：完全改用純排名制（直接取 Top 55，不管分數）——弱勢盤面會被迫湊出 55 支候選，可能硬拉進 30 幾分的低品質股充數，與使用者「數量太多」的訴求方向相反；用額外指標（如 RS_vs_Sector）當同分 tie-breaker——50~60 是彈性目標區間非精確值，不需要為了卡在單一數字引入額外排序鍵增加複雜度
- → 詳見 `plans/2026-07-02-l2-rank-cap.md`

### DD-1: RSI > 80 改為軟過濾

- **選擇**：RSI > 80 的股票 RSI 分項得 0 分，但不從候選池移除
- **原因**：PANIC_REVERSAL 環境下，超強勢股（RSI > 80）可能是唯一抵抗跌勢的防禦性標的。硬排除會在最需要它的市場環境中把最強的股票排掉，邏輯矛盾。
- **捨棄**：`if rsi > 80: return [] (hard filter)` — 在 PANIC 環境導致候選池清空

### DD-2: PANIC_REVERSAL 強制放行

- **選擇**：RSI < 35 且 20 日跌 > 15% → 強制進 L3
- **原因**：超賣反轉股的 L2 分數通常 < 20 分（MA/RSI/Momentum 全低），但這正是反轉策略的目標標的。若以 40 分門檻過濾，仍然會被排除。
- **捨棄**：只降低門檻至 40 分（反轉股得 0-20 分，仍被 40 分門檻排除）

### DD-3: PANIC_REVERSAL 門檻 40 分而非 60 分

- **選擇**：PANIC_REVERSAL 時使用 40 分門檻
- **原因**：PANIC 環境下，「輕度超跌但尚未崩潰」的股票（得分 40-60 分）也有價值，應讓 AI 判斷。60 分門檻在 PANIC 時過於嚴苛，讓 AI 看不到這些候選。
- **捨棄**：PANIC 時維持 60 分（AI 候選池過窄）

## Design Decisions（續）

### DD-4: 量能 K_pos 條件阻斷型

- **選擇**：`K_pos = (Close - Low) / (High - Low)`，爆量但 K_pos < 0.6 時量能分項直接歸零
- **原因**：爆量黑 K 或長上影線（K_pos 低）是主力高檔出貨的典型特徵。原純量能比邏輯會誤將出貨訊號給滿分 20 分。K_pos 閾值 0.6 意指「收盤在當日振幅上 60% 以上才認定為多頭推進量」
- **捨棄**：連續型 VTF（`raw_score × K_pos`），過渡更平滑但可觀察性低，留作第二版優化

### DD-5: ATR 倍數法取代絕對 % 動能

- **選擇**：`momentum_atr = (Close[-1] - Close[-20]) / ATR14`，門檻 ≥2.0 ATR = 滿分
- **原因**：高 Beta 科技股（ATR% ≈ 3-4%）與低 Beta 防禦股（ATR% ≈ 0.5-1%）在同一百分比門檻下競爭不公平。ATR 倍數讓各股在自身波動率基準下競爭
- **ATR 計算**：`pandas_ta.atr(high, low, close, length=14)`，已在 df 中包含所需 OHLCV 欄位
- **捨棄**：絕對百分比（≥10%=滿）會讓科技股天然霸榜，低 Beta 強勢股被活埋

## Acceptance Criteria

- [ ] 正常環境：RSI=85 的股票，RSI 分項=0，但若其他項目合計 >= 60，仍出現在候選池
- [ ] 正常環境：RSI=85 + 總分 55 → 不出現在候選池（分數不足）
- [ ] PANIC_REVERSAL：RSI=32、20日跌22% → 出現在候選池（強制放行），即使總分 = 8
- [ ] PANIC_REVERSAL：RSI=50、總分=45 → 出現在候選池（40分門檻）
- [ ] PANIC_REVERSAL：RSI=50、總分=35 → 不出現在候選池（未達 40 分且非強制放行）
- [ ] 非 PANIC_REVERSAL：RSI=32、20日跌22% → 若總分 < 60，不出現在候選池（強制放行僅 PANIC 啟用）
- [ ] 量能 K_pos：High=110, Low=100, Close=101（K_pos=0.1）且量≥1.5x → volume_score = 0（出貨阻斷）
- [ ] 量能 K_pos：High=110, Low=100, Close=108（K_pos=0.8）且量≥1.5x → volume_score = 20
- [ ] 量能 K_pos：High=110, Low=100, Close=108（K_pos=0.8）且量 1.0x-1.5x → volume_score = 10
- [ ] ATR 動能：20 日上漲 = 2.5 ATR → momentum_score = 15（滿分）
- [ ] ATR 動能：20 日上漲 = 1.2 ATR → momentum_score = 7.5（半分）
- [ ] ATR 動能：20 日下跌 → momentum_score = 0
