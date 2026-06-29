# Pipeline — 流程編排規格

## Purpose

將 universe → fetcher → filter → scorer → market → ranker 六個步驟串接成完整的選股流程，管理快取、錯誤降級，並回傳供 main.py / publisher.py 使用的彙總結果。

## Behavior

### 執行步驟順序（含 2.5 和 5.5）

| Step | 模組 | 輸入 | 輸出 | 快取 |
|------|------|------|------|------|
| 1 | universe.py | — | symbols: list[str] | 無 |
| 2 | fetcher.py | symbols | price_data: dict | `.cache/price_YYYYMMDD.pkl`（當日） |
| 2.5 | market.py | price_data | regime_quick: str | 無 |
| 3 | fetcher.py | symbols | info_data: dict | `.cache/info_YYYYMMDD.json`（7 日） |
| 3.5 | earnings.py | symbols, info_data | earnings_data: dict | `.cache/earnings_registry.json`（30 日） |
| 4 | filter.py | price_data, info_data | liq_filtered: list[str] | 無 |
| 4.5 | earnings.py + filter.py | liq_filtered, info_data | l1_passed: list[str] | `.cache/earnings_registry.json` 更新 |
| 5 | scorer.py | l1_passed, price_data, regime_quick | candidates: list[dict] | 無 |
| 5.5 | market.py | candidate_sectors | market_context: dict | 無 |
| 6 | ranker.py | candidates, price_data, info_data, market_context | ranked: list[dict] | 無 |

- **必須**：Step 2.5 在 Step 5 之前執行，regime_quick 必須傳入 `score_all()`
- **必須**：Step 5.5 在 Step 6 之前執行，只下載候選股所在的產業 ETF
- **若** Step 6 失敗：降級使用 L2 分數前 top_n 名（`_enrich_fallback()`），不讓整個流程中斷

### 快取策略

| 資料類型 | 快取機制 | 失效條件 |
|----------|----------|----------|
| 日 K 數據 | `price_YYYYMMDD.pkl`，日期不符即重下 | 跨日 |
| 基本面資訊 | 掃描 7 日內最新 `info_*.json` | 超過 7 日無任何快取 |
| 財報日期 | `earnings_registry.json`，per-symbol TTL 判斷 | 各股 cached_at 超過 30 天 |
| ETF / 大盤 | 無快取 | 每次執行都重新下載 |

- `--no-cache` 旗標：跳過所有快取，強制重下
- `clear_old_cache()`：執行前清除超過 7 日的 `.cache/` 檔案

### L1 硬篩條件（filter.py，兩段執行）

**第一段（Step 4 — 流動性）**
- 股價 > $5
- 30 日均量 > 500,000
- 市值 > 3 億（$300M）
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
