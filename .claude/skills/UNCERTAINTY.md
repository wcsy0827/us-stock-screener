# UNCERTAINTY — 審查後留存的未定案發現

日期：2026-07-13。三鏡頭審查（事實核查／教義一致性／零語境可用性）中無法在本 repo 內定案、或刻意決定不修的項目。標籤依交付規範：`unverified`（repo 內查不到直接證據）/ `user-must-provide`（需使用者權限或記憶）/ `accepted-as-is`（已知瑕疵，權衡後保留）。

## unverified

1. **GitHub Actions 歷史 log 引句**：`Cache hit for: screener-data-2026-07-02`、`[ranker] 解析成功，取得 0 筆結果` 等 CI log 原文，出處是 `plans/` 檔案的轉述，無法回查 GitHub Actions 歷史 log（保留期限已過或需線上查詢）。技能檔中這些引句應視為「plan 檔記載」而非一手證據。
2. **「2026-07-06 排查實際使用過 `gh run view --log`」**：debugging-playbook 與 ci-operations 的分診命令語法已驗證合理，但「當時排查確實用了這些命令」是 plan 檔敘事，無當時 session 記錄可對。命令本身的可用性不受影響。

## user-must-provide

3. **`DEEPSEEK_API_KEY` secret 的實際設定狀態**：技能檔寫「設在 repo Settings → Secrets and variables → Actions」是 CLAUDE.md 的記載；是否真的已設、是否輪替過，需 repo 管理權限確認。
4. **`.env.example` 的 `MIN_SCORE=70` 與 `TELEGRAM_*` 殘留欄位的意圖**：70 是刻意的個人偏好還是漏同步？TELEGRAM 是廢棄計畫還是待做功能？技能庫按「與程式碼不符的殘留」處理（config-and-flags 已標注落差），但正確答案在使用者記憶裡；若是刻意的，該技能段落應改寫。

## accepted-as-is

5. **description 三方重疊**：「報告日期停留前一日」同時命中 run-and-operate、debugging-playbook、ci-operations 三份的載入條件。審查認定內容互相一致且互相指路（debugging-playbook S3 明確導向 run-and-operate 時區表），誤載可自癒，不收斂——收斂反而增加漏載風險。
6. **行號與測試數量的時效性**：全部標記 2026-07-13 快照且各檔尾附再驗證指令，接受自然漂移，不做「無行號化」改寫（行號對弱模型的導航價值大於漂移成本）。
7. **validation-and-qa 的 fail-then-pass 標準**：CLAUDE.md 原文只要求「補測試 + pytest 通過」，fail-then-pass 是本技能庫加嚴的建議（檔內已明示兩者界線）。未來維護者若認為過嚴，可降級該半句而不影響其餘內容。
