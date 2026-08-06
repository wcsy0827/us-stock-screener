"""HTML 報告發布模組：生成每日選股報告並推送至 GitHub Pages。"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

from tracker import (
    _max_watch_days,
    _parse_hold_period,
    MAX_ACTIVE_POSITIONS,
    TRAILING_ACTIVATION_PCT,
    TRAILING_RETRACE_PCT,
    EXPIRY_EXTENSION_MAX_DAYS,
    EXPIRY_TRAIL_RETRACE_PCT,
)

WEEKDAY_ZH = ["一", "二", "三", "四", "五", "六", "日"]

_ROOT = Path(__file__).parent.parent
_DOCS = _ROOT / "docs"
_REPORTS_DIR = _DOCS / "reports"
_DATA_DIR = _DOCS / "data"
_INDEX_JSON = _DATA_DIR / "reports-index.json"
_LAST_RUN_JSON = _DATA_DIR / "last_run.json"
_INDEX_HTML = _DOCS / "index.html"
_PERF_PATH = _ROOT / "data" / "performance_history.json"


# ── 歷史績效統計 ─────────────────────────────────────────────────────

def _load_performance_stats() -> dict:
    """讀取 performance_history.json 計算績效統計。冷啟動安全：任何異常均回傳預設零值。"""
    default: dict = {"total": 0, "win_rate": 0.0, "avg_return": 0.0, "by_strategy": {}}
    if not _PERF_PATH.exists():
        return default
    try:
        with open(_PERF_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return default

    records = [
        r for r in data.get("history_records", [])
        if r.get("actual_outcome", {}).get("exit_reason") in (
            "CLOSED_PROFIT", "CLOSED_LOSS", "CLOSED_TRAILING_STOP", "FORCE_EXPIRED"
        )
    ]
    if not records:
        return default

    total = len(records)
    wins = sum(1 for r in records if r.get("performance_metrics", {}).get("is_win"))
    returns = [
        r["performance_metrics"]["return_pct"]
        for r in records
        if r.get("performance_metrics", {}).get("return_pct") is not None
    ]
    win_rate = round(wins / total * 100, 1) if total > 0 else 0.0
    avg_return = round(sum(returns) / len(returns), 2) if returns else 0.0

    by_strategy: dict[str, dict] = {}
    for r in records:
        strat = r.get("signal_details", {}).get("assigned_strategy", "其他") or "其他"
        if strat not in by_strategy:
            by_strategy[strat] = {"total": 0, "wins": 0}
        by_strategy[strat]["total"] += 1
        if r.get("performance_metrics", {}).get("is_win"):
            by_strategy[strat]["wins"] += 1

    strat_rates = {
        k: round(v["wins"] / v["total"] * 100, 1) if v["total"] > 0 else 0.0
        for k, v in by_strategy.items()
    }
    return {"total": total, "win_rate": win_rate, "avg_return": avg_return, "by_strategy": strat_rates}


def _build_performance_section(perf: dict) -> str:
    """生成歷史績效摘要 HTML。total == 0 時回傳空字串（冷啟動不渲染）。"""
    if perf.get("total", 0) == 0:
        return ""

    total = perf["total"]
    win_rate = perf["win_rate"]
    avg_return = perf["avg_return"]
    by_strategy = perf.get("by_strategy", {})

    avg_cls = "c-active" if avg_return >= 0 else "c-invalid"
    avg_sign = f"+{avg_return:.2f}%" if avg_return >= 0 else f"{avg_return:.2f}%"

    strat_cells = ""
    for strat, rate in by_strategy.items():
        strat_cells += (
            f'<div class="stat-group">'
            f'<span class="stat-num" style="font-size:0.95rem">{rate:.1f}%</span>'
            f'<span class="stat-lbl">{_esc(strat)}勝率</span>'
            f"</div>"
        )

    return f"""
<div class="summary-box" style="margin-bottom:20px;border-top-color:#a855f7">
  <h2>📊 歷史選股績效（累計 {total} 筆結算）</h2>
  <div class="stat-row">
    <div class="stat-group">
      <span class="stat-num c-new">{win_rate:.1f}%</span>
      <span class="stat-lbl">整體勝率</span>
    </div>
    <div class="stat-group">
      <span class="stat-num {avg_cls}">{_esc(avg_sign)}</span>
      <span class="stat-lbl">平均回報</span>
    </div>
    <div class="stat-group">
      <span class="stat-num" style="color:var(--muted)">{total}</span>
      <span class="stat-lbl">筆結算</span>
    </div>
  </div>
  {f'<div class="stat-row">{strat_cells}</div>' if strat_cells else ""}
