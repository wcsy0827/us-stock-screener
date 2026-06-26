"""HTML 報告發布模組：生成每日選股報告並推送至 GitHub Pages。"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

WEEKDAY_ZH = ["一", "二", "三", "四", "五", "六", "日"]

_ROOT = Path(__file__).parent.parent
_DOCS = _ROOT / "docs"
_REPORTS_DIR = _DOCS / "reports"
_DATA_DIR = _DOCS / "data"
_INDEX_JSON = _DATA_DIR / "reports-index.json"
_INDEX_HTML = _DOCS / "index.html"


# ── 工具函式 ─────────────────────────────────────────────────────────

def _days(entry: dict) -> int:
    return len(entry.get("tracked_dates", []))


def _get_daily_change(record: dict) -> tuple[float, str, str]:
    """回傳 (pct, sign_char, css_class)"""
    df = record.get("_price_data")
    if df is None or len(df) < 2:
        return 0.0, "▬", "flat"
    close = df["Close"].dropna()
    if len(close) < 2:
        return 0.0, "▬", "flat"
    prev = float(close.iloc[-2])
    now = float(close.iloc[-1])
    pct = (now - prev) / prev * 100 if prev else 0.0
    if pct >= 0:
        return abs(pct), "▲", "up"
    return abs(pct), "▼", "down"


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── CSS ──────────────────────────────────────────────────────────────

_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #0f172a; --card: #1e293b; --border: #334155;
  --text: #e2e8f0; --muted: #94a3b8; --subtle: #475569;
  --active: #22c55e; --watch: #eab308; --invalid: #ef4444;
  --expired: #6b7280; --new: #3b82f6; --reset: #a855f7;
}
body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 15px; line-height: 1.6; }
a { color: var(--new); text-decoration: none; }
a:hover { text-decoration: underline; }
.container { max-width: 860px; margin: 0 auto; padding: 28px 16px 48px; }

/* Header */
.page-header { margin-bottom: 28px; }
.page-header h1 { font-size: 1.5rem; font-weight: 700; margin-bottom: 4px; }
.page-header .date-line { font-size: 1rem; color: var(--muted); margin-bottom: 12px; }
.scan-bar { background: var(--card); border-radius: 8px; padding: 10px 14px; font-size: 0.85rem; color: var(--muted); border-left: 3px solid var(--new); display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.scan-bar .arrow { color: var(--border); }
.scan-bar strong { color: var(--text); }
.back-link { display: inline-flex; align-items: center; gap: 4px; margin-top: 12px; font-size: 0.85rem; color: var(--muted); }
.back-link:hover { color: var(--new); text-decoration: none; }

/* Section */
.section { margin-bottom: 24px; }
.section-title { font-size: 0.9rem; font-weight: 600; letter-spacing: 0.03em; padding-bottom: 8px; border-bottom: 1px solid var(--border); margin-bottom: 12px; display: flex; align-items: center; gap: 6px; }
.section-count { font-weight: 400; color: var(--muted); font-size: 0.82rem; margin-left: 2px; }

/* Tracking rows */
.track-item { background: var(--card); border-radius: 8px; padding: 11px 14px; margin-bottom: 7px; border-left: 3px solid var(--expired); display: grid; gap: 2px; }
.track-item.active  { border-left-color: var(--active); }
.track-item.watch   { border-left-color: var(--watch); }
.track-item.invalid { border-left-color: var(--invalid); }
.track-item.expired { border-left-color: var(--expired); opacity: 0.6; }
.track-header { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.track-symbol { font-weight: 700; font-size: 0.95rem; }
.track-name { color: var(--muted); font-size: 0.85rem; }
.strategy-tag { font-size: 0.72rem; padding: 1px 7px; border-radius: 4px; background: var(--border); color: var(--subtle); margin-left: auto; white-space: nowrap; }
.track-status { font-size: 0.82rem; color: var(--muted); }
.track-prices { font-size: 0.82rem; color: var(--text); margin-top: 2px; }
.track-prices .cur-price { color: var(--text); font-weight: 600; }

/* Stock cards */
.stock-card { background: var(--card); border-radius: 10px; border: 1px solid var(--border); padding: 16px; margin-bottom: 12px; transition: border-color 0.15s; }
.stock-card:hover { border-color: var(--new); }
.stock-card.reset-card:hover { border-color: var(--reset); }
.card-header { display: flex; align-items: flex-start; gap: 10px; margin-bottom: 10px; }
.card-rank { background: var(--border); color: var(--muted); border-radius: 50%; width: 26px; height: 26px; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; font-weight: 700; flex-shrink: 0; margin-top: 2px; }
.card-title { flex: 1; }
.card-symbol { font-size: 1.1rem; font-weight: 800; color: var(--new); }
.reset-card .card-symbol { color: var(--reset); }
.card-company { font-size: 0.88rem; color: var(--muted); }
.card-price { text-align: right; }
.card-price .price-val { font-size: 1rem; font-weight: 700; display: block; }
.card-price .price-chg { font-size: 0.8rem; }
.price-chg.up   { color: var(--active); }
.price-chg.down { color: var(--invalid); }
.price-chg.flat { color: var(--muted); }
.card-badges { display: flex; gap: 7px; flex-wrap: wrap; margin-bottom: 10px; }
.badge { font-size: 0.73rem; padding: 2px 8px; border-radius: 4px; background: var(--border); color: var(--muted); }
.reason-box { font-size: 0.875rem; line-height: 1.55; color: var(--text); padding: 10px 12px; background: #0f172a; border-radius: 7px; margin-bottom: 10px; }
.trade-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 6px; margin-bottom: 10px; }
.trade-cell { background: #0f172a; border-radius: 7px; padding: 8px 10px; }
.trade-label { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); margin-bottom: 2px; }
.trade-val { font-size: 0.9rem; font-weight: 700; }
.trade-val.buy  { color: var(--new); }
.trade-val.tgt  { color: var(--active); }
.trade-val.stop { color: var(--invalid); }
.risk-box { font-size: 0.8rem; line-height: 1.5; color: #fcd34d; background: rgba(234,179,8,0.08); border-radius: 7px; padding: 8px 12px; border-left: 2px solid var(--watch); }

/* Summary footer */
.summary-box { background: var(--card); border-radius: 10px; padding: 16px 20px; margin-top: 12px; border-top: 3px solid var(--new); }
.summary-box h2 { font-size: 0.9rem; margin-bottom: 12px; color: var(--muted); letter-spacing: 0.03em; }
.stat-row { display: flex; gap: 24px; flex-wrap: wrap; margin-bottom: 8px; }
.stat-row:last-child { margin-bottom: 0; }
.stat-group { display: flex; align-items: baseline; gap: 6px; }
.stat-num { font-size: 1.1rem; font-weight: 800; }
.stat-num.c-active  { color: var(--active); }
.stat-num.c-watch   { color: var(--watch); }
.stat-num.c-invalid { color: var(--invalid); }
.stat-num.c-new     { color: var(--new); }
.stat-num.c-reset   { color: var(--reset); }
.stat-num.c-removed { color: var(--expired); }
.stat-lbl { font-size: 0.8rem; color: var(--muted); }

/* Index page */
.index-hero { text-align: center; padding: 32px 0 20px; }
.index-hero h1 { font-size: 1.8rem; font-weight: 800; margin-bottom: 6px; }
.index-hero p { color: var(--muted); font-size: 0.9rem; }
.report-list { display: flex; flex-direction: column; gap: 8px; margin-top: 24px; }
.report-entry { background: var(--card); border-radius: 9px; padding: 14px 18px; border: 1px solid var(--border); display: flex; align-items: center; gap: 16px; text-decoration: none; color: var(--text); transition: border-color 0.15s; }
.report-entry:hover { border-color: var(--new); text-decoration: none; color: var(--text); }
.report-date { font-weight: 700; font-size: 0.95rem; min-width: 130px; }
.report-weekday { font-size: 0.8rem; color: var(--muted); margin-top: 1px; }
.report-chips { display: flex; gap: 7px; flex-wrap: wrap; margin-left: auto; }
.chip { font-size: 0.75rem; padding: 2px 9px; border-radius: 12px; font-weight: 600; }
.chip.active  { background: rgba(34,197,94,0.15);  color: var(--active); }
.chip.watch   { background: rgba(234,179,8,0.15);  color: var(--watch); }
.chip.invalid { background: rgba(239,68,68,0.12);  color: var(--invalid); }
.chip.new     { background: rgba(59,130,246,0.15); color: var(--new); }
.chip.reset   { background: rgba(168,85,247,0.12); color: var(--reset); }
.chip.neutral { background: var(--border); color: var(--muted); }
.arrow-icon { color: var(--border); font-size: 1rem; }
.empty-state { text-align: center; padding: 48px; color: var(--muted); }

@media (max-width: 600px) {
  .scan-bar { flex-direction: column; align-items: flex-start; gap: 2px; }
  .card-header { flex-wrap: wrap; }
  .card-price { text-align: left; }
  .report-entry { flex-wrap: wrap; }
  .report-chips { margin-left: 0; }
}
"""


