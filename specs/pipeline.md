# Pipeline — 流程編排規格

## Purpose

將 universe → fetcher → filter → scorer → market → analyzer → ranker 各步驟串接成完整的選股流程，管理快取、錯誤降級，並回傳供 main.py / publisher.py 使用的彙總結果。

## Behavior

### 執行步驟順序（含 2.5 和 5.5）

| Step | 模組 | 輸入 | 輸出 | 快取 |
|------|------|------|------|------|
| 1 | universe.py | — | symbols: list[str] | 無 |
| 2 | fetcher.py | symbols + ETF tickers | price_data: dict（含板塊 ETF 及 SPY） | `.cache/price_YYYYMMDD.pkl`（當日） |
| 2.5 | market.py | price_data | regime_quick: str | 無 |
| 3 | fetcher.py | symbols | info_data: dict | `.cache/info_YYYYMMDD.json`（7 日） |
| 3.5 | earnings.py | symbols, info_data | earnings_data: dict | `.cache/earnings_registry.json`（30 日） |
| 4 | filter.py | price_data, info_data | liq_filtered: list[str] | 無 |
| 4.5 | earnings.py + filter.py | liq_filtered, info_data | l1_passed: list[str] | `.cache/earnings_registry.json` 更新 |
| 5 | scorer.py | l1_passed, price_data, regime_quick | candidates: list[dict] | 無 |
| 5.5 | market.py | candidate_sectors | market_context: dict | 無 |
| 5.7 | analyzer.py | `data/performance_history.json` | `data/ai_hints.json` | 無（每輪全量重寫） |
| 6 | ranker.py | candidates, price_data, info_data, market_context | ranked: list[dict] | 無 |

- **必須**：Step 2.5 在 Step 5 之前執行，regime_quick 必須傳入 `score_all()`
- **必須**：Step 5.5 在 Step 6 之前執行，只下載候選股所在的產業 ETF
- **必須**：Step 5.7 在 VIX Gate 之後、Step 6 之前執行；analyzer 失敗印警告後繼續，不中斷流程（DD-7）
- **若** Step 6 失敗：降級使用 L2 分數前 top_n 名（`_enrich_fallback()`），不讓整個流程中斷

### 快取策略

| 資料類型 | 快取機制 | 失效條件 |
|----------|----------|----------|
| 日 K 數據（含板塊 ETF） | `price_YYYYMMDD.pkl`，日期不符即重下；若快取中缺少 SPY 也視為無效 | 跨日或 SPY 缺失 |
| 基本面資訊 | 掃描 7 日內最新 `info_*.json` | 超過 7 日無任何快取 |
| 財報日期 | `earnings_registry.json`，per-symbol TTL 判斷 | 各股 cached_at 超過 30 天 |
| ETF / 大盤（AI Prompt 用） | 無快取 | 每次 Step 5.5 都重新下載，確保 AI 看到最新數據 |

- `--no-cache` 旗標：跳過所有快取，強制重下
- `clear_old_cache()`：執行前清除超過 7 日的 `.cache/` 檔案

### Step 2 收盤完整性防呆（DD-6）

`fetcher.trim_incomplete_session(price_data, now=None)` 在 Step 2 快取/下載完成後、統計計數與 `market_date` 計算之前執行：

- 以 `price_data["SPY"]` 最後一列日期為基準；若該日期等於美東當下日期（`America/New_York`）**且**美東現在時間早於當天 `16:15`（收盤 16:00 + 15 分鐘 settle buffer），判定該列為尚未收盤的殘缺 K 棒
- 判定成立時，逐股比對日期並過濾掉那一列（非整批 `.iloc[:-1]` 盲刪），過濾後列數 `< 20` 的股票整支移除
- 週末/假日、或已收盤後執行：no-op，不印訊息
- `market_date`（`pipeline.py` 現行第 78 行左右）因此自然回退到前一個完整交易日，Step 2.5 的 `fetch_regime_quick()` 也會讀到同一份已修剪的 `price_data`，不需額外改動

### L1 硬篩條件（filter.py，兩段執行）