</div>"""


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
.strategy-tag { font-size: 0.72rem; padding: 1px 7px; border-radius: 4px; background: var(--border); color: var(--text); margin-left: auto; white-space: nowrap; }
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
.detail-box { font-size: 0.8rem; line-height: 1.5; color: #94a3b8; background: rgba(148,163,184,0.06); border-radius: 7px; padding: 8px 12px; border-left: 2px solid #475569; margin-top: 6px; }

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
.doc-link-row { text-align: center; margin-bottom: 20px; }
.doc-link { display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px; border: 1px solid var(--border); border-radius: 8px; color: var(--text); font-size: 0.85rem; }
.doc-link:hover { border-color: var(--new); color: var(--new); text-decoration: none; }

/* Market Regime Dashboard */
.market-dashboard { border-radius: 10px; padding: 14px 18px; margin-bottom: 20px; border: 1px solid var(--border); }
.market-dashboard.bull          { border-left: 4px solid var(--active);  background: rgba(34,197,94,0.07); }
.market-dashboard.consolidation { border-left: 4px solid var(--watch);   background: rgba(234,179,8,0.07); }
.market-dashboard.panic         { border-left: 4px solid #f97316;        background: rgba(249,115,22,0.07); }
.market-dashboard.bear          { border-left: 4px solid var(--invalid); background: rgba(239,68,68,0.1); }
.regime-header { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.regime-name { font-size: 1rem; font-weight: 700; }
.regime-strategy { font-size: 0.82rem; color: var(--muted); margin-left: auto; }
.regime-metrics { display: flex; gap: 20px; flex-wrap: wrap; }
.regime-metric { min-width: 80px; }
.regime-metric-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); margin-bottom: 2px; }
.regime-metric-val { font-size: 1rem; font-weight: 700; }
.regime-metric-sub { font-size: 0.72rem; color: var(--muted); margin-top: 1px; }
.spy-above { color: var(--active); }
.spy-below { color: var(--invalid); }
.defense-banner { background: rgba(239,68,68,0.08); border: 1px solid rgba(239,68,68,0.3); border-radius: 10px; padding: 20px 24px; text-align: center; margin-bottom: 16px; }
.defense-banner .defense-title { font-size: 1rem; font-weight: 700; color: var(--invalid); margin-bottom: 6px; }
.defense-banner .defense-desc { font-size: 0.85rem; color: var(--muted); line-height: 1.7; }

/* System info (index page) */
details { margin-bottom: 20px; }
summary { cursor: pointer; list-style: none; display: flex; align-items: center; gap: 8px; padding: 12px 16px; background: var(--card); border-radius: 9px; border: 1px solid var(--border); font-weight: 600; font-size: 0.9rem; user-select: none; }
summary::-webkit-details-marker { display: none; }
details summary::before { content: "▶"; font-size: 0.65rem; color: var(--muted); transition: transform 0.2s; flex-shrink: 0; }
details[open] summary::before { transform: rotate(90deg); }
.info-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; padding-top: 12px; }
.info-card { background: var(--card); border-radius: 9px; border: 1px solid var(--border); padding: 14px 16px; }
.info-card h3 { font-size: 0.78rem; font-weight: 700; margin-bottom: 10px; color: var(--muted); letter-spacing: 0.06em; text-transform: uppercase; }
.info-table { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
.info-table th { text-align: left; padding: 4px 8px; color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--border); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; }
.info-table td { padding: 5px 8px; border-bottom: 1px solid rgba(51,65,85,0.4); line-height: 1.5; vertical-align: top; }
.info-table td:first-child { white-space: nowrap; }
.info-table tr:last-child td { border-bottom: none; }
.pipe-flow { display: flex; flex-direction: column; gap: 3px; }
.pipe-step { display: flex; align-items: flex-start; gap: 8px; font-size: 0.8rem; line-height: 1.5; }
.pipe-badge { background: var(--border); color: var(--muted); border-radius: 4px; padding: 1px 7px; font-size: 0.7rem; font-weight: 700; flex-shrink: 0; margin-top: 2px; }
.pipe-arrow { color: var(--subtle); font-size: 0.75rem; padding-left: 16px; }
.report-section-title { font-size: 0.82rem; font-weight: 600; color: var(--muted); letter-spacing: 0.05em; text-transform: uppercase; margin-bottom: 10px; }

.tip-wrap { position: relative; display: inline-flex; align-items: center; gap: 5px; }
.tip-icon { display: inline-flex; align-items: center; justify-content: center; width: 15px; height: 15px; border-radius: 50%; background: var(--border); color: var(--muted); font-size: 0.68rem; font-weight: 700; cursor: help; flex-shrink: 0; }
.tip-box { display: none; position: absolute; right: 0; top: calc(100% + 6px); background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; font-size: 0.78rem; line-height: 1.65; color: var(--text); font-weight: 400; width: 230px; z-index: 20; box-shadow: 0 4px 16px rgba(0,0,0,0.5); white-space: normal; text-align: left; }
.tip-wrap:hover .tip-box { display: block; }

/* Data freshness indicator */
.date-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 4px; }
.date-label { font-size: 0.8rem; color: var(--muted); }
.date-val { font-size: 1rem; color: var(--text); font-weight: 600; }
.freshness-badge { display: inline-flex; align-items: center; gap: 4px; font-size: 0.75rem; padding: 2px 9px; border-radius: 10px; font-weight: 600; cursor: pointer; }
.freshness-ok    { background: rgba(34,197,94,0.15);  color: var(--active); }
.freshness-stale { background: rgba(234,179,8,0.12);  color: var(--watch); }
.tz-note { font-size: 0.8rem; color: var(--muted); margin-top: 6px; min-height: 1.3em; }
.run-info { font-size: 0.75rem; color: var(--muted); margin-bottom: 12px; padding: 6px 10px; background: rgba(255,255,255,0.04); border-radius: 6px; display: none; line-height: 1.8; }
.run-info.visible { display: block; }

@media (max-width: 600px) {
  .scan-bar { flex-direction: column; align-items: flex-start; gap: 2px; }
  .card-header { flex-wrap: wrap; }
  .card-price { text-align: left; }
  .report-entry { flex-wrap: wrap; }
  .report-chips { margin-left: 0; }
  .regime-strategy { margin-left: 0; }
  .tip-box { right: auto; left: 0; }
  .info-grid { grid-template-columns: 1fr; }
}
"""


# ── HTML 生成：大盤儀表板 ────────────────────────────────────────────

