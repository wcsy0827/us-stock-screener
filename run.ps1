# 美股 AI 選股系統啟動腳本
# 用法：.\run.ps1 [--dry-run] [--top N] [--min-score N]
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe main.py @args
