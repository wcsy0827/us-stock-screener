---
name: failure-archaeology
description: 觀察到以下任一狀態時載入：你的方案似曾相識（快取優化、依賴升級、加開關、後處理修正 AI 輸出、清理「死碼」）；想推翻或簡化某個看起來過度複雜的既有設計；需要知道某條路為什麼沒走。
---

# 失敗考古：死路、翻案與被否決的方案

事實時間戳：2026-07-13。來源：`plans/`（15 份）、git log（205 commits）、CLAUDE.md DD 摘要。**在此檔出現過的方案，重提前必須先讀對應 plan 檔的否決理由。**

## 一、真實事故（付過學費的）

| 事故 | 根因 | 教訓 | 出處 |
|---|---|---|---|
| CI 排程 Segfault（exit 139） | pandas-ta→numba→LLVM 在 Actions Ubuntu 初始化崩潰 | 指標全改純 pandas；pandas-ta 永久禁入 | CLAUDE.md CI 注意事項 |
| CI Segfault 第二波 | curl_cffi 0.15 與 LD_LIBRARY_PATH 衝突 | 鎖 `curl_cffi<0.15` + `yfinance<1.0`，上限不得移除 | 同上 |
| hotfix 合併後輸出不變（2026-07-02 上午，影響 7/1 報告） | Actions cache key 只綁日期，撿到合併前 AI 快取 | CI 加 `--no-ai-cache` | plans/2026-07-02-ci-ai-cache-staleness.md |
| 7/2 報告靜默未產出（2026-07-02 晚間） | 同一 cache 機制的 price 版本：早跑快取污染晚間排程 | 升級成 `--no-cache` + 移除 `.cache/` 的 actions/cache 步驟（pip 快取步驟保留）。**同一機制同一天連咬兩口才被根除**——修快取問題時要問「這個 key 還會以什麼形式再咬一次？」 | plans/2026-07-03-ci-price-cache-staleness.md |
| 虧損交易無聲消失 | E 步驟先 `_eval_status` 再結算，active 被翻 invalid 繞過歸檔 | active→invalid 轉換移除（DD-17）；勝率統計曾系統性虛高 | specs/tracker.md DD-17 |
| 真實成交未被追蹤 | 只認收盤價，盤中觸價回落不算進場 | DD-19 盤中觸價優先 | specs/tracker.md DD-19 |
| 大盤背景整組蒸發（2026-07-06） | 單一 ETF 缺 Close → KeyError 傳到函式層 try/except，好數據陪葬 | 防呆放在最小單元（`_analyze()` 回 `{}`），不是外層再包一層 | plans/2026-07-07-market-context-single-etf-resilience.md |
| 合併衝突吃掉報告索引（2026-06-28） | 報告索引（reports-index.json 與 index.html 條目）在分支合併時被誤清 | commit dba28ba 補回；合併涉及 data/docs 生成物時逐檔檢查 | git log |
| tooltip hover 失效 | `div` 塞進不允許的位置產生無效 HTML | a7bb69b：改 span；前端改動要實際開頁面看 | git log |

## 二、被推翻的設計（走過又退回來的路）

1. **RSI > 80 硬排除 → 軟過濾**（86a5a27，2026-06-27）：強勢股被 L2 直接驅逐，改為零分但不排除。教訓：排除條件比扣分條件危險一個量級。
2. **動能買入區間 EMA20~EMA10 深回檔帶（DD-12）→ ATR 淺回檔帶（DD-19）**：L2 挑的就是還沒回檔的強勢股，深回檔帶等於「越強越買不到」。中途還有一版「保留 EMA 帶為預設、只加極端強勢例外」**被使用者退回**——治標，大部分強勢股仍先撞預設規則。
3. **持倉上限 v1「事後擇優」→ v2「事前掛單名單制」（同日推翻，2026-07-10）**：v1 讓「使用者沒掛單的股票」進了績效帳本。使用者原話：「系統內雖然可以判斷，但我從當天的報告看不出來。」教訓：**模擬語意必須對齊使用者的實際操作時序**（事前掛單，不是盤中盯盤擇優）。v1 plan 檔保留、檔頭標註已修訂。
4. **`_INFO_HTML` 人工同步 → `sync_index()` + 整檔全等守門**（DD-6）：「叮囑人記得同步」的規則活不過三次改動，凡是純函數輸出就該機器守門。

## 三、被否決的方案目錄（含當時的合理化，重提=先讀否決理由）

| 被否決方案 | 當時聽起來的理由 | 否決理由 |
|---|---|---|
| 新增 `--no-price-cache` 旗標 | 「語意最精確」 | 違反不加 flag 原則；選 `--no-cache` |
| smart cache invalidation（比對 SPY last date） | 「只在數據過期時才重下，最省」 | 「預期最後交易日」在假日邊界複雜度爆炸 |
| Python 端強制下修 AI 的 stop_loss | 「AI 不可靠，程式保底最穩」 | AI 敘述與實存數字不一致，除錯更難；修 Prompt 才是修根因 |
| Python 端確定性計算 buy_zone | 同上 | 偏離「AI 給完整交易計畫」架構，DD-12/19 兩度確認 |
| pre-commit hook 自動再生成 index.html | 「自動化最徹底」 | `--no-verify` 即失守；CI pytest 已是強制防線，hook 是重複建設 |
| 測試偵測漂移時自動改寫 index.html | 「測試自癒多方便」 | 綠燈掩蓋「repo 內已部署檔案是舊的」，違反守門語意 |
| 盤中執行直接中斷報錯 | 「殘缺數據不如不跑」 | 打斷 CI 自動化；剪殘缺列讓行為對齊既有心智模型 |
| 盤中執行只印警告照跑 | 「至少讓使用者知道」 | 等於沒防：market_date 照樣誤標、AI 照樣吃殘缺數據 |
| 進場代理價 `min(今日開盤, buy_zone_upper)` | 「更貼近真實成交價」 | 經 skeptic/red-team/simplifier 抗辯否決：精度增益有限，開盤異常值污染 return_pct |
| tracker 用 `confidence==5` 猜 fallback | 「不用改 ranker 就能區分」 | 脆弱：任一端調整門檻/預設分數即誤判；改為顯式 `is_fallback` 欄位 |
| 調整 watch 天數 / 已追高門檻來解「等不到回檔」 | 「小改參數就好」 | 區間錨點不改，等再久還是等不到——參數調整治不了結構性錯位 |
| Turtle 式分批建倉 | 「機構實務更真實」 | 與單一 buy_zone 資料模型差距過大，需重構整條 tracker/publisher 鏈 |

## 使用規則

**觸發**：你的新方案與上表任一條相似。
**步驟**：讀對應 plan/spec 的否決段落 → 若情境確實已變（例如資料模型已重構），在新 plan 中明文引用舊否決並說明何以不再成立 → 走 spec-first 流程。
**完成定義**：不存在「不知情地重新發明被否決方案」；翻案有書面依據。

再驗證（PowerShell）：`ls plans/; git log --oneline -20`（考古目錄是否有新增條目該補進此表）