def _build_market_dashboard(market_context: dict) -> str:
    """生成大盤儀表板 HTML 區塊，顯示市場廣度、Regime 與主推策略。"""
    if not market_context or "regime" not in market_context:
        return ""

    regime = market_context.get("regime", "")
    breadth = market_context.get("market_breadth_pct")
    primary = market_context.get("primary_strategy", "")
    vix_val = market_context.get("vix", {}).get("value")
    vix_label = market_context.get("vix", {}).get("label", "")
    spy_above_ema20 = market_context.get("sp500", {}).get("above_ema20")

    _REGIME_CONFIG = {
        "BULL_TREND":              ("bull",          "📈 強勢牛市"),
        "CONSOLIDATION":           ("consolidation", "⚖️ 震盪整理"),
        "CONSOLIDATION_VOLATILE":  ("consolidation", "⚡ 高波動整理"),
        "PANIC_REVERSAL":          ("panic",         "🔥 恐慌超跌"),
        "BEAR_DISTRIBUTION":       ("bear",          "🐻 陰跌熊市"),
    }
    cls, name = _REGIME_CONFIG.get(regime, ("", _esc(regime)))

    breadth_str = f"{breadth:.1f}%" if breadth is not None else "-"
    vix_str = f"{vix_val:.1f}" if vix_val is not None else "-"
    vix_sublabel = f'<div class="regime-metric-sub">{_esc(vix_label)}</div>' if vix_label else ""
    if spy_above_ema20 is True:
        spy_str = "✅ EMA20 之上"
        spy_cls = "spy-above"
    elif spy_above_ema20 is False:
        spy_str = "⚠️ EMA20 之下"
        spy_cls = "spy-below"
    else:
        spy_str = "-"
        spy_cls = ""
    _REGIME_TIPS = {
        "BULL_TREND":             "條件：市場廣度 ≥ 60% 且 VIX &lt; 20<br>選強勢領頭羊、均線多頭排列標的<br>買入區間錨定收盤下方 0.25～1×ATR 淺回檔帶",
        "CONSOLIDATION":          "條件：市場廣度 35～60% 且 VIX &lt; 20<br>帶量突破壓力位，訊號確認後積極進場",
        "CONSOLIDATION_VOLATILE": "條件：市場廣度 35～60% 且 VIX ≥ 20（高波動整理）<br>L2 門檻提高至 65 分；要求更強確認訊號，不明確一律跳過",
        "PANIC_REVERSAL":         "條件：市場廣度 &lt; 35% 且 VIX ≥ 25<br>找超賣底背離標的，嚴設止損",
        "BEAR_DISTRIBUTION":      "條件：市場廣度 &lt; 35% 且 VIX &lt; 25<br>全面防禦，不輸出任何買入建議",
    }
    tip_content = _REGIME_TIPS.get(regime, "")
    if primary and tip_content:
        strategy_html = (
            f'<span class="tip-wrap">'
            f'主推：{_esc(primary)}'
            f'<span class="tip-icon">?</span>'
            f'<span class="tip-box">{tip_content}</span>'
            f'</span>'
        )
    else:
        strategy_html = "⛔ 全面防禦，無買入建議"

    return f"""
<div class="market-dashboard {cls}">
  <div class="regime-header">
    <span class="regime-name">{_esc(name)}</span>
    <span class="regime-strategy">{strategy_html}</span>
  </div>
  <div class="regime-metrics">
    <div class="regime-metric">
      <div class="regime-metric-label">市場廣度</div>
      <div class="regime-metric-val">{_esc(breadth_str)}</div>
    </div>
    <div class="regime-metric">
      <div class="regime-metric-label">VIX</div>
      <div class="regime-metric-val">{_esc(vix_str)}</div>
      {vix_sublabel}
    </div>
    <div class="regime-metric">
      <div class="regime-metric-label">SPY 位置</div>
      <div class="regime-metric-val {spy_cls}">{_esc(spy_str)}</div>
    </div>
  </div>
</div>"""


# ── HTML 生成：每日報告 ───────────────────────────────────────────────

def _tracking_row(e: dict, status_cls: str) -> str:
    sym = _esc(e["symbol"])
    name = _esc(e.get("name", sym))
    strategy_raw = e.get("strategy", "-")
    strategy = _esc(strategy_raw)
    days = _days(e)
    p = e.get("current_price")
    price_str = f'<span class="cur-price">${p:.2f}</span>｜' if p else ""
    bz = _esc(e.get("buy_zone", "-"))
    tgt = _esc(e.get("target", "-"))
    sl = _esc(e.get("stop_loss", "-"))

    if status_cls == "active":
        active_days = e.get("active_days", 0)
        try:
            hold_limit = int(str(e.get("hold_period", "10")).strip())
        except (ValueError, TypeError):
            hold_limit = 10
        entry_p = e.get("active_entry_price")
        pnl_html = ""
        if entry_p and p:
            pnl = (p - entry_p) / entry_p * 100
            pnl_cls = "c-active" if pnl >= 0 else "c-invalid"
            sign = "+" if pnl >= 0 else ""
            pnl_html = f' ｜ <span class="{pnl_cls}" style="font-weight:700">{sign}{pnl:.2f}%</span>'
        if active_days > hold_limit:
            # 到期趨勢延伸（tracker DD-21）：收盤仍貼近峰值，延長持倉而非硬砍
            extended_days = active_days - hold_limit
            status_text = (
                f"持倉 {active_days} 天（已到期延長 "
                f"第 {extended_days}/{EXPIRY_EXTENSION_MAX_DAYS} 天）✅{pnl_html}"
            )
        else:
            status_text = f"持倉 {active_days} / {hold_limit} 天 ✅{pnl_html}"
        entry_str = f'進場 ${entry_p:.2f}｜' if entry_p else ""

        # 動態止損：顯示系統實際用於結算的 effective_stop_loss（保本鎖定後會上移至
        # buy_zone_upper，見 tracker.py DD-12/13），而非 AI 原始 stop_loss，
        # 避免使用者手動跟單時止損與系統結算脫節
        effective_sl = e.get("effective_stop_loss")
        if effective_sl is not None:
            lock_tag = " 🔒保本" if e.get("is_breakeven_locked") else ""
            sl_display = f"${effective_sl:.2f}{lock_tag}"
        else:
            sl_display = sl

        # 移動停利觸發線：峰值浮盈達門檻後才「武裝」，顯示回撤觸價供使用者對照（DD-13）
        trailing_html = ""
        if strategy_raw != "反轉策略" and entry_p and entry_p > 0:
            highest = e.get("highest_close_since_active") or entry_p
            if (highest - entry_p) / entry_p >= TRAILING_ACTIVATION_PCT:
                trigger = highest * (1 - TRAILING_RETRACE_PCT)
                trailing_html = f"｜移動停利線 ${trigger:.2f}"

        # 到期延伸停利線（tracker DD-21）：延長期間收盤自峰值回撤達此線即出場
        extension_html = ""
        if active_days > hold_limit:
            highest_ext = e.get("highest_close_since_active")
            if highest_ext:
                ext_trigger = highest_ext * (1 - EXPIRY_TRAIL_RETRACE_PCT)
                extension_html = f"｜延長停利線 ${ext_trigger:.2f}"

        prices_html = (
            f'<div class="track-prices">{price_str}{entry_str}目標 {tgt}'
            f"｜止損 {sl_display}{trailing_html}{extension_html}</div>"
        )
    elif status_cls == "watch":
        remaining = max(0, _max_watch_days(e) - days)
        if e.get("slot_blocked_today"):
            # 未在掛單名單被擋（tracker DD-20 v2 / publisher DD-8）：讓使用者知道今日其實觸價過
            status_text = (
                f"第 {days} 天（今日觸價但未在掛單名單，未進場；"
                f"剩 {remaining} 天自動移除）"
            )
        else:
            status_text = f"第 {days} 天（等待回落至買入區間，剩 {remaining} 天自動移除）"
        prices_html = (
            f'<div class="track-prices">{price_str}買入區間 {bz}｜目標 {tgt}｜止損 {sl}'
            f"｜{_conf_l2_str(e)}</div>"
        )
    elif status_cls == "invalid":
        reason = _esc(e.get("invalid_reason", ""))
        remaining = max(0, _max_watch_days(e) - days)
        status_text = f"第 {days} 天 ── {reason}（剩 {remaining} 天自動移除）"
        prices_html = (
            f'<div class="track-prices">{price_str}買入區間 {bz}｜目標 {tgt}｜止損 {sl}'
            f"｜{_conf_l2_str(e)}</div>"
        )
    else:  # expired
        status_text = f"已追蹤 {days} 天，今日移除"
        prices_html = ""

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


