---
name: debugging-playbook
description: 觀察到以下任一狀態時載入：報告輸出與預期不符（0 支新增、日期停留前一日、統計數字互相矛盾、止損等於買入區間下緣）；hotfix 後行為沒變；勝率/績效數字可疑；CI 某天的執行結果需要歸因。
---

# 除錯手冊：症狀 → 分診（全部來自真實事故）

事實時間戳：2026-07-13。通則：先分辨「數據殘缺」「快取汙染」「Prompt 措辭」「狀態機缺陷」四類根因——它們的症狀高度相似，但修法完全不同。

## S1｜報告 0 支新增，但 last_run.json 的 ai_count > 0 或 regime 為空字串

實例：2026-07-06。因果鏈：一支板塊 ETF 下載失敗 → `_analyze()` 對缺 Close 欄位拋 KeyError → 整個 market_context 退化 `{}` → regime 空字串 + AI 拿到空大盤背景回傳空結果 → `_enrich_fallback()` 佔位股被計入 ai_count。
分診：①`gh run view <id> --log` 找 `[pipeline] 警告：大盤數據抓取失敗`、`[ranker] 解析成功，取得 0 筆結果`、`AI 排序失敗，改用 L2 分數直接輸出`；②`[fetcher] 成功取得 N 支` 是否短少。
現狀：market DD-7（單 ETF 防呆）與 ranker DD-18（is_fallback 標記）已修。若重現同類症狀，優先懷疑**新的**單點失敗沿類似路徑擴散——先修「單點失敗不該拖垮全局」的韌性缺口。**偶發的單一 ticker 失敗**（重跑自癒）不必追下載端原因（該次 plan 明文範疇約束）；但若是**系統性失敗**（多 ticker 齊掛、連日重現、rate-limit 特徵），下載端原因就是主線，不適用此約束。
完成定義：能寫出完整因果鏈（哪個 ticker → 哪層防呆漏了 → 哪個統計被污染），且每個統計數字都能對回 log。

## S2｜合併了修正，重跑結果卻和修正前一模一樣

實例：2026-07-02（PR #46 於當日 UTC 05:53 合併，06:10 CI 重跑仍輸出舊止損；受影響的是 7/1 報告）。根因：`.cache/ranked_YYYYMMDD.json` key 只綁日期，撿到合併前的 AI 快取。
分診：log 找 `[ranker] 複用今日 AI 快取`——出現即石錘。本機重現：`--dry-run --yes --no-ai-cache` 看新行為是否出現。
陷阱：**先確認修正本身有沒有效，再懷疑修正寫錯**。當年排查順序正確：本機 no-ai-cache 驗證修正有效 → 才把矛頭轉向 CI 快取。反過來會浪費半天改一段沒問題的程式碼。
完成定義：指出讀到的快取檔案與其建立時間點。

## S3｜排程跑了但沒有新 commit / 報告日期停在前一交易日

兩種成因，先分流：
- **正常**：美股當日尚未收盤或剛收盤（UTC 20:00 前），market_date 本來就是前一日；盤中觸發另會有 `[fetcher] 偵測到 ... 尚未收盤，已捨棄殘缺K棒` log。對照 run-and-operate 的時區表，多半收工。
- **事故型**（2026-07-02 實例，已修）：同日早跑建立的 price 快取被晚間排程複用 → 產出與前次全等 → `git diff --staged --quiet` 靜默跳過 commit。CI 現為 `--no-cache`，此路徑理論上封死；若重現，第一件事查 workflow 的旗標是否被人動過。
分診：`gh run view <id> --log` 尾端找 `No changes to commit`；比對 log 內 market_date 與預期交易日。

## S4｜watchlist 裡止損 == 買入區間下緣

實例：CB/KHC/V/AJG/LIN 五支完全相等（2026-07-02）。根因類型：**Prompt 措辭只有方向詞、無明確百分比**，AI 照字面把止損設在錨點本身。ranker DD-15/19 已加「下方 2%」「−1×ATR」與「不得等於下緣」反制句。
分診：`data/watchlist.json` 逐條比對 `stop_loss` 與 `buy_zone` 下緣；若重現，先讀 `SYSTEM_PROMPT`（`src/ranker.py:594` 起）找哪條規則又出現無數字的方向詞。修法在 Prompt，**不在 Python 端 clamp**（DD-15 已否決）。
注意：已寫入 watchlist 的壞值不回頭清洗（欄位訊號日鎖定；`plans/2026-07-02-ci-ai-cache-staleness.md` 範圍外事項節明文），等自然結算。

## S5｜watch 條目大量死於「已追高」或到期，等不到買點

實例：2026-07-06 報告 4 支動能股死於已追高。根因是結構性的：L2 專挑強勢股，L3 買入區間卻錨在深回檔帶（EMA20~EMA10），越強越等不到。DD-19 已改 ATR 淺回檔帶。
分診：統計死因分佈（報告的留意清單）；看死掉條目的 `buy_zone` 距訊號日收盤價幾個百分點、幾倍 ATR。
陷阱：不要用「放寬已追高門檻」（`_eval_status()` 內 `price > buy_zone_upper*1.08` 的追高失效判定，見 specs/tracker.md）或「延長 watch 天數」治標——plan 檔明文否決過：「區間錨點不改，等再久還是等不到。」

## S6｜勝率/績效數字可疑地漂亮

根因類型：虧損交易在歸檔前被攔截消失（DD-17 實例：active 被 `_eval_status` 翻成 invalid 繞過結算）。
分診：`git log -p data/watchlist.json` 追某支虧損股的狀態軌跡——它最後一次出現時是什麼狀態？有沒有對應的 `performance_history.json` 條目？每一筆 active 的消失都必須有歸檔記錄，否則就是同類缺陷。
完成定義：任取三筆已消失的 active 條目，全部能在 performance_history 找到歸檔。

## S7｜某股票門檻價位一夜之間全部錯位 / 已進場部位被翻回 watch

根因類型：拆股，或 `signal_date_close` 錨定錯位誤判拆股（DD-3/DD-17）。
分診：查該股近日是否拆股；比對 watchlist 條目的 `signal_date_close` 與訊號日（`tracked_dates[0]`）真實收盤價——兩者差超過 ±1% 就會誤判 split_factor。

## S8｜Windows 本機執行就編碼炸掉 / emoji 亂碼

`$env:PYTHONUTF8=1` 或用 `.\run.ps1`。CI 的 workflow 已設 `PYTHONUTF8: '1'`。

再驗證：`gh run list --workflow daily-screener.yml --limit 5`（工具鏈可用性）＋ 讀 `docs/data/last_run.json` 對照最近一次執行