**第一段（Step 4 — 流動性）**
- 股價 > $5
- 30 日平均日成交額 > $1,000 萬（avg_vol_30 × close，env: MIN_DOLLAR_VOLUME，預設 $10M）
- 市值 > 3 億（$300M）；市值為 None（API 缺失）視同不足，直接排除
- 近 5 日有成交（避免停牌股）

**第二段（Step 4.5 — 財報防禦牆）**
- 未來 3 天內無已知財報（`apply_earnings_filter()`）
- `earnings_data[sym] is None` → 視為無已知財報，通過
- 詳見 `specs/earnings.md`

### 回傳結構（`summary` dict）

```python
{
    "success": bool,
    "error": str | None,
    "total": int,           # S&P 500 支數
    "downloaded": int,      # 成功下載日 K 的支數
    "l1_count": int,        # L1 通過支數
    "l2_count": int,        # L2 通過支數
    "ranked": list[dict],   # AI 精選結果（BEAR_DISTRIBUTION 時為 []）
    "market_context": dict, # 大盤背景（含 regime）
}
```

## Interface

```python
def run(
    min_score: float = 60.0,
    top_n: int = 10,
    dry_run: bool = False,
    use_cache: bool = True,
) -> dict:
    """執行完整選股流程，回傳 summary dict。"""
```

## Design Decisions

### DD-1: Step 2.5 快速 Regime 插在 Step 2 之後、Step 5 之前

- **選擇**：在日 K 下載完成後立即計算 Regime，不等到 Step 5.5
- **原因**：scorer 的動態門檻（60 vs 40 分）和強制放行邏輯必須在評分時就知道 Regime。若等 Step 5.5 才計算，scorer 已執行完畢，無法套用。
- **捨棄**：在 Step 5.5 統一計算 Regime（scorer 無法使用動態門檻）

### DD-2: Step 5.5 只下載候選股產業的 ETF

- **選擇**：`candidate_sectors = {info_data.get(c["symbol"], {}).get("sector") for c in candidates}`，只取候選股所在產業
- **原因**：下載 11 個 ETF 增加耗時。L2 篩完後候選通常來自 3-5 個產業，只下載這幾支 ETF 夠用。
- **捨棄**：下載全部 11 個 ETF（多餘網路請求，ETF 不在候選產業中也無助 AI 推理）

### DD-4: 板塊 ETF 納入 Step 2 全域下載

- **選擇**：`pipeline.py` 在 Step 2 下載 S&P 500 股票時，同步將 11 支板塊 ETF（XLK/XLV/XLF 等）與 SPY 一併加入批次，存入 `.cache/price_YYYYMMDD.pkl`
- **原因**：scorer.py 在 Step 5 計算 L2 相對強度（RS）分數需要板塊 ETF 的日 K 數據，但 Step 5.5 尚未執行，ETF 尚未下載。將 ETF 納入 Step 2 讓 `price_data` 在 Step 5 時就包含 ETF 資料，避免 scorer.py 內部觸發臨時 I/O（違反「集中下載、快取複用」原則）。
- **與 Step 5.5 的區別**：Step 5.5 的 `fetch_market_context()` 仍然對 SPY 和板塊 ETF 發出新的網路請求，確保 AI Prompt 使用的是最新數據（含完整 60 日歷史及最新漲跌）。Step 2 快取的 ETF 數據供 scorer.py 使用，Step 5.5 的結果供 AI 使用，兩者用途不同。
- **快取有效性**：若現有快取缺少 SPY，視同無效快取並強制重新下載（避免舊快取導致 RS 計算缺失 ETF 數據）。
- **廣度計算保護**：`calculate_market_breadth()` 必須排除 ETF tickers（見 DD-5），確保板塊 ETF 不計入 S&P 500 廣度分母。
- **捨棄**：在 Step 2.5 另行下載 ETF（需改動 `fetch_regime_quick()` 接口，且 VIX Gate 邏輯複雜化）；在 scorer.py 內部觸發即時下載（破壞模組職責邊界，且 scorer 不應有 I/O 副作用）

### DD-5: 市場廣度計算排除板塊 ETF