def _settled_row(e: dict) -> str:
    sym = _esc(e["symbol"])
    name = _esc(e.get("name", sym))
    strategy = _esc(e.get("strategy", "-"))
    exit_reason = e.get("_exit_reason", "")
    exit_price = e.get("_exit_price")
    entry_price = e.get("active_entry_price")
    active_days = e.get("active_days", 0)

    if exit_reason == "CLOSED_PROFIT":
        reason_html = '<span class="c-active">🎯 達到目標價，停利出場</span>'
    elif exit_reason == "CLOSED_LOSS":
        reason_html = '<span class="c-invalid">🛑 觸發止損，停損出場</span>'
    elif exit_reason == "CLOSED_TRAILING_STOP":
        reason_html = '<span class="c-watch">📈 移動停利觸發，鎖利出場</span>'
    else:
        hold_limit = _parse_hold_period(e.get("hold_period", "-"))
        if active_days > hold_limit:
            # 到期延伸後趨勢轉弱才出場（tracker DD-21），與純到期文案區分
            reason_html = (
                f'<span class="c-watch">⏰ 到期延長後趨勢轉弱'
                f'（共持倉 {active_days} 天），強制出場</span>'
            )
        else:
            reason_html = f'<span class="c-watch">⏰ 持倉期限（{active_days} 天）已到，強制出場</span>'

    pnl_html = ""
    if entry_price and exit_price:
        pnl = (exit_price - entry_price) / entry_price * 100
        sign = "+" if pnl >= 0 else ""
        pnl_cls = "c-active" if pnl >= 0 else "c-invalid"
        pnl_html = (
            f'<span class="{pnl_cls}" style="font-weight:700">{sign}{pnl:.2f}%</span>'
            f'　進場 ${entry_price:.2f} → 出場 ${exit_price:.2f}，持倉 {active_days} 天'
        )

    return f"""
<div class="track-item" style="border-left-color:#a855f7">
  <div class="track-header">
    <span class="track-symbol">{sym}</span>
    <span class="track-name">{name}</span>
    <span class="strategy-tag">{strategy}</span>
  </div>
  <div class="track-status">{reason_html}</div>
  <div class="track-prices">{pnl_html}</div>
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
    strategy_reason = _esc(rec.get("strategy_reason", ""))
    confidence_reason = _esc(rec.get("confidence_reason", ""))

    strategy_reason_html = f'<div class="detail-box">📋 策略依據：{strategy_reason}</div>' if strategy_reason else ""
    confidence_reason_html = f'<div class="detail-box">💡 信心評分依據：{confidence_reason}</div>' if confidence_reason else ""

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
  {strategy_reason_html}
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
  {confidence_reason_html}
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


def _conf_l2_str(e: dict) -> str:
    """「信心 X/10｜L2 Y 分」顯示字串（publisher DD-9），缺值顯示 N/A。"""
    conf = e.get("ai_confidence")
    l2 = e.get("l2_score")
    conf_str = f"{conf}/10" if conf is not None else "N/A"
    l2_str = f"{l2:.0f} 分" if isinstance(l2, (int, float)) else "N/A"
    return f"信心 {conf_str}｜L2 {l2_str}"


def _order_plan_section(order_plan: dict) -> str:
    """
    明日掛單計畫（publisher DD-9）：資料來自 tracker.compute_order_plan()
    （categories["order_plan"]），報告名單即次日 watch→active 的進場資格
    （tracker DD-20 名單制）。roster 依優先序渲染，前 free_slots 名標
    「✅ 建議掛單」（綠框），其餘「⏸ 備援」（黃框）。roster 為空時隱藏區段。
    """
    roster = (order_plan or {}).get("roster", [])
    if not roster:
        return ""
    free_slots = order_plan.get("free_slots", 0)

    rows = []
    for i, e in enumerate(roster, start=1):
        sym = _esc(e.get("symbol", "-"))
        name = _esc(e.get("name", sym))
        strategy = _esc(e.get("strategy", "-"))
        upper = e.get("buy_zone_upper")
        upper_str = f"${upper:.2f}" if isinstance(upper, (int, float)) else "-"
        bz = _esc(e.get("buy_zone", "-"))
        sl = _esc(e.get("stop_loss", "-"))
        tgt = _esc(e.get("target", "-"))
        remaining = max(0, _max_watch_days(e) - _days(e))
        if i <= free_slots:
            mark, row_cls = "✅ 建議掛單", "active"
        else:
            mark, row_cls = "⏸ 備援（名額外）", "watch"
        rows.append(f"""
<div class="track-item {row_cls}">
  <div class="track-header">
    <span class="track-symbol">#{i} {sym}</span>
    <span class="track-name">{name}</span>
    <span class="strategy-tag">{strategy}</span>
  </div>
  <div class="track-status">{mark}｜{_conf_l2_str(e)}</div>
  <div class="track-prices">掛單價 {upper_str}｜買入區間 {bz}｜止損 {sl}｜目標 {tgt}｜剩 {remaining} 天觀察期</div>
