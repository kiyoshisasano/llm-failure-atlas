"""
update_policy.py

Phase 19: Self-Improving Loop (suggestion-only).

Reads evaluation results and updates learning stores:
  1. calibration_history.json — tracks which thresholds worked
  2. fix_effectiveness.json  — tracks which fixes were effective
  3. suggestions.json        — actionable recommendations

NEVER modifies patterns, graph, or fix templates directly.

Usage:
  python update_policy.py <errors.json> <evaluate_fix_report.json>
  python update_policy.py <errors.json> <evaluate_fix_report.json> --suggest
"""

import json
import sys
from pathlib import Path
from datetime import datetime

LEARNING_DIR = Path(__file__).parent

CALIBRATION_PATH = LEARNING_DIR / "calibration_history.json"
FIX_EFFECTIVENESS_PATH = LEARNING_DIR / "fix_effectiveness.json"
SUGGESTIONS_PATH = LEARNING_DIR / "suggestions.json"


# ---------------------------------------------------------------------------
# Store I/O
# ---------------------------------------------------------------------------

def _load_store(path: Path) -> dict:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_store(path: Path, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# 1. Calibration history update
# ---------------------------------------------------------------------------

def update_calibration_history(errors: list[dict]) -> dict:
    """
    Track error patterns over time.
    If a failure accumulates threshold_boundary or false_positive errors,
    mark it as a recalibration candidate.
    """
    store = _load_store(CALIBRATION_PATH)
    timestamp = datetime.now().isoformat()

    # Count errors by failure
    error_counts = {}
    for err in errors:
        fid = err.get("failure", err.get("scenario_id", "unknown"))
        etype = err["type"]
        key = f"{fid}:{etype}"
        error_counts[key] = error_counts.get(key, 0) + 1

    # Update history
    if "runs" not in store:
        store["runs"] = []

    store["runs"].append({
        "timestamp": timestamp,
        "error_counts": error_counts,
        "total_errors": len(errors),
    })

    # Keep last 20 runs
    store["runs"] = store["runs"][-20:]

    # Detect recalibration candidates: failures with repeated errors
    failure_error_history = {}
    for run in store["runs"]:
        for key, count in run["error_counts"].items():
            fid = key.split(":")[0]
            if fid not in failure_error_history:
                failure_error_history[fid] = 0
            failure_error_history[fid] += count

    candidates = []
    for fid, total in failure_error_history.items():
        if total >= 3:
            candidates.append({
                "failure": fid,
                "total_errors": total,
                "priority": "high" if total >= 5 else "medium",
            })

    candidates.sort(key=lambda x: x["total_errors"], reverse=True)
    store["recalibration_candidates"] = candidates

    _save_store(CALIBRATION_PATH, store)
    return store


# ---------------------------------------------------------------------------
# 2. Fix effectiveness update
# ---------------------------------------------------------------------------

def update_fix_effectiveness(eval_report: dict) -> dict:
    """
    Track fix outcomes: keep / review / rollback per failure × fix_type.
    """
    store = _load_store(FIX_EFFECTIVENESS_PATH)
    timestamp = datetime.now().isoformat()

    decision = eval_report.get("decision", "unknown")
    mitigated = eval_report.get("delta", {}).get("mitigated_failures", [])
    remaining = eval_report.get("delta", {}).get("remaining_failures", [])

    # We need to know which fix was applied — reconstruct from before/after
    before_ids = set(eval_report.get("before", {}).get("failure_ids", []))
    mitigated_set = set(mitigated)

    # For each mitigated failure, credit the fix
    for fid in mitigated:
        if fid not in store:
            store[fid] = {}

        # Determine fix_type from AUTOFIX_MAP if available
        try:
            from fix_templates import AUTOFIX_MAP
            fix_type = AUTOFIX_MAP.get(fid, {}).get("fix_type", "unknown")
        except ImportError:
            fix_type = "unknown"

        if fix_type not in store[fid]:
            store[fid][fix_type] = {
                "attempts": 0,
                "keep": 0,
                "review": 0,
                "rollback": 0,
                "total_mitigated": 0,
                "history": [],
            }

        entry = store[fid][fix_type]
        entry["attempts"] += 1
        if decision in ("keep", "review", "rollback"):
            entry[decision] = entry.get(decision, 0) + 1
        entry["total_mitigated"] += len(mitigated)

        # Compute effectiveness score
        attempts = entry["attempts"]
        if attempts > 0:
            keep_rate = entry["keep"] / attempts
            entry["effectiveness_score"] = round(
                0.6 * keep_rate
                + 0.3 * (1.0 - entry.get("rollback", 0) / attempts)
                + 0.1 * min(entry["total_mitigated"] / max(attempts, 1) / 5, 1.0),
                4
            )

        entry["history"].append({
            "timestamp": timestamp,
            "decision": decision,
            "mitigated_count": len(mitigated),
        })
        # Keep last 50 entries
        entry["history"] = entry["history"][-50:]

    _save_store(FIX_EFFECTIVENESS_PATH, store)
    return store


# ---------------------------------------------------------------------------
# 3. Generate suggestions
# ---------------------------------------------------------------------------

def generate_suggestions(calibration: dict, effectiveness: dict) -> dict:
    """
    Produce actionable suggestions based on learning stores.
    These are SUGGESTIONS ONLY — never auto-applied.
    """
    suggestions = {
        "generated_at": datetime.now().isoformat(),
        "recalibration": [],
        "fix_ranking_updates": [],
        "safety_promotions": [],
    }

    # A. Recalibration suggestions
    for candidate in calibration.get("recalibration_candidates", []):
        suggestions["recalibration"].append({
            "failure": candidate["failure"],
            "action": f"Re-run calibration for {candidate['failure']}",
            "reason": f"{candidate['total_errors']} errors accumulated across runs",
            "priority": candidate["priority"],
        })

    # B. Fix ranking: promote highly effective fixes
    for fid, fix_types in effectiveness.items():
        for fix_type, data in fix_types.items():
            score = data.get("effectiveness_score", 0)
            attempts = data.get("attempts", 0)
            if attempts >= 3 and score >= 0.8:
                suggestions["fix_ranking_updates"].append({
                    "failure": fid,
                    "fix_type": fix_type,
                    "action": "Consider boosting priority in decision_support",
                    "reason": f"effectiveness={score} over {attempts} attempts",
                })

    # C. Safety promotions: medium → high if consistently successful
    for fid, fix_types in effectiveness.items():
        for fix_type, data in fix_types.items():
            attempts = data.get("attempts", 0)
            rollbacks = data.get("rollback", 0)
            if attempts >= 5 and rollbacks == 0:
                try:
                    from fix_templates import AUTOFIX_MAP
                    current_safety = AUTOFIX_MAP.get(fid, {}).get("safety")
                    if current_safety == "medium":
                        suggestions["safety_promotions"].append({
                            "failure": fid,
                            "fix_type": fix_type,
                            "action": f"Consider promoting {fid} safety from medium to high",
                            "reason": f"{attempts} attempts with 0 rollbacks",
                        })
                except ImportError:
                    pass

    _save_store(SUGGESTIONS_PATH, suggestions)
    return suggestions


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def update(errors_path: str, eval_report_path: str) -> dict:
    """Full learning cycle: read inputs → update stores → generate suggestions."""
    with open(errors_path, encoding="utf-8") as f:
        errors = json.load(f)
    with open(eval_report_path, encoding="utf-8") as f:
        eval_report = json.load(f)

    calibration = update_calibration_history(errors)
    effectiveness = update_fix_effectiveness(eval_report)
    suggestions = generate_suggestions(calibration, effectiveness)

    return {
        "calibration_candidates": len(calibration.get("recalibration_candidates", [])),
        "effectiveness_entries": sum(
            len(ft) for ft in effectiveness.values()
        ),
        "suggestions": {
            "recalibration": len(suggestions["recalibration"]),
            "fix_ranking": len(suggestions["fix_ranking_updates"]),
            "safety_promotions": len(suggestions["safety_promotions"]),
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}

    if len(args) < 2:
        print("Usage: python update_policy.py errors.json evaluate_fix_report.json [--suggest]")
        sys.exit(1)

    errors_path = args[0]
    eval_report_path = args[1]
    show_suggest = "--suggest" in flags

    # Add debugger to path for fix_templates import
    import os
    debugger_dir = str(Path(__file__).parent.parent.parent / "debugger")
    if debugger_dir not in sys.path:
        sys.path.insert(0, debugger_dir)

    result = update(errors_path, eval_report_path)

    if show_suggest:
        suggestions = _load_store(SUGGESTIONS_PATH)
        print(json.dumps(suggestions, indent=2))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()