"""
run_real_eval.py — Real-world validation runner.

Usage:
  python run_real_eval.py [scenarios_dir]
  python run_real_eval.py [scenarios_dir] --with-annotations

Runs scenarios through matcher → debugger → weak signal checks.
If annotations exist, includes human judgment scores in the report.
"""

import json
import sys
from pathlib import Path

VALIDATION_DIR = Path(__file__).parent
ATLAS_DIR = VALIDATION_DIR.parent
DEBUGGER_DIR = ATLAS_DIR.parent / "debugger"

sys.path.insert(0, str(ATLAS_DIR))
sys.path.insert(0, str(DEBUGGER_DIR))


# ---------------------------------------------------------------------------
# Matcher (run all patterns against a log)
# ---------------------------------------------------------------------------

def run_matcher(log: dict) -> list[dict]:
    from matcher import run as matcher_run_file
    import tempfile, os

    patterns_dir = ATLAS_DIR / "failures"
    # Write log to temp file for matcher
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                      delete=False, dir="/tmp") as f:
        json.dump(log, f)
        log_path = f.name

    results = []
    for pattern_path in sorted(patterns_dir.glob("*.yaml")):
        result = matcher_run_file(str(pattern_path), log_path)
        results.append(result)

    os.unlink(log_path)
    return results


# ---------------------------------------------------------------------------
# Debugger
# ---------------------------------------------------------------------------

def run_debugger(matcher_output: list) -> dict:
    from graph_loader import load_graph
    from causal_resolver import resolve
    from formatter import format_output

    graph_path = str(DEBUGGER_DIR / "failure_graph.yaml")
    graph = load_graph(graph_path)
    result = resolve(graph, matcher_output)
    return format_output(result)


# ---------------------------------------------------------------------------
# Explainer (deterministic)
# ---------------------------------------------------------------------------

def run_explainer(debugger_output: dict) -> dict | None:
    try:
        from explainer import explain
        return explain(debugger_output, use_llm=False)
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Weak signal checks (automatic)
# ---------------------------------------------------------------------------

def weak_signal_checks(scenario: dict, matcher_output: list,
                       debugger_output: dict,
                       explainer_output: dict | None) -> dict:
    """Automatic quality checks that don't require human judgment."""

    diagnosed = [r for r in matcher_output if r.get("diagnosed")]
    checks = {}

    # 1. Failure count sanity
    failure_count = len(diagnosed)
    checks["failure_count"] = failure_count
    checks["over_detection_warning"] = failure_count > 5

    # 2. Zero detection check
    checks["zero_detection"] = failure_count == 0

    # 3. Explanation length
    explanation = debugger_output.get("explanation", "")
    checks["explanation_length"] = len(explanation)
    checks["explanation_too_long"] = len(explanation) > 1000

    # 4. Path exists
    primary = debugger_output.get("primary_path")
    checks["has_primary_path"] = primary is not None and len(primary) >= 2

    # 5. Conflicts detected
    checks["conflict_count"] = len(debugger_output.get("conflicts", []))

    # 6. Evidence coverage (if explainer ran)
    if explainer_output:
        validation = explainer_output.get("validation", {})
        checks["explainer_valid"] = validation.get("valid", False)
        checks["explainer_violations"] = len(validation.get("violations", []))

    # 7. Category-specific checks
    category = scenario.get("category", "")
    if category == "clean":
        checks["clean_correct"] = failure_count == 0
    elif category == "false_positive":
        # Should have few or no failures if alignment is high
        alignment = scenario["log"].get("response", {}).get("alignment_score", 0)
        if alignment >= 0.7:
            checks["fp_warning"] = failure_count > 2

    return checks


# ---------------------------------------------------------------------------
# Load annotations
# ---------------------------------------------------------------------------

def load_annotations(annotations_dir: Path) -> dict:
    """Load human annotations keyed by scenario_id."""
    annotations = {}
    for path in annotations_dir.glob("*.json"):
        with open(path, encoding="utf-8") as f:
            ann = json.load(f)
            annotations[ann["scenario_id"]] = ann
    return annotations


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

ERROR_TYPES = {
    "false_positive":    "Failure detected that should not have been",
    "false_negative":    "Expected failure not detected",
    "wrong_root":        "Root cause is incorrect",
    "weak_explanation":  "Explanation is thin or unclear (single-node path)",
    "over_detection":    "Too many failures detected for the scenario",
    "threshold_boundary": "Detection triggered at exact boundary value",
}