</div>""")

    if free_slots > 0:
        note = f"明日可進場名額 {free_slots} 支，依限價單掛買入區間上緣"
    else:
        note = "名額 0，持倉已滿，明日不建議掛新單"
    return _section_html("📋", "明日掛單計畫", rows, note)


def _build_daily_report(
    categories: dict,
    stats: dict,
    date_str: str,
    weekday: str,
    market_context: dict | None = None,
) -> str:
    total = stats.get("total", 0)
    l1 = stats.get("l1_count", 0)
    l2 = stats.get("l2_count", 0)
    ai = stats.get("ai_count", 0)

    active   = categories.get("active", [])
    watch    = categories.get("watch", [])
    invalid  = categories.get("invalid", [])
    expired  = categories.get("expired", [])
    settled  = categories.get("settled", [])
    new      = categories.get("new", [])
    reset    = categories.get("reset", [])
    order_plan = categories.get("order_plan") or {}

    regime = (market_context or {}).get("regime", "")
    dashboard_html = _build_market_dashboard(market_context or {})
    perf_html = _build_performance_section(_load_performance_stats())

    sections = ""

    if active:
        rows = [_tracking_row(e, "active") for e in active]
        sections += _section_html("✅", "有效追蹤清單", rows, f"上限 {MAX_ACTIVE_POSITIONS} 支")

    if watch:
        rows = [_tracking_row(e, "watch") for e in watch]
        sections += _section_html("🟡", "留意清單", rows)

    if invalid:
        rows = [_tracking_row(e, "invalid") for e in invalid]
        sections += _section_html("❌", "失效訊號", rows, "仍在追蹤期內")

    if expired:
        rows = [_tracking_row(e, "expired") for e in expired]
        sections += _section_html("🗑", "今日移除", rows)

    if settled:
        rows = [_settled_row(e) for e in settled]
        sections += _section_html("📦", "今日結算", rows)

    # BEAR_DISTRIBUTION 且無新進標的：顯示全面防禦橫幅
    if regime == "BEAR_DISTRIBUTION" and not new and not reset:
        breadth = (market_context or {}).get("market_breadth_pct")
        breadth_str = _esc(f"{breadth:.1f}%") if breadth is not None else "偏低"
        sections += f"""
<div class="defense-banner">
  <div class="defense-title">🛡️ 今日大盤風險過高，系統啟動全面防禦</div>
  <div class="defense-desc">市場廣度：{breadth_str}，恐慌情緒蔓延，無新進標的建議。<br>請靜待市場企穩訊號，保留現金為宜。</div>