# ── HTML 生成：每日報告 ───────────────────────────────────────────────

def _tracking_row(e: dict, status_cls: str) -> str:
    sym = _esc(e["symbol"])
    name = _esc(e.get("name", sym))
    strategy = _esc(e.get("strategy", "-"))
    days = _days(e)
    p = e.get("current_price")
    price_str = f'<span class="cur-price">${p:.2f}</span>｜' if p else ""
    bz = _esc(e.get("buy_zone", "-"))
    tgt = _esc(e.get("target", "-"))
    sl = _esc(e.get("stop_loss", "-"))

    if status_cls == "active":
        status_text = f"第 {days} 天 ── 已落入買入區間 ✅"
    elif status_cls == "watch":
        status_text = f"第 {days} 天（等待回落至買入區間）"
    elif status_cls == "invalid":
        reason = _esc(e.get("invalid_reason", ""))
        remaining = max(0, 5 - days)
        status_text = f"第 {days} 天 ── {reason}（剩 {remaining} 天自動移除）"
    else:  # expired
        status_text = f"已追蹤 {days} 天，今日移除"

    prices_html = ""
    if status_cls != "expired":
        prices_html = f'<div class="track-prices">{price_str}買入區間 {bz}｜目標 {tgt}｜止損 {sl}</div>'

    return f"""
<div class="track-item {status_cls}">
  <div class="track-header">
    <span class="track-symbol">{sym}</span>
    <span class="track-name">{name}</span>
    <span class="strategy-tag">{strategy}</span>
  </div>
  <div class="track-status">{status_text}</div>
  {prices_html}
</div>"""


