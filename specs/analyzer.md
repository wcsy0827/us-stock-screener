# analyzer.py

## Purpose

本地績效診斷：讀取 `data/performance_history.json` 的已結算交易，歸納 Regime、策略、產業三維度的賺賠統計，輸出 `data/ai_hints.json`，供 ranker 在 L3 Prompt 末尾注入實戰回饋。不呼叫任何外部 API、不下載數據。

## Behavior

- **必須**：只統計 `exit_reason ∈ {CLOSED_PROFIT, CLOSED_LOSS, CLOSED_TRAILING_STOP, FORCE_EXPIRED}` 且 `return_pct` 非 null 的記錄（與 `publisher._load_performance_stats` 同口徑）。
- **必須**：冷啟動安全——`performance_history.json` 不存在、損壞或無有效記錄時，仍寫出 `ai_hints.json`（`total_settled=0`、`prompt_lines=[]`），不拋例外。
- **必須**：每一維度分組樣本數 < `MIN_GROUP_SAMPLES`（3）時不產生該組 hint；有效總樣本 < `MIN_TOTAL_SAMPLES`（5）時 `prompt_lines` 全空（僅輸出統計主體）。
- **必須**：hint 為描述性統計（含樣本數、勝率、平均報酬），不得寫成指令式結論（「禁止選XX」）；小樣本警語固定附加，防止 AI 對少量樣本過度反應。
- **必須**：所有 `print()` 用繁體中文 `[analyzer]` 前綴；失敗只影響本模組，不中斷 pipeline（pipeline 端以 try/except 攔截，見 `specs/pipeline.md` DD-7）。
- **不得**：修改 `performance_history.json`（唯讀）；不得觸碰數據下載與 L2 評分邏輯。

## Interface

```python
MIN_GROUP_SAMPLES = 3   # 單一分組最少樣本數，低於此不產生該組 hint
MIN_TOTAL_SAMPLES = 5   # 有效總樣本低於此，prompt_lines 全空

def generate_hints(market_date: str | None = None) -> dict:
    """讀取 performance_history.json，統計三維度賺賠關聯，
    寫入 data/ai_hints.json 並回傳該 dict。冷啟動回傳空統計。"""
```

`data/ai_hints.json` schema：

```json
{
  "generated_at_utc": "2026-07-03T21:31:00Z",
  "market_date": "2026-07-02",
  "total_settled": 12,
  "dimensions": {
    "by_regime":   [{"key": "BULL_TREND", "trades": 5, "win_rate": 60.0, "avg_return_pct": 1.25}],
    "by_strategy": [{"key": "動能策略", "trades": 7, "win_rate": 42.9, "avg_return_pct": -0.31}],
    "by_sector":   [{"key": "Technology", "trades": 4, "win_rate": 75.0, "avg_return_pct": 2.10}]
  },
  "prompt_lines": ["【Regime】BULL_TREND：5 筆結算，勝率 60.0%，平均報酬 +1.25%"]
}
```

- `dimensions` 各維度含**全部**分組（不受 `MIN_GROUP_SAMPLES` 限制，供審計）；`prompt_lines` 只含達門檻的分組。
- `market_date` 由 pipeline 注入（`summary["market_date"]`），記錄 hints 生成時對應的數據基準日。

## Design Decisions

### DD-1: 內嵌 pipeline Step 5.7，不新增 CLI 參數；讀前輪結算（1-cycle lag）

- **選擇**：analyzer 在 `pipeline.run()` 的 Step 5.5 之後、Step 6 ranker 之前執行（Step 5.7），無獨立觸發參數。實際流程中 tracker 結算在 ranker 之後（main.py），故 analyzer 讀到的是「截至前一輪執行」的結算資料——當日結算進入次日 hints，與 tracker DD-11 的 1-day lag 哲學一致。
- **捨棄**：把結算搬到 ranker 之前（重構 main.py/tracker DD-11 執行順序，風險高、只換得一天新鮮度）；獨立 CLI 參數（GitHub Actions 手動觸發模式下多餘）。→ 詳見 `plans/2026-07-03-analyzer-ai-hints.md`

### DD-2: 維度為 Regime × 策略 × 產業；產業代理基本面

- **選擇**：三維度全部取自既有歸檔欄位（`signal_details.entry_regime` / `signal_details.assigned_strategy` / `meta_data.sector`）。「基本面關聯」以產業（sector）代理。
- **原因**：歸檔記錄沒有 Fwd_PE / Profit_Margin / Rev_Growth_YoY（ranker DD-14 欄位只進 Prompt、未進 watchlist 與歸檔），真基本面維度無資料可算。
- **捨棄**：擴充 tracker 歸檔基本面欄位（觸碰 tracker.py，且既有歷史記錄無此欄位，需要長時間累積後才有樣本；留待未來需要時獨立立案）。→ 詳見 `plans/2026-07-03-analyzer-ai-hints.md`

### DD-3: 最小樣本門檻 + 描述性語氣，防小樣本過擬合

- **選擇**：分組 < 3 筆不產生 hint；總樣本 < 5 筆完全不輸出 `prompt_lines`。hint 一律附樣本數，區塊尾固定加「樣本數有限，僅供權衡參考，不得覆蓋 Market_Regime 的策略方向」警語（警語由 ranker 在組裝第四區塊時附加，見 ranker DD-16）。
- **捨棄**：無門檻全量輸出（2 筆虧損就讓 AI 迴避整個產業，統計噪音）；指令式結論（「不要選能源股」——越權，L3 決策主權在 Regime 與候選池）。→ 詳見 `plans/2026-07-03-analyzer-ai-hints.md`

### DD-4: ai_hints.json 為可再生衍生檔，statistics 與渲染同源

- **選擇**：analyzer 同時輸出結構化統計（`dimensions`）與渲染好的 `prompt_lines`；ranker 只盲讀 `prompt_lines` 附加，不做任何統計邏輯。檔案每輪全量重寫，刪除後下一輪自動重建；CI 的 `git add data/` 既有步驟自動持久化（免改 workflow）。
- **捨棄**：ranker 端自行渲染（統計與呈現分散兩個模組，改 hint 格式要動兩處）；只存統計不存文字（同上）；不落地直接記憶體傳遞（違反 ai_hints.json 檔案契約，且失去可審計性）。→ 詳見 `plans/2026-07-03-analyzer-ai-hints.md`

## Acceptance Criteria

- [ ] `performance_history.json` 不存在時，pipeline 全流程照常完成，`ai_hints.json` 內容為空統計
- [ ] 有 ≥3 筆同組結算記錄時，該組出現在 `prompt_lines` 且數字與手算一致
- [ ] 分組樣本 <3 不輸出該組；總樣本 <5 時 `prompt_lines` 為空陣列
- [ ] analyzer 拋出任何例外時 pipeline 印警告後繼續執行 L3
- [ ] pytest（`tests/test_analyzer.py`）全數通過，不連網、不觸碰 `data/`