</div>"""
    else:
        if new:
            cards = [_stock_card(i + 1, rec) for i, rec in enumerate(new)]
            sections += _section_html("🆕", "今日新進觀察名單", cards)

        if reset:
            cards = [_stock_card(i + 1, rec, "reset-card") for i, rec in enumerate(reset)]
            sections += _section_html("🔄", "重新入選，重置追蹤", cards)

    # 明日掛單計畫（publisher DD-9）：人工下單決策的單一視圖
    sections += _order_plan_section(order_plan)

    if not sections:
        sections = '<p style="color:var(--muted);padding:24px 0;">今日無資料</p>'

    na = len(active)
    nw = len(watch)
    ni = len(invalid)
    nn = len(new)
    nr = len(reset)
    ne = len(expired)
    ns = len(settled)

    settled_stat = (
        f'<div class="stat-group"><span class="stat-num" style="color:#a855f7">{ns}</span>'
        f'<span class="stat-lbl">支結算</span></div>'
        if ns > 0 else ""
    )

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
    <div class="date-meta">
      <span class="date-label">美股資料截止日</span>
      <span class="date-val">📅 {date_str}（{weekday}）</span>
      <span class="freshness-badge" id="freshness-badge" onclick="toggleRunInfo()" title="點擊查看資料來源詳情"></span>
    </div>
    <div class="run-info" id="run-info">載入中…</div>
    <div class="scan-bar">
      掃描 <strong>S&amp;P500 {total}支</strong>
      <span class="arrow">→</span> L1 <strong>{l1}支</strong>
      <span class="arrow">→</span> L2 <strong>{l2}支</strong>
      <span class="arrow">→</span> AI精選 <strong>{ai}支</strong>
    </div>
    <a class="back-link" href="../index.html">← 返回首頁</a>
  </div>
<script>
(function() {{
  var d = '{date_str}';
  var parts = d.split('-');
  var reportDay = Date.UTC(+parts[0], +parts[1]-1, +parts[2]);
  var now = new Date();
  var todayUTC = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  var diffDays = Math.round((todayUTC - reportDay) / 86400000);
  var badge = document.getElementById('freshness-badge');
  if (!badge) return;
  if (diffDays <= 0) {{
    badge.textContent = '✓ 今日最新數據 ▸';
    badge.className = 'freshness-badge freshness-ok';
  }} else {{
    var next = new Date(reportDay + 86400000);
    var mo = next.getUTCMonth() + 1;
    var dy = next.getUTCDate();
    badge.textContent = '↻ ' + mo + '/' + dy + ' 05:30（台灣時間）更新 ▸';
    badge.className = 'freshness-badge freshness-stale';
  }}
}})();

function toggleRunInfo() {{
  var info = document.getElementById('run-info');
  if (!info) return;
  if (info.classList.contains('visible')) {{
    info.classList.remove('visible');
    return;
  }}
  info.classList.add('visible');
  if (info._loaded) return;
  info._loaded = true;
  fetch('../data/last_run.json?_=' + Date.now())
    .then(function(r) {{ return r.json(); }})
    .then(function(d) {{
      var utc = d.run_at_utc || '—';
      // 轉換為台灣時間顯示
      var dt = new Date(utc);
      var twStr = dt.toLocaleString('zh-TW', {{timeZone:'Asia/Taipei', hour12:false,
        year:'numeric', month:'2-digit', day:'2-digit',
        hour:'2-digit', minute:'2-digit'}});
      info.innerHTML =
        '<strong>資料來源核實</strong><br>' +
        '執行時間（UTC）：' + utc + '<br>' +
        '執行時間（台灣）：' + twStr + '<br>' +
        '資料截止日：' + (d.market_date || '—') + '（若於美股盤中執行，系統會自動捨棄當日未收盤數據，此欄可能為前一交易日）<br>' +
        '掃描：S&amp;P500 ' + (d.total_scanned || '—') + ' 支 → L1 ' +
        (d.l1_count || '—') + ' → L2 ' + (d.l2_count || '—') +
        ' → AI精選 ' + (d.ai_count || '—') + ' 支';
    }})
    .catch(function() {{
      info.textContent = '⚠ 無法載入 last_run.json（本地預覽時正常，GitHub Pages 上才有此檔案）';
    }});
}}
</script>

  {dashboard_html}

  {perf_html}

  {sections}

  <div class="summary-box">
    <h2>📈 今日統計</h2>
    <div class="stat-row">
      <div class="stat-group"><span class="stat-num c-active">{na} / {MAX_ACTIVE_POSITIONS}</span><span class="stat-lbl">持倉／上限</span></div>
      <div class="stat-group"><span class="stat-num c-watch">{nw}</span><span class="stat-lbl">支留意</span></div>
      <div class="stat-group"><span class="stat-num c-invalid">{ni}</span><span class="stat-lbl">支失效</span></div>
    </div>
    <div class="stat-row">
      <div class="stat-group"><span class="stat-num c-new">{nn}</span><span class="stat-lbl">支新增</span></div>
      <div class="stat-group"><span class="stat-num c-reset">{nr}</span><span class="stat-lbl">支重新入選</span></div>
      <div class="stat-group"><span class="stat-num c-removed">{ne}</span><span class="stat-lbl">支移除</span></div>
      {settled_stat}
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


_INFO_HTML = """
<details open>
<summary>📖 系統說明</summary>
<div class="info-grid">

  <div class="info-card">
    <h3>📡 篩選流程</h3>
    <div class="pipe-flow">
      <div class="pipe-step"><span class="pipe-badge">L0</span>S&amp;P 500 約 503 支成份股</div>
      <div class="pipe-arrow">↓</div>
      <div class="pipe-step"><span class="pipe-badge">L1</span>硬條件篩選：股價 &gt; $5、30 日均量美元成交額 &gt; $1,000 萬、市值 &gt; 3 億、ATR14/收盤價 ≤ 8%（波動風控，避免止損緩衝形同虛設）；財報防禦牆：排除 5 天內有財報的個股</div>
      <div class="pipe-arrow">↓</div>
      <div class="pipe-step"><span class="pipe-badge">L2</span>技術評分 100 分制，門檻 60 分；恐慌超跌環境（見右側）降至 40 分，RSI &lt; 35 且 20 日跌幅 &gt; 15% 的超賣股強制放行；門檻篩選後再依總分排名取前 55 名，穩定收斂至 50～60 支候選（不受大盤強弱影響通過數量暴增或暴減）</div>
      <div class="pipe-arrow">↓</div>
      <div class="pipe-step"><span class="pipe-badge">L3</span>DeepSeek AI 依當日大盤環境（Regime）的主推策略精選，最多 3 支；輸入矩陣含 ATR 標準化動能（Momentum_ATR）、個股波動單位（ATR14，動能策略買入區間與止損的深度基準）、量能推進因子（VTF_Score，正值=帶量上攻，負值=高檔出貨）、60 日 Beta 與財報剩餘天數（≤5 天自動排除），跨行業公平評比、避免高 Beta 偏見；動能策略買入區間錨定收盤價下方 0.25～1×ATR14 淺回檔帶（深度自適應個股波動，不再要求深度回檔至均線帶）；並疊加基本面維度（估值 Fwd_PE、獲利品質 Profit_Margin、成長性 Rev_Growth_YoY）與空頭比例（Short_Float_Pct，&gt;15% 視為高軋空風險）輔助取捨，技術面相近時優先選基本面健康的個股；陰跌熊市環境下全面防禦，不輸出任何建議（詳見右側 Regime 表）</div>
    </div>
  </div>

  <div class="info-card">
    <h3>🌐 大盤環境（Market Regime）</h3>
    <p style="font-size:0.72rem;color:var(--muted);margin-bottom:8px">市場廣度 = S&amp;P 500 中收盤價高於 50 日 SMA 的股票比例；VIX 為芝加哥期權交易所恐慌指數</p>
    <table class="info-table">
      <tr><th>環境名稱</th><th>判斷條件</th><th>主推策略</th><th>L2 門檻</th></tr>
      <tr><td style="color:var(--active)">📈 牛市趨勢</td><td>廣度 ≥ 60% 且 VIX &lt; 20</td><td>動能策略</td><td>60 分</td></tr>
      <tr><td style="color:var(--watch)">⚖️ 震盪整理</td><td>廣度 35～60% 且 VIX &lt; 20</td><td>突破策略（積極）</td><td>60 分</td></tr>
      <tr><td style="color:#f59e0b">⚡ 高波動整理</td><td>廣度 35～60% 且 VIX ≥ 20</td><td>突破策略（保守）</td><td><strong>65 分</strong></td></tr>
      <tr><td style="color:#f97316">🔥 恐慌超跌</td><td>廣度 &lt; 35% 且 VIX ≥ 25</td><td>反轉策略</td><td>40 分</td></tr>
      <tr><td style="color:var(--invalid)">🐻 陰跌熊市</td><td>廣度 &lt; 35% 且 VIX &lt; 25</td><td>⛔ 全面防禦</td><td>—</td></tr>
    </table>
  </div>

  <div class="info-card">
    <h3>📊 L2 技術評分（100 分制）</h3>
    <table class="info-table">
      <tr><th>指標</th><th>滿分</th><th>評分說明</th></tr>
      <tr><td>MA 多頭排列</td><td>20</td><td>EMA5&gt;10&gt;20&gt;50，每條件 +6.67 分</td></tr>
      <tr><td>RSI 健康區間</td><td>18</td><td>50～70 滿分；40～50 或 70～80 半分；其餘 0（RSI&gt;80 軟過濾，不驅逐，BULL_TREND 下 50～80 均為滿分）</td></tr>
      <tr><td>MACD 柱狀體</td><td>17</td><td>正且遞增滿分；正遞減半分；負 0</td></tr>
      <tr><td>量能放大（含趨勢）</td><td>15</td><td>VTF 基礎分（量比 × K_pos 阻斷） × 5 日量能趨勢係數（持續放量 ×1.0、平穩 ×0.85、縮量 ×0.65）</td></tr>
      <tr><td>多週期動能</td><td>15</td><td>20 日 ATR 倍數主趨勢 × 5 日方向確認：中短線一致滿分（≥2ATR+5日確認），短線反轉減半</td></tr>
      <tr><td>相對強度 RS</td><td>15</td><td>個股 5 日報酬率 − 板塊 ETF 5 日報酬率：≥+2% 滿分；+0.5%～+2% 得 8 分；-0.5%～+0.5% 得 3 分；&lt;-0.5% 得 0 分</td></tr>
    </table>
  </div>

  <div class="info-card">
    <h3>🎯 買進區間與停損停利如何設定</h3>
    <p style="font-size:0.72rem;color:var(--muted);margin-bottom:8px">買入區間、目標價與初始止損由 AI 在訊號日依策略規則輸出並鎖定；目標價是停利觸發線。進場後止損改由系統動態管理（見下表），跟單請以每日報告顯示的止損為準</p>
    <table class="info-table">
      <tr><th>策略</th><th>買入區間</th><th>初始止損</th></tr>
      <tr><td>動能</td><td>收盤價下方 0.25～1×ATR14 淺回檔帶（下緣不低於 EMA10），深度自適應個股波動；若已量縮回檔至 EMA20～EMA10 帶則直接採用該區間</td><td>買入區間下緣再下方 1×ATR14（回檔帶情境改用 EMA20 下方 2%，取較高者；不得寬於進場價 −10%）</td></tr>
      <tr><td>突破</td><td>優先設在回測確認帶（20 日高點～+2%），次選突破緩衝帶（20 日高點上方 +0.5%～+1.5%）；距 20 日高點超過 +3% 視為追高</td><td>20 日高點下方 2%</td></tr>
      <tr><td>反轉</td><td>EMA50 ±3% 支撐帶，須同時滿足底背離確認（Stoch_K &lt; 25 且 RSI 高於 5 日前）且收盤已明顯高於 20 日低點（右側反彈確立）</td><td>20 日低點下方 2%（不得高於 EMA50）</td></tr>
    </table>
    <table class="info-table" style="margin-top:8px">
      <tr><th>進場後動態風控</th><th>規則（系統自動執行）</th></tr>
      <tr><td>🎯 停利</td><td>當日最高價觸及目標價即停利出場（出場價 = 目標價）</td></tr>
      <tr><td>🛑 停損</td><td>當日最低價觸及有效止損即停損出場（出場價 = 止損價）；同日雙觸發（黑天鵝）保守判為停損</td></tr>
      <tr><td>🔒 保本鎖定</td><td>收盤浮盈達目標距離 50% 時，止損自動上移至買入區間上緣，報告標註「🔒保本」</td></tr>
      <tr><td>📈 移動停利</td><td>動能/突破策略峰值浮盈超過 10% 後，收盤自峰值回撤 5% 即鎖利出場（出場價 = 收盤價；反轉策略不適用）</td></tr>
      <tr><td>⏰ 到期出場</td><td>持倉達 AI 設定天數時，動能/突破策略若收盤仍在期間最高收盤 3% 以內，自動延長持倉（最多再 10 個交易日），期間收盤自峰值回撤 3% 即出場；反轉策略與趨勢已弱者照舊到期出場</td></tr>
    </table>
  </div>

  <div class="info-card">
    <h3>🚦 訊號追蹤狀態</h3>
    <table class="info-table">
      <tr><td>✅ active</td><td>當日最低價已觸及買入區間上緣（模擬限價單成交），且在前一日報告「明日掛單計畫」的建議掛單名單內（優先序 = AI 信心分數，同分比 L2 分數；名額預設 5 支，MAX_ACTIVE_POSITIONS 可調），顯示持倉天數與彩色浮損益</td></tr>
      <tr><td>🟡 watch</td><td>今日未觸及買入區間等待回落；或已觸價但未在掛單名單（持倉已滿或優先序不足），暫不進場（觀察期照常倒數，次日重新競爭）</td></tr>
      <tr><td>❌ invalid</td><td>趨勢轉弱或跌破止損，訊號失效（僅發生在今日未觸價成交的 watch 階段；已進場的 active 部位出場只由 settled 控制）</td></tr>
      <tr><td>🗑 expired</td><td>觀察達策略對應上限自動移除（突破/動能 5 日、反轉 10 日；高波動整理市的突破 3 日、VIX&gt;35 尖底的反轉 5 日）</td></tr>
      <tr><td>📦 settled</td><td>停利／停損／移動停利／到期結算，歸檔績效資料庫</td></tr>
    </table>
  </div>