def _stock_card(i: int, rec: dict, card_cls: str = "") -> str:
    pct, sign, chg_cls = _get_daily_change(rec)
    sym = _esc(rec["symbol"])
    name = _esc(rec.get("name", sym))
    price = rec.get("price", 0.0)
    score = rec.get("total_score", 0.0)
    conf = rec.get("confidence", 5)
    sector = _esc(rec.get("sector", "-"))
    reason = _esc(rec.get("reason", ""))
    risk = _esc(rec.get("risk", ""))
    bz = _esc(rec.get("buy_zone", "-"))
    tgt = _esc(rec.get("target", "-"))
    sl = _esc(rec.get("stop_loss", "-"))
    hold = _esc(rec.get("hold_period", "-"))
    strategy = _esc(rec.get("strategy", "-"))

    return f"""
<div class="stock-card {card_cls}">
  <div class="card-header">
    <div class="card-rank">{i}</div>
    <div class="card-title">
      <div class="card-symbol">{sym}</div>
      <div class="card-company">{name}</div>
    </div>
    <div class="card-price">
      <span class="price-val">${price:.2f}</span>
      <span class="price-chg {chg_cls}">{sign}{pct:.2f}%</span>
    </div>
  </div>
  <div class="card-badges">
    <span class="badge">📊 評分 {score:.0f}</span>
    <span class="badge">信心 {conf}/10</span>
    <span class="badge">🏭 {sector}</span>
    <span class="badge">📋 {strategy}</span>
  </div>
  <div class="reason-box">🤖 {reason}</div>
  <div class="trade-grid">
    <div class="trade-cell">
      <div class="trade-label">買入區間</div>
      <div class="trade-val buy">{bz}</div>
    </div>
    <div class="trade-cell">
      <div class="trade-label">目標價</div>
      <div class="trade-val tgt">{tgt}</div>
    </div>
    <div class="trade-cell">
      <div class="trade-label">止損</div>
      <div class="trade-val stop">{sl}</div>
    </div>
    <div class="trade-cell">
      <div class="trade-label">持有週期</div>
      <div class="trade-val">{hold}</div>
    </div>
  </div>
  <div class="risk-box">⚠️ {risk}</div>
</div>"""


