# 評估：兩個 watch 天數優化提案

## Context

使用者提出兩個優化建議，字面用詞是「觀察期」，但兩個提案的天數（突破策略 5→3、反轉策略維持 10）與現有 `specs/tracker.md` DD-15 的 `_max_watch_days()` 機制完全對應——這不是新概念，而是 DD-15 既有的「策略差異化 watch 天數上限」機制的進一步細分（regime/VIX 感知）。

`_max_watch_days(entry)`（`src/tracker.py:456-458`）決定一檔股票在 `watch`（等待回落進買入區間）狀態下，超過幾個追蹤日未觸發進場就判定 `expired` 移出 watchlist。原本：
- 預設（動能/突破策略）：5 日（`_DEFAULT_WATCH_DAYS`）
- 反轉策略：10 日（`_WATCH_DAYS_BY_STRATEGY["反轉策略"]`）

兩個提案都是在「訊號當下的大盤環境」條件下，進一步收緊或維持這個 watch 上限，目的是讓風控更貼近「假突破在高波動整理市中特別多」「V 型反轉股若遲遲不進場可能不是錯殺而是真崩盤」這兩個市場觀察。

**關鍵發現**：`entry["entry_regime"]`（訊號當下 regime）與 `entry["vix_value"]`（訊號當下 VIX）在建立 watchlist 條目時就已經寫入（`src/tracker.py:668-670`，供既有績效分析使用），因此兩個優化**完全不需要改動 `pipeline.py` 或 `ranker.py` 的資料流**，只需要在 `_max_watch_days()` 內多讀這兩個既有欄位。這也符合現有設計慣例：`buy_zone`/`stop_loss`/`target` 等訊號特徵都是訊號當下鎖定（entry-time snapshot），不隨每日重新評估的當下 regime 變動。

## 探索過程

透過 Explore 子agent 確認：
1. `hold_period`（進場後持倉天數上限，觸發 FORCE_EXPIRED）由 AI 在 ranker.py prompt 中決定，與使用者提案的「觀察期」數字（5→3、10）不符。
2. 使用者提案的天數精確對應 `_WATCH_DAYS_BY_STRATEGY`（DD-15）：`_DEFAULT_WATCH_DAYS=5`（突破/動能策略）、`反轉策略=10`。確認提案是在描述 watch 階段（訊號建立後、尚未落入買入區間前的等待期），而非 active 階段的 hold_period。
3. 進一步確認 `entry_regime`、`vix_value` 兩欄位已於 `src/tracker.py:668-670` 在訊號建立當下寫入 watchlist 條目，供既有績效分析（`entry_regime` 也用於 `performance_history.json` 的 `signal_details`）使用，因此無需修改 pipeline 資料流即可在 `_max_watch_days()` 取用。

## 評估結論

兩個提案**邏輯合理、可行、且與現有架構高度契合**，採用，改動量小（僅 `tracker.py` 一個函式 + spec 文件）。

- 優化 1（CONSOLIDATION_VOLATILE + 突破策略 → 3 天）：與 DD-15 原本「進場信號具時效性」的推理完全一致，只是把「高波動整理市中假突破機率更高」這個更細緻的條件納入。
- 優化 2（PANIC_REVERSAL + VIX 分層）：DD-15 原本用「反轉策略底部確認需要更長時間」把反轉策略統一設為 10 天，但沒有考慮 VIX 極端值。VIX > 35 對應近乎流動性擠壓式的尖底，V 型反彈通常在數日內兌現；若 5 天仍未進場，繼續等待的風險確實升高（可能是真正的基本面黑天鵝）。VIX 25~30 維持 10 天不變，是保守且合理的邊界處理（不動既有行為，只在極端值時收緊）。

## 考慮過但捨棄的方案

- **每日重新評估的當下 regime**：改用 `market_context`（B/C 步驟傳入的今日 regime）而非訊號當下鎖定的 `entry_regime`。捨棄原因：會讓同一檔股票的 watch 上限在追蹤期間內隨每日大盤變化而波動，與 `buy_zone`/`stop_loss`/`target` 等訊號特徵鎖定的既有慣例（訊號建立當下即固定）不一致。
- **以 `date_added` 判斷新舊條目、僅對部署後新訊號生效**：需要額外判斷 `date_added` 是否早於某個切換日期，實作稍微複雜，且需要硬編碼一個切換日期常數。捨棄原因：`entry_regime`/`vix_value` 欄位本來就已存在於既有條目，套用新規則不需要遷移邏輯；且經與使用者確認，既有條目立即套用新規則的行為變化是可接受的。

## 已與使用者確認的決定

既有 `data/watchlist.json` 中已追蹤 1~2 天的 watch 條目，新規則上線後**立即套用**，不分新舊、不做版本判斷。已知後果：目前已追蹤 1~2 天、且符合「突破策略 + CONSOLIDATION_VOLATILE」或「反轉策略 + PANIC_REVERSAL + VIX>35」條件的既有條目，會比原本預期更早（甚至下次追蹤就）觸發 `expired`，使用者已知並接受此行為。

## 實作方案

### `src/tracker.py`

`_max_watch_days(entry)` 改為同時讀取 `entry["strategy"]`、`entry["entry_regime"]`、`entry["vix_value"]`：

```python
def _max_watch_days(entry: dict) -> int:
    """依策略與訊號當下大盤環境回傳 watch/invalid 天數上限（DD-15、DD-16）。"""
    strategy = entry.get("strategy", "")
    regime   = entry.get("entry_regime", "")
    vix      = entry.get("vix_value")

    if strategy == "突破策略" and regime == "CONSOLIDATION_VOLATILE":
        return 3  # 高波動整理市假突破風險升高，縮短觀察期（DD-16）

    if strategy == "反轉策略" and regime == "PANIC_REVERSAL" and vix is not None and vix > 35:
        return 5  # VIX 暴噴級尖底，V 型反彈應快速兌現，遲遲不進場視為真黑天鵝（DD-16）

    return _WATCH_DAYS_BY_STRATEGY.get(strategy, _DEFAULT_WATCH_DAYS)
```

### `specs/tracker.md`

新增 DD-16（緊接 DD-15 之後），並在 Acceptance Criteria 加入 3 條新驗證案例。

### 文件同步

- `CLAUDE.md` 十五大設計決策摘要新增一點（DD-16 濃縮版）
- 本文件即完整記錄，`specs/tracker.md` DD-16 結尾連結回本文件

## 驗證方式

1. 靜態確認 `_max_watch_days()` 對五組輸入的分支結果：
   - `{"strategy": "突破策略", "entry_regime": "CONSOLIDATION_VOLATILE"}` → 3
   - `{"strategy": "突破策略", "entry_regime": "BULL_TREND"}` → 5（不受影響）
   - `{"strategy": "反轉策略", "entry_regime": "PANIC_REVERSAL", "vix_value": 36}` → 5
   - `{"strategy": "反轉策略", "entry_regime": "PANIC_REVERSAL", "vix_value": 28}` → 10
   - `{"strategy": "反轉策略", "entry_regime": "PANIC_REVERSAL", "vix_value": None}` → 10（防呆）
2. 專案目前無 `tests/` 目錄，不補單元測試框架，僅靠上述靜態確認與 code review
3. 不需要跑完整 `python main.py --dry-run`（此改動不影響資料抓取或 AI 呼叫路徑，純粹是既有 watchlist 欄位的下游邏輯）
