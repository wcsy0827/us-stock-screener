"""analyzer.py 純函式單元測試。對應 specs/analyzer.md 的 Acceptance Criteria。"""

import json

import pytest

import analyzer


@pytest.fixture(autouse=True)
def isolate_data_dir(tmp_path, monkeypatch):
    """所有測試一律隔離至 tmp_path，避免誤寫 repo 的 data/。"""
    monkeypatch.setattr(analyzer, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(analyzer, "_PERF_PATH", tmp_path / "performance_history.json")
    monkeypatch.setattr(analyzer, "_HINTS_PATH", tmp_path / "ai_hints.json")


def _record(regime="BULL_TREND", strategy="動能策略", sector="Technology",
            exit_reason="CLOSED_PROFIT", return_pct=2.0):
    return {
        "meta_data": {"ticker": "TEST", "sector": sector},
        "signal_details": {"entry_regime": regime, "assigned_strategy": strategy},
        "actual_outcome": {"exit_reason": exit_reason},
        "performance_metrics": {
            "return_pct": return_pct,
            "is_win": return_pct > 0 if return_pct is not None else None,
        },
    }


def _write_history(tmp_path, records):
    with open(tmp_path / "performance_history.json", "w", encoding="utf-8") as f:
        json.dump({"history_records": records}, f, ensure_ascii=False)


def _read_hints(tmp_path):
    with open(tmp_path / "ai_hints.json", encoding="utf-8") as f:
        return json.load(f)


# ── 冷啟動與容錯 ─────────────────────────────────────────────────────

def test_cold_start_no_history_file(tmp_path):
    payload = analyzer.generate_hints(market_date="2026-07-02")
    assert payload["total_settled"] == 0
    assert payload["prompt_lines"] == []
    assert payload["market_date"] == "2026-07-02"
    # 仍寫出 ai_hints.json（冷啟動安全）
    assert _read_hints(tmp_path)["total_settled"] == 0


def test_corrupt_history_file(tmp_path):
    (tmp_path / "performance_history.json").write_text("{ 損壞的JSON", encoding="utf-8")
    payload = analyzer.generate_hints()
    assert payload["total_settled"] == 0
    assert payload["prompt_lines"] == []


def test_unsettled_and_null_return_records_excluded(tmp_path):
    _write_history(tmp_path, [
        _record(exit_reason="INVALID"),             # 非結算 exit_reason
        _record(return_pct=None),                   # return_pct 為 null
        _record(),
    ])
    payload = analyzer.generate_hints()
    assert payload["total_settled"] == 1


# ── 聚合統計 ─────────────────────────────────────────────────────────

def test_aggregation_numbers(tmp_path):
    _write_history(tmp_path, [
        _record(return_pct=4.0),
        _record(return_pct=2.0),
        _record(return_pct=-3.0),
        _record(regime="PANIC_REVERSAL", strategy="反轉策略", sector="Energy", return_pct=1.0),
        _record(regime="PANIC_REVERSAL", strategy="反轉策略", sector="Energy", return_pct=-1.0),
        _record(regime="PANIC_REVERSAL", strategy="反轉策略", sector="Energy", return_pct=-1.0),
    ])
    payload = analyzer.generate_hints()
    assert payload["total_settled"] == 6

    by_regime = {s["key"]: s for s in payload["dimensions"]["by_regime"]}
    bull = by_regime["BULL_TREND"]
    assert bull["trades"] == 3
    assert bull["win_rate"] == pytest.approx(66.7)
    assert bull["avg_return_pct"] == pytest.approx(1.0)

    panic = by_regime["PANIC_REVERSAL"]
    assert panic["trades"] == 3
    assert panic["win_rate"] == pytest.approx(33.3)
    assert panic["avg_return_pct"] == pytest.approx(-0.33)

    # 兩組皆達 MIN_GROUP_SAMPLES，三維度各 2 組 → 6 條
    assert len(payload["prompt_lines"]) == 6
    assert any("BULL_TREND" in x and "66.7%" in x for x in payload["prompt_lines"])


# ── 門檻抑制 ─────────────────────────────────────────────────────────

def test_group_below_min_samples_suppressed(tmp_path):
    # 總樣本 5（達 MIN_TOTAL_SAMPLES），但 CONSOLIDATION 組只有 2 筆
    _write_history(tmp_path, [
        _record(), _record(), _record(),
        _record(regime="CONSOLIDATION", strategy="突破策略", sector="Financials"),
        _record(regime="CONSOLIDATION", strategy="突破策略", sector="Financials"),
    ])
    payload = analyzer.generate_hints()
    lines = payload["prompt_lines"]
    assert any("BULL_TREND" in x for x in lines)
    assert not any("CONSOLIDATION" in x for x in lines)
    # dimensions 仍含全部分組（審計用），不受門檻限制
    assert any(s["key"] == "CONSOLIDATION" for s in payload["dimensions"]["by_regime"])


def test_total_below_min_samples_no_prompt_lines(tmp_path):
    # 4 筆同組（達分組門檻）但總樣本 < MIN_TOTAL_SAMPLES
    _write_history(tmp_path, [_record() for _ in range(4)])
    payload = analyzer.generate_hints()
    assert payload["total_settled"] == 4
    assert payload["prompt_lines"] == []
    assert payload["dimensions"]["by_regime"][0]["trades"] == 4