def _section_html(emoji: str, title: str, items: list[str], note: str = "") -> str:
    n = len(items)
    if n == 0:
        return ""
    note_str = f"，{note}" if note else ""
    content = "\n".join(items)
    return f"""
<div class="section">
  <div class="section-title">
    {emoji} {_esc(title)}<span class="section-count">（{n}支{note_str}）</span>
  </div>
  {content}
</div>"""


def _build_daily_report(categories: dict, stats: dict, date_str: str, weekday: str) -> str:
    total = stats.get("total", 0)
    l1 = stats.get("l1_count", 0)
    l2 = stats.get("l2_count", 0)
    ai = stats.get("ai_count", 0)

    active  = categories.get("active", [])
    watch   = categories.get("watch", [])
    invalid = categories.get("invalid", [])
    expired = categories.get("expired", [])
    new     = categories.get("new", [])
    reset   = categories.get("reset", [])

    sections = ""

    if active:
        rows = [_tracking_row(e, "active") for e in active]
        sections += _section_html("✅", "有效追蹤清單", rows)

    if watch:
        rows = [_tracking_row(e, "watch") for e in watch]
        sections += _section_html("🟡", "留意清單", rows)

    if invalid:
        rows = [_tracking_row(e, "invalid") for e in invalid]
        sections += _section_html("❌", "失效訊號", rows, "仍在追蹤期內")

    if expired:
        rows = [_tracking_row(e, "expired") for e in expired]
        sections += _section_html("🗑", "今日移除", rows)

    if new:
        cards = [_stock_card(i + 1, rec) for i, rec in enumerate(new)]
        sections += _section_html("🆕", "今日新進觀察名單", cards)

    if reset:
        cards = [_stock_card(i + 1, rec, "reset-card") for i, rec in enumerate(reset)]
        sections += _section_html("🔄", "重新入選，重置追蹤", cards)

    if not sections:
        sections = '<p style="color:var(--muted);padding:24px 0;">今日無資料</p>'

    na = len(active)
    nw = len(watch)
    ni = len(invalid)
    nn = len(new)
    nr = len(reset)
    ne = len(expired)

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>美股 AI 選股 {date_str}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="container">
  <div class="page-header">
    <h1>📊 美股 AI 選股報告</h1>
    <div class="date-line">📅 {date_str}（{weekday}）</div>
    <div class="scan-bar">
      掃描 <strong>S&amp;P500 {total}支</strong>
      <span class="arrow">→</span> L1 <strong>{l1}支</strong>
      <span class="arrow">→</span> L2 <strong>{l2}支</strong>
      <span class="arrow">→</span> AI精選 <strong>{ai}支</strong>
    </div>
    <a class="back-link" href="../index.html">← 返回首頁</a>
  </div>

  {sections}

  <div class="summary-box">
    <h2>📈 今日統計</h2>
    <div class="stat-row">
      <div class="stat-group"><span class="stat-num c-active">{na}</span><span class="stat-lbl">支有效</span></div>
      <div class="stat-group"><span class="stat-num c-watch">{nw}</span><span class="stat-lbl">支留意</span></div>
      <div class="stat-group"><span class="stat-num c-invalid">{ni}</span><span class="stat-lbl">支失效</span></div>
    </div>
    <div class="stat-row">
      <div class="stat-group"><span class="stat-num c-new">{nn}</span><span class="stat-lbl">支新增</span></div>
      <div class="stat-group"><span class="stat-num c-reset">{nr}</span><span class="stat-lbl">支重新入選</span></div>
      <div class="stat-group"><span class="stat-num c-removed">{ne}</span><span class="stat-lbl">支移除</span></div>
    </div>
  </div>
</div>
</body>
</html>"""


# ── HTML 生成：首頁索引 ───────────────────────────────────────────────

def _chip(count: int, label: str, cls: str) -> str:
    if count == 0:
        return ""
    return f'<span class="chip {cls}">{count} {label}</span>'


def _build_index(report_index: list[dict]) -> str:
    entries_html = ""
    for entry in sorted(report_index, key=lambda x: x["date"], reverse=True):
        d = entry["date"]
        wd = entry.get("weekday", "")
        chips = (
            _chip(entry.get("active", 0), "有效", "active") +
            _chip(entry.get("watch", 0), "留意", "watch") +
            _chip(entry.get("invalid", 0), "失效", "invalid") +
            _chip(entry.get("new", 0), "新增", "new") +
            _chip(entry.get("reset", 0), "重置", "reset")
        )
        if not chips:
            chips = '<span class="chip neutral">無追蹤</span>'
        entries_html += f"""
