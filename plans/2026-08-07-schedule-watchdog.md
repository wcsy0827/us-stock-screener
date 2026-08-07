# 排程監控機制（watchdog.yml）

日期：2026-08-07

## 背景（事故）

2026-08-06（週四）UTC 21:30 的 `daily-screener.yml` 排程完全沒有觸發——`gh run list --workflow daily-screener.yml` 查不到當天任何 `schedule` 事件的執行紀錄，workflow 狀態確認為 `active`，cron 表達式 `30 21 * * 1-5` 本身沒問題。診斷結論：GitHub Actions 的排程觸發器偶發性跳過（官方文件承認高負載時可能延遲或整個跳過，屬 GitHub 平台行為，repo 端配置無法根治）。

台灣時間 2026-08-07 08:01 有人手動觸發 `workflow_dispatch` 補跑成功，push 了 `report: 2026-08-06`（commit `bbe6438`）。但這次是使用者主動發現才補救，若當天沒人注意，報告會直接漏掉一天且沒有任何告警。

## 目標

排程漏跑時要有主動通知，不依賴使用者自己發現。

## 考慮過的方案

1. **在 `daily-screener.yml` 內部加檢查步驟**：捨棄。如果排程觸發本身沒發生，`daily-screener.yml` 整個 job 都不會啟動，內部加什麼檢查都沒用——問題發生在觸發層，不是執行層。
2. **依賴 GitHub Actions 預設的失敗通知信**：捨棄。這只在 job **有跑但失敗**時才會寄信，涵蓋不到「觸發器直接跳過、job 根本沒啟動」這個實際發生的失效模式。
3. **獨立的 watchdog workflow，排在主排程之後檢查產出是否新鮮**（採用）：新增 `.github/workflows/watchdog.yml`，排程訂在主排程（UTC 21:30）後 1.5 小時（UTC 23:00），檢查 `docs/data/last_run.json` 的 `market_date` 是否等於「最近一個平日」，不符就自動開 GitHub Issue。這個 workflow 有自己獨立的 cron 觸發，不依賴 `daily-screener.yml` 是否有跑，能涵蓋「觸發器跳過」這個實際發生過的失效模式。

## 通知方式

向使用者確認過：選擇 GitHub Issue（貼標籤 `screener-watchdog`），依賴 GitHub 帳號預設的 Issue 開單通知信，不需額外設定 SMTP secrets。

## 已知限制（未解決，刻意接受）

美股假日當天，`market_date` 正常會停留在前一個交易日（yfinance 沒有新數據可用，這是正確行為，不是 bug）。watchdog 的新鮮度判斷只用「Mon-Fri」簡單規則，不查美股假日曆，因此假日當天會誤報一次 Issue。

捨棄了查美股假日曆的方案——需要額外維護一份假日表或串接假日 API，換來的只是少開幾張可以直接手動關閉的 Issue，複雜度不成比例。使用者收到通知後確認是假日即可直接關閉，成本很低。

## 實作

- 新檔案：`.github/workflows/watchdog.yml`
- 邏輯：inline Python（stdlib `json`/`datetime`，無額外依賴）算出「最近一個平日」，與 `last_run.json` 的 `market_date` 比對；不符則用 `gh issue create` 開單，開單前用 `gh issue list --search` 檢查同一預期日期是否已有未關閉的 Issue，避免重複開單。
- 文件同步：`CLAUDE.md`（GitHub Actions 節）、`README.md`（GitHub Actions 自動化節 + 專案結構樹）已同步更新。