def classify_errors(scenario: dict, matcher_output: list,
                    debugger_output: dict) -> list[dict]:
    """Classify potential errors for a scenario. Returns list of error entries."""
    errors = []
    category = scenario.get("category", "")
    log = scenario["log"]
    diagnosed = [r for r in matcher_output if r.get("diagnosed")]
    diagnosed_ids = [r["failure_id"] for r in diagnosed]

    # False positive: clean/false_positive scenario with detections
    alignment = log.get("response", {}).get("alignment_score", 0)
    if category == "clean" and diagnosed:
        for d in diagnosed:
            errors.append({
                "type": "false_positive",
                "failure": d["failure_id"],
                "detail": f"Detected in clean scenario (alignment={alignment})",
            })
    elif category == "false_positive" and alignment >= 0.7 and len(diagnosed) > 1:
        for d in diagnosed:
            errors.append({
                "type": "false_positive",
                "failure": d["failure_id"],
                "detail": f"Detected despite good alignment ({alignment})",
            })

    # Single false positive in false_positive category (borderline)
    if category == "false_positive" and alignment >= 0.7 and len(diagnosed) == 1:
        d = diagnosed[0]
        errors.append({
            "type": "threshold_boundary",
            "failure": d["failure_id"],
            "detail": f"Single detection with good alignment ({alignment}). "
                      f"Confidence={d['confidence']}",
        })

    # Weak explanation: single-node path
    primary = debugger_output.get("primary_path")
    if primary is not None and len(primary) < 2 and diagnosed:
        errors.append({
            "type": "weak_explanation",
            "failure": diagnosed[0]["failure_id"],
            "detail": f"Single-node path, no causal chain. "
                      f"Path={primary}",
        })

    # Over-detection: more than 5 failures
    if len(diagnosed) > 5:
        errors.append({
            "type": "over_detection",
            "detail": f"{len(diagnosed)} failures detected",
            "failures": diagnosed_ids,
        })

    # Threshold boundary: any diagnosed failure with confidence exactly at threshold
    for d in diagnosed:
        if d["confidence"] == d["threshold"]:
            errors.append({
                "type": "threshold_boundary",
                "failure": d["failure_id"],
                "detail": f"Confidence ({d['confidence']}) exactly at threshold ({d['threshold']})",
            })

    return errors


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}

    scenarios_dir = Path(args[0]) if args else VALIDATION_DIR / "scenarios"
    with_annotations = "--with-annotations" in flags
    annotations_dir = VALIDATION_DIR / "annotations"

    annotations = {}
    if with_annotations and annotations_dir.exists():
        annotations = load_annotations(annotations_dir)

    # Load scenarios
    scenarios = []
    for path in sorted(scenarios_dir.glob("*.json")):
        with open(path, encoding="utf-8") as f:
            scenarios.append(json.load(f))

    if not scenarios:
        print(f"No scenarios found in {scenarios_dir}")
        sys.exit(1)

    # Run pipeline
    results = []
    for scenario in scenarios:
        sid = scenario["scenario_id"]
        log = scenario["log"]

        matcher_output = run_matcher(log)
        debugger_output = run_debugger(matcher_output)
        explainer_output = run_explainer(debugger_output)

        diagnosed = [r["failure_id"] for r in matcher_output if r.get("diagnosed")]

        checks = weak_signal_checks(
            scenario, matcher_output, debugger_output, explainer_output
        )

        classified_errors = classify_errors(scenario, matcher_output, debugger_output)

        entry = {
            "scenario_id": sid,
            "category": scenario.get("category", "unknown"),
            "description": scenario.get("description", ""),
            "diagnosed_failures": diagnosed,
            "root_candidates": debugger_output.get("root_candidates", []),
            "primary_path": debugger_output.get("primary_path"),
            "conflicts": debugger_output.get("conflicts", []),
            "explanation": debugger_output.get("explanation", ""),
            "weak_signal_checks": checks,
            "classified_errors": classified_errors,
        }

        # Attach human annotation if available
        if sid in annotations:
            entry["human_annotation"] = annotations[sid]

        results.append(entry)

    # --- Summary ---
    n = len(results)
    detected = sum(1 for r in results if r["diagnosed_failures"])
    zero_det = sum(1 for r in results if not r["diagnosed_failures"])
    avg_failures = sum(len(r["diagnosed_failures"]) for r in results) / n
    over_det = sum(1 for r in results
                   if r["weak_signal_checks"].get("over_detection_warning"))

    summary = {
        "total_scenarios": n,
        "detection": {
            "scenarios_with_failures": detected,
            "scenarios_clean": zero_det,
            "avg_failures_per_scenario": round(avg_failures, 2),
            "over_detection_warnings": over_det,
        },
        "categories": {},
    }

    # Per-category breakdown
    categories = set(r["category"] for r in results)
    for cat in sorted(categories):
        cat_results = [r for r in results if r["category"] == cat]
        cat_detected = sum(1 for r in cat_results if r["diagnosed_failures"])
        summary["categories"][cat] = {
            "count": len(cat_results),
            "detected": cat_detected,
        }

    # Human judgment aggregation
    annotated = [r for r in results if "human_annotation" in r]
    if annotated:
        avg_root = sum(r["human_annotation"]["root_score"] for r in annotated) / len(annotated)
        avg_path = sum(r["human_annotation"]["path_score"] for r in annotated) / len(annotated)
        avg_expl = sum(r["human_annotation"]["explanation_score"] for r in annotated) / len(annotated)
        summary["human_judgment"] = {
            "annotated_count": len(annotated),
            "avg_root_score": round(avg_root, 2),
            "avg_path_score": round(avg_path, 2),
            "avg_explanation_score": round(avg_expl, 2),
        }

    summary["per_scenario"] = results

    # --- Error classification aggregation ---
    all_errors = []
    for r in results:
        for err in r.get("classified_errors", []):
            all_errors.append({
                "scenario_id": r["scenario_id"],
                "category": r["category"],
                **err,
            })

    error_counts = {}
    for err in all_errors:
        t = err["type"]
        error_counts[t] = error_counts.get(t, 0) + 1

    summary["error_classification"] = {
        "total_errors": len(all_errors),
        "by_type": error_counts,
    }

    # Write errors.json
    errors_path = VALIDATION_DIR / "errors.json"
    with open(errors_path, "w", encoding="utf-8") as f:
        json.dump(all_errors, f, indent=2)

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()