</div>
</details>
"""


def _build_index() -> str:
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
    <p class="tz-note" id="index-freshness"></p>
    <p class="tz-note" id="index-lastrun" style="font-size:0.75rem;"></p>
  </div>
  <div class="doc-link-row">
    <a class="doc-link" href="technical-doc.html">📄 篩選與進出場機制技術文件</a>
  </div>
<script>
(function() {{
  // report-list 等元素定義在此 script 標籤之後，必須等 DOMContentLoaded 才能 getElementById 取到
  document.addEventListener('DOMContentLoaded', function() {{
  // ── 時間狀態訊息（依 UTC 時段）────────────────────────────────
  var el = document.getElementById('index-freshness');
  if (el) {{
    var now = new Date();
    var day = now.getUTCDay();   // 0=日, 6=六
    var h   = now.getUTCHours();
    var m   = now.getUTCMinutes();
    var msg;
    if (day === 0 || day === 6) {{
      msg = '週末無新報告，下一份報告於週一 05:30（台灣時間）產生';
    }} else if (h > 21 || (h === 21 && m >= 30)) {{
      msg = '今日報告已產生（CI UTC 21:30 執行）· 請點選最新報告查看';
    }} else if (h >= 20) {{
      msg = '美股已收盤（UTC 20:00），報告正在產生中，將於台灣時間 05:30 發布';
    }} else if (h > 13 || (h === 13 && m >= 30)) {{
      msg = '美股交易中（UTC 13:30–20:00）· 今日報告將於台灣時間 05:30 更新';
    }} else {{
      msg = '美股市場尚未開盤（UTC 13:30 開盤）· 今日報告將於台灣時間 05:30 更新';
    }}
    el.textContent = msg;
  }}

  // ── 顯示上次實際執行時間（從 last_run.json）────────────────────
  var lr = document.getElementById('index-lastrun');
  if (lr) {{
    fetch('data/last_run.json?_=' + Date.now())
      .then(function(r) {{ return r.json(); }})
      .then(function(d) {{
        if (!d.run_at_utc) return;
        var dt = new Date(d.run_at_utc);
        var twStr = dt.toLocaleString('zh-TW', {{timeZone:'Asia/Taipei', hour12:false,
          year:'numeric', month:'2-digit', day:'2-digit',
          hour:'2-digit', minute:'2-digit'}});
        lr.textContent = '上次執行：' + twStr + '（台灣時間）· 資料截止 ' + (d.market_date || '—');
      }})
      .catch(function() {{}});  // 靜默失敗，首次部署前 last_run.json 不存在
  }}

  // ── 動態渲染歷史報告列表（從 reports-index.json）─────────────
  // 讓首頁完全與索引 JSON 同步，不需要每次重新產生 index.html（DD-5）
  var list = document.getElementById('report-list');
  if (list) {{
    fetch('data/reports-index.json?_=' + Date.now())
      .then(function(r) {{ return r.json(); }})
      .then(function(data) {{
        if (!data || !data.length) {{
          list.innerHTML = '<div class="empty-state">尚無報告，請先執行選股系統</div>';
          return;
        }}
        data.sort(function(a, b) {{ return b.date.localeCompare(a.date); }});
        var html = '';
        data.forEach(function(entry) {{
          var chips = '';
          if (entry.active)  chips += '<span class="chip active">'  + entry.active  + ' 有效</span>';
          if (entry.watch)   chips += '<span class="chip watch">'   + entry.watch   + ' 留意</span>';
          if (entry.invalid) chips += '<span class="chip invalid">' + entry.invalid + ' 失效</span>';
          if (entry.new)     chips += '<span class="chip new">'     + entry.new     + ' 新增</span>';
          if (entry.reset)   chips += '<span class="chip reset">'   + entry.reset   + ' 重置</span>';
          if (!chips) chips = '<span class="chip neutral">無追蹤</span>';
          html += '<a class="report-entry" href="reports/' + entry.date + '.html">' +
            '<div><div class="report-date">' + entry.date + '</div>' +
            '<div class="report-weekday">（' + (entry.weekday || '') + '）</div></div>' +
            '<div class="report-chips">' + chips + '</div>' +
            '<span class="arrow-icon">›</span></a>';
        }});
        list.innerHTML = html;
      }})
      .catch(function() {{
        list.innerHTML = '<div class="empty-state">尚無報告，請先執行選股系統</div>';
      }});
  }}
  }});
}})();
</script>
  {_INFO_HTML}
  <div class="report-section-title">📋 歷史報告</div>
  <div class="report-list" id="report-list"></div>
</div>
</body>
</html>"""


