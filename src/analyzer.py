"""本地績效診斷：歸納已結算交易的賺賠關聯，輸出 ai_hints.json 供 L3 Prompt 參考。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_DATA_DIR = _ROOT / "data"
_PERF_PATH = _DATA_DIR / "performance_history.json"
_HINTS_PATH = _DATA_DIR / "ai_hints.json"

MIN_GROUP_SAMPLES = 3   # 單一分組最少樣本數，低於此不產生該組 hint（DD-3）
MIN_TOTAL_SAMPLES = 5   # 有效總樣本低於此，prompt_lines 全空（DD-3）

_SETTLED_REASONS = {"CLOSED_PROFIT", "CLOSED_LOSS", "CLOSED_TRAILING_STOP", "FORCE_EXPIRED"}

# (json 鍵, hint 標籤, 記錄取值函式)——維度定義單一來源（DD-2）
_DIMENSIONS = [
    ("by_regime",   "Regime", lambda r: r.get("signal_details", {}).get("entry_regime", "")),
    ("by_strategy", "策略",   lambda r: r.get("signal_details", {}).get("assigned_strategy", "")),
    ("by_sector",   "產業",   lambda r: r.get("meta_data", {}).get("sector", "")),
]


def _load_settled_records() -> list[dict]:
    """讀取已結算且 return_pct 非空的記錄。冷啟動安全：檔案不存在或損壞回傳空清單。"""
    if not _PERF_PATH.exists():
        return []
    try:
        with open(_PERF_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    return [
        r for r in data.get("history_records", [])
        if r.get("actual_outcome", {}).get("exit_reason") in _SETTLED_REASONS
        and r.get("performance_metrics", {}).get("return_pct") is not None
    ]


def _aggregate(records: list[dict], key_fn) -> list[dict]:
    """依 key_fn 分組統計筆數、勝率、平均報酬；含全部分組（審計用），依筆數降冪。"""
    groups: dict[str, list[float]] = {}
    for r in records:
        key = key_fn(r)
        if not key:
            continue
        groups.setdefault(key, []).append(r["performance_metrics"]["return_pct"])

    stats = []
    for key, returns in groups.items():
        # 勝負判定與 tracker DD-13 同口徑：純 return_pct > 0
        wins = sum(1 for x in returns if x > 0)
        stats.append({
            "key":            key,
            "trades":         len(returns),
            "win_rate":       round(wins / len(returns) * 100, 1),
            "avg_return_pct": round(sum(returns) / len(returns), 2),
        })
    stats.sort(key=lambda s: (-s["trades"], s["key"]))
    return stats


def _render_lines(dimensions: dict[str, list[dict]]) -> list[str]:
    """渲染達分組門檻的描述性統計行；語氣不得指令化（DD-3）。"""
    lines = []
    for dim_key, label, _ in _DIMENSIONS:
        for s in dimensions[dim_key]:
            if s["trades"] < MIN_GROUP_SAMPLES:
                continue
            lines.append(
                f"【{label}】{s['key']}：{s['trades']} 筆結算，勝率 {s['win_rate']:.1f}%，"
                f"平均報酬 {s['avg_return_pct']:+.2f}%"
            )
    return lines


def generate_hints(market_date: str | None = None) -> dict:
    """讀取 performance_history.json，統計三維度賺賠關聯，
    寫入 data/ai_hints.json 並回傳該 dict。冷啟動回傳空統計。"""
    records = _load_settled_records()
    dimensions = {
        dim_key: _aggregate(records, key_fn)
        for dim_key, _, key_fn in _DIMENSIONS
    }
    prompt_lines = _render_lines(dimensions) if len(records) >= MIN_TOTAL_SAMPLES else []

    payload = {
        "generated_at_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "market_date":      market_date or "",
        "total_settled":    len(records),
        "dimensions":       dimensions,
        "prompt_lines":     prompt_lines,
    }
    _DATA_DIR.mkdir(exist_ok=True)
    with open(_HINTS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    if prompt_lines:
        print(f"[analyzer] 績效診斷完成：{len(records)} 筆結算，生成 {len(prompt_lines)} 條歷史回饋")
    else:
        print(f"[analyzer] 結算樣本不足（{len(records)} 筆 < {MIN_TOTAL_SAMPLES}），本輪不生成歷史回饋")
    return payload
