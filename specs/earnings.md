# Earnings — 財報日三層快取規格

## Purpose

管理個股財報日查詢，透過三層防禦架構在趨近零額外網路 I/O 的前提下，確保 L1 財報防禦牆能識別未來 N 天內有財報的股票。

## Behavior

### 三層防禦查詢流程

```
Tier 1（本地 registry）
  → 命中且未過期（< 30 天） → 直接回傳，0 次 I/O
  → 未命中或已過期 → 繼續 Tier 2

Tier 2（info_data 提取）
  → info_data[sym]["earnings_date"] 有值 → 解析後寫回 registry
  → 值為 null 但 key 存在 → 寫回 registry（tier=2），列入 Tier 3 候選
  → key 不存在 → 列入 Tier 3 候選

Tier 3（ticker.calendar 精準補抓）
  → 僅對 post_l1_symbols 中仍在 Tier 3 候選清單的個股觸發
  → 結果寫回 registry（tier=3），無論成功或失敗
```

### Registry 格式

```
.cache/earnings_registry.json
{
  "AAPL": {
    "next_earnings": "2026-07-29",
    "cached_at": "2026-06-28T12:00:00",
    "tier": 2
  },
  "NVDA": {
    "next_earnings": null,
    "cached_at": "2026-06-28T12:01:00",
    "tier": 3
  }
}
```

- **TTL**：30 天，不受 `clear_old_cache()` 7 天機制管轄，由 `earnings.py` 自行判斷過期
- **tier 欄位**：`2` = 來自 `.info`，`3` = 來自 `ticker.calendar`
- **next_earnings = null**：已查詢但無已知財報，同樣快取，避免重複請求
- **Tier 1 接受條件**：`tier == 3` 且 `< 30 天`，或有具體日期（`next_earnings` 非 null）且 `< 30 天`；`tier == 2` 且 null → 仍允許 Tier 3 升級

### earningsDate 來源

`fetcher.py` 的 `fetch_info()` 在 info_map 中新增 `earnings_date` 欄位，直接存入 `info.get("earningsDate")` 原始值（可能為 Unix timestamp、list of timestamps 或 None）。

Tier 2 的 `_parse_earnings_timestamp()` 負責將原始值轉為 `date` 物件：
- `int/float` → Unix timestamp → `datetime.fromtimestamp(..., utc).date()`
- `list/tuple` → 取第一元素（最早預估日）
- `str` → `date.fromisoformat(value[:10])`
- 其他/None → `None`

## Interface

```python
def fetch_earnings_dates(
    symbols: list[str],
    info_data: dict[str, dict],
    post_l1_symbols: list[str] | None = None,
    cache_path: str = EARNINGS_REGISTRY_FILE,
) -> dict[str, date | None]:
    """
    回傳 {symbol: next_earnings_date | None}。
    None = 無已知即將到來的財報（通過防禦牆）。
    post_l1_symbols 非 None 時觸發 Tier 3 補抓。
    """
```

常數：
- `EARNINGS_REGISTRY_FILE = ".cache/earnings_registry.json"`
- `REGISTRY_TTL_DAYS = 30`

## Design Decisions

### DD-E1: 三層架構，Tier 3 後置精準觸發

- **選擇**：Tier 3 (`ticker.calendar`) 僅對通過 L1 流動性篩選的個股觸發
- **原因**：503 支全量觸發 = 503 次額外 I/O，違反「零增量開銷」設計目標。L1 過濾後僅剩 ~200 支，Tier 2 命中多數，Tier 3 實際觸發數通常 < 50 次
- **捨棄**：全量觸發 `ticker.calendar`（高 I/O，破壞快取優化）

### DD-E2: Tier 2 null 允許 Tier 3 升級

- **選擇**：`.info` 存在 `earningsDate` key 但值 null 時，仍列入 Tier 3 候選
- **原因**：yfinance `.info` 的 `earningsDate` 欄位填充率約 60-70%，空值不代表「無財報」，可能只是 yfinance 未填充
- **捨棄**：Tier 2 null 直接快取為「無財報」（漏報率高，防禦牆失效）

### DD-E3: registry TTL 獨立設定為 30 天

- **選擇**：不跟隨 `info_*.json` 的 7 天清理機制
- **原因**：財報日期每季公布一次，7 天後仍有效。頻繁重新查詢 = 浪費 Tier 3 請求配額
- **捨棄**：7 天 TTL（與 info cache 對齊，但過短，每週觸發 Tier 3）

## Acceptance Criteria

- [ ] 第一次執行：registry 不存在 → Tier 2+3 填充 → 寫入 `.cache/earnings_registry.json`
- [ ] 第二次執行（同日）：所有 liq_filtered 個股命中 Tier 1 → Tier 3 不觸發（print 不出現 "Tier 3 補抓"）
- [ ] registry 某股 tier=2 且 next_earnings=null → 第二次執行（該股在 liq_filtered 中）→ Tier 3 觸發
- [ ] registry 某股 tier=3 且 next_earnings=null → 第二次執行 → Tier 1 命中，Tier 3 不觸發
- [ ] next_earnings 距今 <= 3 天 → `apply_earnings_filter()` 排除該股
- [ ] next_earnings 為 None → 視為「無已知財報」，通過防禦牆