<a class="report-entry" href="reports/{d}.html">
  <div>
    <div class="report-date">{d}</div>
    <div class="report-weekday">（{wd}）</div>
  </div>
  <div class="report-chips">{chips}</div>
  <span class="arrow-icon">›</span>
</a>"""

    if not entries_html:
        entries_html = '<div class="empty-state">尚無報告，請先執行選股系統</div>'

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>美股 AI 選股系統</title>
<style>{_CSS}</style>
</head>
<body>
<div class="container">
  <div class="index-hero">
    <h1>📈 美股 AI 選股系統</h1>
    <p>每日選股報告 · 訊號追蹤 · S&amp;P 500</p>
  </div>
  <div class="report-list">
    {entries_html}
  </div>
</div>
</body>
</html>"""


# ── 索引 JSON I/O ────────────────────────────────────────────────────

def _load_report_index() -> list[dict]:
    if not _INDEX_JSON.exists():
        return []
    with open(_INDEX_JSON, encoding="utf-8") as f:
        return json.load(f)


def _save_report_index(index: list[dict]) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_INDEX_JSON, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


# ── git push ─────────────────────────────────────────────────────────

def _git_push(date_str: str) -> None:
    cmds = [
        ["git", "add", "docs/"],
        ["git", "commit", "-m", f"report: {date_str}"],
        ["git", "push"],
    ]
    cwd = str(_ROOT)
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
        if result.returncode != 0:
            stderr = result.stderr.strip()
            # "nothing to commit" is not an error
            if "nothing to commit" in stderr or "nothing to commit" in result.stdout:
                print(f"[publisher] git commit: 無變更，略過")
                return
            print(f"[publisher] git 錯誤（{' '.join(cmd)}）：{stderr}")
            raise RuntimeError(f"git 指令失敗：{' '.join(cmd)}")
    print(f"[publisher] 已推送至 GitHub")


def _check_git_remote() -> bool:
    result = subprocess.run(
        ["git", "remote"], capture_output=True, text=True, cwd=str(_ROOT)
    )
    return bool(result.stdout.strip())


# ── 主函式 ──────────────────────────────────────────────────────────

def publish(categories: dict, stats: dict, dry_run: bool = False) -> None:
    """
    生成每日 HTML 報告 + 更新首頁索引，並 git push（dry_run 時略過 push）。
    """
    dt: datetime = stats.get("date", datetime.now())
    weekday = WEEKDAY_ZH[dt.weekday()]
    date_str = dt.strftime("%Y-%m-%d")

    # 建立目錄
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # 生成每日報告
    report_html = _build_daily_report(categories, stats, date_str, weekday)
    report_path = _REPORTS_DIR / f"{date_str}.html"
    report_path.write_text(report_html, encoding="utf-8")
    print(f"[publisher] 報告已生成：{report_path}")

    # 更新索引 JSON
    index = _load_report_index()
    existing_dates = {e["date"] for e in index}
    entry = {
        "date":    date_str,
        "weekday": weekday,
        "active":  len(categories.get("active", [])),
        "watch":   len(categories.get("watch", [])),
        "invalid": len(categories.get("invalid", [])),
        "new":     len(categories.get("new", [])),
        "reset":   len(categories.get("reset", [])),
        "removed": len(categories.get("expired", [])),
    }
    if date_str in existing_dates:
        index = [entry if e["date"] == date_str else e for e in index]
    else:
        index.append(entry)
    _save_report_index(index)

    # 生成首頁
    index_html = _build_index(index)
    _INDEX_HTML.write_text(index_html, encoding="utf-8")
    print(f"[publisher] 首頁已更新：{_INDEX_HTML}")

    if dry_run:
        print(f"[publisher] Dry-run 模式，略過 git push")
        print(f"[publisher] 請用瀏覽器開啟：{report_path}")
        return

    if not _check_git_remote():
        print("[publisher] ⚠️  尚未設定 git remote，略過 push。請先執行：")
        print("  git remote add origin https://github.com/<user>/<repo>.git")
        return

    _git_push(date_str)