def sync_index() -> None:
    """重新生成 docs/index.html。_build_index() 為無參數確定性函式（DD-6），
    改動 _INFO_HTML/_CSS/模板後執行本函式即完成同步，不得手動編輯 docs/index.html。"""
    _INDEX_HTML.write_text(_build_index(), encoding="utf-8", newline="\n")
    print(f"[publisher] 首頁已同步：{_INDEX_HTML}")


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


def _write_last_run(stats: dict, date_str: str, market_context: dict | None = None) -> None:
    """寫入 docs/data/last_run.json，記錄本次實際執行時間（UTC）與掃描統計。
    run_at_utc 使用 datetime.utcnow()——此欄位記錄執行時刻，與報告日期錨定規則無關。
    新增 regime 與 market_date 欄位，供 fetch_regime_quick() 的遲滯帶讀取（DD-5）。"""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    regime = (market_context or {}).get("regime", "")
    payload = {
        "run_at_utc":    datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "market_date":   date_str,
        "regime":        regime,          # 供下次執行的遲滯帶校驗（DD-5）
        "total_scanned": stats.get("total", 0),
        "l1_count":      stats.get("l1_count", 0),
        "l2_count":      stats.get("l2_count", 0),
        "ai_count":      stats.get("ai_count", 0),
    }
    with open(_LAST_RUN_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[publisher] last_run.json 已更新：{payload['run_at_utc']}（regime={regime}）")


def _check_git_remote() -> bool:
    result = subprocess.run(
        ["git", "remote"], capture_output=True, text=True, cwd=str(_ROOT)
    )
    return bool(result.stdout.strip())


# ── 主函式 ──────────────────────────────────────────────────────────

def publish(
    categories: dict,
    stats: dict,
    dry_run: bool = False,
    market_context: dict | None = None,
) -> None:
    """
    生成每日 HTML 報告 + 更新首頁索引，並 git push（dry_run 時略過 push）。
    """
    dt: datetime = stats["date"]
    weekday = WEEKDAY_ZH[dt.weekday()]
    date_str = dt.strftime("%Y-%m-%d")

    # 建立目錄
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # 生成每日報告
    report_html = _build_daily_report(categories, stats, date_str, weekday, market_context=market_context)
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

    # 生成首頁（報告列表由前端 fetch reports-index.json 動態渲染，DD-5）
    sync_index()

    # 寫入執行記錄（供前端核實資料新鮮度）
    _write_last_run(stats, date_str, market_context=market_context)

    if dry_run:
        print(f"[publisher] Dry-run 模式，略過 git push")
        print(f"[publisher] 請用瀏覽器開啟：{report_path}")
        return

    if not _check_git_remote():
        print("[publisher] ⚠️  尚未設定 git remote，略過 push。請先執行：")
        print("  git remote add origin https://github.com/<user>/<repo>.git")
        return

    _git_push(date_str)


if __name__ == "__main__":
    sync_index()
