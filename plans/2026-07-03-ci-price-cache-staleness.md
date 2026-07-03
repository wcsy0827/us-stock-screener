# CI Price Cache Staleness Fix

**Date**: 2026-07-03  
**Status**: Implemented

## 問題描述

7/2 報告未能產出 7/2 數據，排程 CI 仍輸出 7/1 報告，且未產生新的 commit。

## 根本原因

```
UTC 06:10 Jul 2（台灣下午 workflow_dispatch 手動觸發）
  → fetcher._today() = "20260702"（UTC 日期）
  → 下載 price data，美股尚未開盤，SPY last date = 2026-07-01
  → 儲存 .cache/price_20260702.pkl（含 7/1 數據）
  → GitHub Actions 儲存 cache key "screener-data-2026-07-02"

UTC 21:30 Jul 2（排程 CI 觸發，美股已收盤）
  → GitHub Actions RESTORE 舊的 screener-data-2026-07-02 快取
  → .cache/price_20260702.pkl 已存在
  → Python 以 --no-ai-cache 執行，price 快取仍被複用
  → SPY last date = 2026-07-01（舊數據！）
  → market_date = "2026-07-01"
  → 產出與已提交內容完全相同的 7/1 報告
  → git diff --staged --quiet → true → 無新 commit → 靜默失敗
```

## 影響

- GitHub Pages 未更新到 7/2 報告。
- 此問題與 AI cache 汙染（PR #47）為同一模式：GitHub Actions cache key 只綁日期，同日不同時段的兩次執行共享同一快取。

## 解決方案（已實施）

將 CI 的 `--no-ai-cache` 改為 `--no-cache`，確保 CI 每次執行都從 yfinance 重新下載 price data（同時也略過 info 與 AI 快取）。

**考慮過但捨棄的方案**：

| 方案 | 捨棄原因 |
|------|----------|
| `--no-price-cache`（新旗標）| 違反 CLAUDE.md「不新增 feature flag」原則 |
| smart cache invalidation（下載後比對 SPY last date） | 需判斷「預期最後交易日」，假日邊界情況複雜 |
| 移除 GHA price cache step | info cache 本身有價值（7 日 TTL），不需每次重取 |

選 `--no-cache` 因為：正常排程每日只跑一次，info 多下一次的代價可接受；邏輯最一致（與 `--no-ai-cache` 同原則，且一併解決兩個問題）。

## 修改清單

- `.github/workflows/daily-screener.yml`：`--no-ai-cache` → `--no-cache`，補充注釋說明原因
- `CLAUDE.md`：CI 注意事項與 GitHub Actions 時區說明段落同步更新

## 關聯

- 同類問題（AI cache）：`plans/2026-07-02-ci-ai-cache-staleness.md`
- 時區設計：CLAUDE.md 設計決策 12
