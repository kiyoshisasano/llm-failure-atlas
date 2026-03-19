"""
compute_kpi.py

Phase 22-lite: KPI measurement (measurement only, no control).

KPIs:
  ① threshold_boundary_rate  — detection stability
  ② fix_dominance            — fix diversity
  ③a failure_monotonicity    — system improvement trend
  ③b rollback_rate           — auto-apply safety
  ④ no_regression_rate       — absence of hard regressions
  ⑤ causal_consistency_rate  — root stability across policy updates

Data sources:
  - learning/calibration_history.json   (①)
  - learning/fix_effectiveness.json     (②, ③b)
  - learning/run_history.json           (③a, ④, ⑤)
  - validation/errors.json              (①)

Usage:
  python compute_kpi.py                        # compute all KPIs
  python compute_kpi.py --record <run.json>    # record a run then compute
  python compute_kpi.py --json-only            # JSON output
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime

LEARNING_DIR = os.environ.get(
    "ATLAS_LEARNING_DIR",
    os.path.join(os.path.dirname(__file__), "learning")
)

RUN_HISTORY_PATH = os.path.join(LEARNING_DIR, "run_history.json")
FIX_EFFECTIVENESS_PATH = os.path.join(LEARNING_DIR, "fix_effectiveness.json")
CALIBRATION_HISTORY_PATH = os.path.join(LEARNING_DIR, "calibration_history.json")

# Validation errors (relative to atlas root)
VALIDATION_DIR = os.environ.get(
    "ATLAS_VALIDATION_DIR",
    os.path.join(os.path.dirname(__file__), "validation")
)
ERRORS_PATH = os.path.join(VALIDATION_DIR, "errors.json")

WINDOW_SIZE = 30  # rolling window for KPIs


# ---------------------------------------------------------------------------
# Store I/O
# ---------------------------------------------------------------------------

def _load_json(path: str):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_json(path: str, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Run recording
# ---------------------------------------------------------------------------

def record_run(root: str, failure_count: int, decision: str,
               rollback_executed: bool = False,
               has_hard_regression: bool = False) -> dict:
    """
    Record one pipeline iteration to run_history.json.

    Called after a full cycle:
      diagnose → decide → fix → evaluate → learn
    """
    store = _load_json(RUN_HISTORY_PATH)
    if not store:
        store = {"runs": [], "window_size": WINDOW_SIZE}

    runs = store.get("runs", [])
    iteration = len(runs) + 1

    runs.append({
        "iteration": iteration,
        "timestamp": datetime.now().isoformat(),
        "root": root,
        "failure_count": failure_count,
        "decision": decision,
        "rollback_executed": rollback_executed,
        "has_hard_regression": has_hard_regression,
    })

    # Keep last 100 runs
    store["runs"] = runs[-100:]
    _save_json(RUN_HISTORY_PATH, store)
    return store


# ---------------------------------------------------------------------------
# KPI ①: threshold_boundary_rate
# ---------------------------------------------------------------------------

def compute_threshold_boundary_rate() -> dict:
    """
    = (# threshold_boundary errors) / (# total scenarios)
    Target: < 5%
    Source: calibration_history.json
    """
    cal = _load_json(CALIBRATION_HISTORY_PATH)
    runs = cal.get("runs", [])

    # Use rolling window
    window = runs[-WINDOW_SIZE:]
    if not window:
        return {"value": 0.0, "target": 0.05, "met": True,
                "detail": "no calibration data"}

    total_errors = 0
    tb_errors = 0
    total_scenarios = 0

    for run in window:
        for key, count in run.get("error_counts", {}).items():
            total_errors += count
            if "threshold_boundary" in key:
                tb_errors += count
        # Each run processes all scenarios
        total_scenarios += run.get("total_errors", 0) + 30  # 30 scenarios per run

    rate = tb_errors / total_scenarios if total_scenarios > 0 else 0.0
    return {
        "value": round(rate, 4),
        "target": 0.05,
        "met": rate < 0.05,
        "detail": f"{tb_errors} threshold_boundary / {total_scenarios} total",
    }


# ---------------------------------------------------------------------------
# KPI ②: fix_dominance
# ---------------------------------------------------------------------------

def compute_fix_dominance() -> dict:
    """
    = max(failure × fix_type frequency) / total applied fixes
    Target: < 60%
    Source: fix_effectiveness.json
    """
    eff = _load_json(FIX_EFFECTIVENESS_PATH)
    if not eff:
        return {"value": 0.0, "target": 0.60, "met": True,
                "detail": "no effectiveness data"}

    attempts_list = []
    for fid, fixes in eff.items():
        for ft, data in fixes.items():
            attempts_list.append({
                "key": f"{fid}/{ft}",
                "attempts": data.get("attempts", 0),
            })

    total = sum(a["attempts"] for a in attempts_list)
    if total == 0:
        return {"value": 0.0, "target": 0.60, "met": True,
                "detail": "no attempts recorded"}

    max_entry = max(attempts_list, key=lambda x: x["attempts"])
    dominance = max_entry["attempts"] / total

    return {
        "value": round(dominance, 4),
        "target": 0.60,
        "met": dominance < 0.60,
        "detail": f"dominant: {max_entry['key']} ({max_entry['attempts']}/{total})",
    }


# ---------------------------------------------------------------------------
# KPI ③a: failure_monotonicity
# ---------------------------------------------------------------------------

def compute_failure_monotonicity() -> dict:
    """
    = (# cases where failure_count[t+5] <= failure_count[t]) / (# total pairs)
    Target: > 90%
    Source: run_history.json
    """
    store = _load_json(RUN_HISTORY_PATH)
    runs = store.get("runs", [])

    if len(runs) < 6:
        return {"value": None, "target": 0.90, "met": None,
                "detail": f"need >= 6 runs, have {len(runs)}"}

    step = 5
    monotone_count = 0
    total_pairs = 0

    for i in range(len(runs) - step):
        fc_t = runs[i]["failure_count"]
        fc_t5 = runs[i + step]["failure_count"]
        total_pairs += 1
        if fc_t5 <= fc_t:
            monotone_count += 1

    rate = monotone_count / total_pairs if total_pairs > 0 else 0.0
    return {
        "value": round(rate, 4),
        "target": 0.90,
        "met": rate > 0.90,
        "detail": f"{monotone_count}/{total_pairs} monotone pairs (step={step})",
    }


# ---------------------------------------------------------------------------
# KPI ③b: rollback_rate
# ---------------------------------------------------------------------------

def compute_rollback_rate() -> dict:
    """
    = (# rollback decisions) / (# auto_apply attempts)
    Target: < 10%
    Source: fix_effectiveness.json + run_history.json
    """
    # Primary: run_history (has rollback_executed)
    store = _load_json(RUN_HISTORY_PATH)
    runs = store.get("runs", [])

    if runs:
        auto_apply_runs = [r for r in runs if r.get("decision") in ("keep", "rollback")]
        total = len(auto_apply_runs)
        rollbacks = sum(1 for r in auto_apply_runs if r.get("rollback_executed"))
    else:
        # Fallback: fix_effectiveness
        eff = _load_json(FIX_EFFECTIVENESS_PATH)
        total = sum(d.get("attempts", 0) for f in eff.values() for d in f.values())
        rollbacks = sum(d.get("rollback", 0) for f in eff.values() for d in f.values())

    rate = rollbacks / total if total > 0 else 0.0
    return {
        "value": round(rate, 4),
        "target": 0.10,
        "met": rate < 0.10,
        "detail": f"{rollbacks}/{total} rollbacks",
    }


# ---------------------------------------------------------------------------
# KPI ④: no_regression_rate
# ---------------------------------------------------------------------------

def compute_no_regression_rate() -> dict:
    """
    = (# runs with no hard regression) / (# total runs)
    Target: > 95%
    Source: run_history.json
    """
    store = _load_json(RUN_HISTORY_PATH)
    runs = store.get("runs", [])

    if not runs:
        return {"value": None, "target": 0.95, "met": None,
                "detail": "no run history"}

    window = runs[-WINDOW_SIZE:]
    total = len(window)
    clean = sum(1 for r in window if not r.get("has_hard_regression", False))
    rate = clean / total

    return {
        "value": round(rate, 4),
        "target": 0.95,
        "met": rate > 0.95,
        "detail": f"{clean}/{total} runs without hard regression",
    }


# ---------------------------------------------------------------------------
# KPI ⑤: causal_consistency_rate
# ---------------------------------------------------------------------------

def compute_causal_consistency_rate() -> dict:
    """
    = (# cases where top-1 root remains unchanged across 5 iterations)
      / (# total windows)
    Target: > 90%

    Measures policy drift: does the learning update change which root
    is identified as primary? This is NOT about determinism within a
    single policy state, but stability across policy updates.

    Source: run_history.json
    """
    store = _load_json(RUN_HISTORY_PATH)
    runs = store.get("runs", [])

    if len(runs) < 5:
        return {"value": None, "target": 0.90, "met": None,
                "detail": f"need >= 5 runs, have {len(runs)}"}

    window_size = 5
    stable_count = 0
    total_windows = 0

    for i in range(len(runs) - window_size + 1):
        window = runs[i:i + window_size]
        roots = [r["root"] for r in window]
        total_windows += 1
        if len(set(roots)) == 1:
            stable_count += 1

    rate = stable_count / total_windows if total_windows > 0 else 0.0
    return {
        "value": round(rate, 4),
        "target": 0.90,
        "met": rate > 0.90,
        "detail": f"{stable_count}/{total_windows} stable windows (size={window_size})",
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def compute_all_kpis() -> dict:
    """Compute all 5 KPIs and return structured result."""
    return {
        "timestamp": datetime.now().isoformat(),
        "window_size": WINDOW_SIZE,
        "kpis": {
            "threshold_boundary_rate": compute_threshold_boundary_rate(),
            "fix_dominance": compute_fix_dominance(),
            "failure_monotonicity": compute_failure_monotonicity(),
            "rollback_rate": compute_rollback_rate(),
            "no_regression_rate": compute_no_regression_rate(),
            "causal_consistency_rate": compute_causal_consistency_rate(),
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}

    json_only = "--json-only" in flags

    # Record mode: --record <run.json>
    if "--record" in flags:
        if not args:
            print("Usage: python compute_kpi.py --record run.json")
            sys.exit(1)
        with open(args[0]) as f:
            run_data = json.load(f)
        record_run(
            root=run_data["root"],
            failure_count=run_data["failure_count"],
            decision=run_data["decision"],
            rollback_executed=run_data.get("rollback_executed", False),
            has_hard_regression=run_data.get("has_hard_regression", False),
        )
        if not json_only:
            print(f"Recorded run: root={run_data['root']}")

    result = compute_all_kpis()

    if json_only:
        print(json.dumps(result, indent=2))
    else:
        _display(result)


def _display(result: dict):
    """Human-readable KPI display."""
    print(f"\n=== KPI REPORT ===")
    print(f"  Window: {result['window_size']} runs\n")

    # Lower-is-better KPIs
    LOWER_BETTER = {"threshold_boundary_rate", "fix_dominance", "rollback_rate"}

    for name, kpi in result["kpis"].items():
        value = kpi["value"]
        target = kpi["target"]
        met = kpi["met"]

        if value is None:
            marker = "⏳"
            val_str = "N/A"
        elif met:
            marker = "✅"
            val_str = f"{value:.2%}" if isinstance(value, float) else str(value)
        else:
            marker = "❌"
            val_str = f"{value:.2%}" if isinstance(value, float) else str(value)

        target_dir = "<" if name in LOWER_BETTER else ">"
        target_str = f"{target:.0%}"

        print(f"  {marker} {name}")
        print(f"    value: {val_str}  (target: {target_dir} {target_str})")
        print(f"    {kpi['detail']}")
        print()


if __name__ == "__main__":
    main()
