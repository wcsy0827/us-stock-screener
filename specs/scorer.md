# Scorer — L2 技術評分規格

## Purpose

對通過 L1 硬篩的股票進行技術指標評分（100 分制），並依當日 Regime 動態調整通過門檻，輸出進入 L3 AI 精選的候選池。

## Behavior

### 評分維度

| 項目 | 滿分 | 條件 |
|------|------|------|
| MA 多頭排列 | 25 | EMA5>EMA10>EMA20>EMA50，每條件 +8.33 分（3 條件） |
| RSI 健康區間 | 20 | 50~70=滿；40~50 或 70~80=半分；其他=0 |
| MACD 柱狀體 | 20 | 正且遞增=滿；正但遞減=半分；負=0 |
| 量能放大 | 20 | ≥1.5x 均量=滿；≥1.0x=半分 |
| 20 日動能 | 15 | >10%=滿；>5%=半分；>0%=1/4 分 |

- **RSI > 80**：軟過濾，RSI 分數 = 0，但股票**不驅逐**（仍可憑其他項目過門檻）
- **不得**：以 RSI > 80 作為硬條件把股票完全排除（見 DD-1）

### 動態門檻（依 Regime）

- 預設門檻：`min_score`（CLI 參數，預設 60）
- `PANIC_REVERSAL` 環境：門檻降至 **40 分**
- `PANIC_REVERSAL` 環境額外：RSI < 35 **且** 20 日跌幅 > 15% 的股票**強制放行**（不受分數限制）

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
    regime: str = "",          # 空字串 = 使用預設門檻
) -> list[dict]:
    """
    回傳依總分降序排列的候選股列表。
    每筆含 symbol, total_score, ma_score, rsi_score, macd_score, volume_score, momentum_score。
    """

def score_stock(sym: str, df: pd.DataFrame) -> dict:
    """計算單支股票分數。df 需含至少 20 筆 Close 與 Volume。"""

def _is_oversold_reversal_candidate(sym: str, df: pd.DataFrame) -> bool:
    """RSI < 35 且 20 日跌幅 > 15% → True。"""
```

## Design Decisions

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

## Acceptance Criteria

- [ ] 正常環境：RSI=85 的股票，RSI 分項=0，但若其他項目合計 >= 60，仍出現在候選池
- [ ] 正常環境：RSI=85 + 總分 55 → 不出現在候選池（分數不足）
- [ ] PANIC_REVERSAL：RSI=32、20日跌22% → 出現在候選池（強制放行），即使總分 = 8
- [ ] PANIC_REVERSAL：RSI=50、總分=45 → 出現在候選池（40分門檻）
- [ ] PANIC_REVERSAL：RSI=50、總分=35 → 不出現在候選池（未達 40 分且非強制放行）
- [ ] 非 PANIC_REVERSAL：RSI=32、20日跌22% → 若總分 < 60，不出現在候選池（強制放行僅 PANIC 啟用）