- **選擇**：`calculate_market_breadth()` 內部以 `_BREADTH_EXCLUDED` frozenset 過濾，跳過 11 支板塊 ETF 及 SPY
- **原因**：DD-4 將 ETF 加入 `price_data` 後，若不過濾，ETF 會被計入廣度分母。板塊 ETF 是追蹤工具，不是 S&P 500 成分股，不應影響廣度百分比。
- **捨棄**：在 `pipeline.py` 傳入篩選後的 `price_data`（增加額外資料結構，且 `fetch_market_context` 的廣度計算也需同步修改）

### DD-6: 盤中執行自動捨棄殘缺當日 K 棒

- **選擇**：Step 2 完成後統一呼叫 `trim_incomplete_session()`，比對 SPY 最後一列日期與美東當下時間，未收盤（美東 16:15 前）則逐股捨棄該列，讓 `market_date` 自動回退到前一個完整交易日
- **原因**：`yf.download(interval="1d")` 在美股盤中會回傳「今天」的殘缺 OHLCV（非最終收盤值），而 `pipeline.py` 原本直接用 `spy_df.index[-1]` 當 `market_date`，沒有完整性檢查。這會讓報告標籤顯示完整日期，內容卻是盤中殘缺數據跑出來的評分與 AI 精選，與 `CLAUDE.md`「盤中觸發＝自動拿到前一日完整報告」的既有心智模型不符
- **捨棄**：中斷執行並警告使用者重跑——會打斷 CI 自動化流程，且使用者原本就預期盤中觸發等於前一日報告，不需要額外中斷；僅印警告但仍用殘缺數據繼續跑——無法防止污染 `market_date` 與下游評分
- → 詳見 `plans/2026-07-02-intraday-partial-bar-guard.md`

### DD-7: Step 5.7 本地績效診斷插在 VIX Gate 之後、Step 6 之前

- **選擇**：`analyzer.generate_hints(market_date=...)` 在 VIX Gate 通過後、Step 6 ranker 之前執行，以 try/except 攔截，失敗印 `[pipeline] 警告` 後繼續（與 Step 2.5/3/5.5 同慣例，enhancement 非關鍵路徑）。
- **原因**：hints 的唯一消費者是 Step 6 的 L3 Prompt（ranker DD-16），必須在 ranker 之前生成；VIX Gate 中斷時 L3 不執行，hints 無消費者，放在 Gate 之後可免做白工。tracker 結算在 main.py 的 ranker 之後，analyzer 讀到的是截至前一輪執行的結算資料（1-cycle lag，與 tracker DD-11 一致，見 `specs/analyzer.md` DD-1）。
- **捨棄**：放在 main.py 的 run_tracker 之後（hints 要等下一輪才被消費，寫入時機與消費時機分離、更難推理）；analyzer 失敗中斷流程（歷史回饋是加分項，不該阻斷選股主流程）。→ 詳見 `plans/2026-07-03-analyzer-ai-hints.md`

### DD-3: L3 失敗降級至 L2 前 top_n

- **選擇**：`_enrich_fallback()` 用 L2 分數排序補齊 AI 輸出欄位（buy_zone 等欄位填 "-"）
- **原因**：DeepSeek API 偶爾超時或服務中斷；整個流程中斷比輸出 L2 結果更不可接受，尤其在 GitHub Actions 自動化場景。
- **捨棄**：L3 失敗時回傳空列表（報告完全沒有推薦，用戶無法行動）

## Acceptance Criteria

- [ ] `--no-cache`：price_data 不從 `.cache/` 讀取，重新下載
- [ ] `regime_quick` 傳入 `score_all()` 且影響到 `effective_min` 值（可用 print 驗證）
- [ ] Step 3 的 info 快取：`.cache/` 有 6 日前的 `info_*.json` → 直接讀取不重下
- [ ] Step 3.5：首次執行 → `earnings_registry.json` 被建立；再次執行 → Tier 3 不觸發
- [ ] Step 4.5：有財報股票 → 被排除，`summary["l1_count"]` 為財報過濾後的數量
- [ ] DeepSeek API 失敗：流程繼續，`ranked` 為 `_enrich_fallback()` 的結果（非空列表）
- [ ] BEAR_DISTRIBUTION → `ranked = []`，`summary["success"] = True`（非錯誤）
