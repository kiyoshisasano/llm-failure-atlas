"""
run_eval.py — Evaluation runner for LLM Failure Atlas.

Usage:
  python run_eval.py [dataset_dir] [--with-explainer]

Runs all evaluation cases through matcher → debugger → (optionally) explainer,
computes metrics, and outputs a summary report.
"""

import json
import sys
import os
from pathlib import Path

# Add parent dirs to path for imports
EVAL_DIR = Path(__file__).parent
ATLAS_DIR = EVAL_DIR.parent
DEBUGGER_DIR = ATLAS_DIR.parent / "debugger"

sys.path.insert(0, str(ATLAS_DIR))
sys.path.insert(0, str(DEBUGGER_DIR))

from metrics import compute_all


def load_dataset(dataset_dir: str) -> list[dict]:
    """Load all evaluation cases from a directory."""
    cases = []
    for path in sorted(Path(dataset_dir).glob("*.json")):
        with open(path) as f:
            cases.append(json.load(f))
    return cases


def run_debugger(matcher_output: list, graph_path: str) -> dict:
    """Run the debugger pipeline on matcher output."""
    from graph_loader import load_graph
    from causal_resolver import resolve
    from formatter import format_output

    graph = load_graph(graph_path)
    result = resolve(graph, matcher_output)
    return format_output(result)


def run_explainer(debugger_output: dict) -> dict | None:
    """Run the explainer in deterministic mode (no LLM)."""
    try:
        from explainer import explain
        return explain(debugger_output, use_llm=False)
    except ImportError:
        return None


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}

    dataset_dir = args[0] if args else str(EVAL_DIR / "dataset")
    graph_path = str(DEBUGGER_DIR / "failure_graph.yaml")
    with_explainer = "--with-explainer" in flags

    cases = load_dataset(dataset_dir)
    if not cases:
        print(f"No evaluation cases found in {dataset_dir}")
        sys.exit(1)

    results = []
    for case in cases:
        debugger_output = run_debugger(case["matcher_output"], graph_path)

        explainer_output = None
        if with_explainer:
            explainer_output = run_explainer(debugger_output)

        metrics = compute_all(case, debugger_output, explainer_output)
        results.append(metrics)

    # --- Summary ---
    n = len(results)

    # Aggregate scores
    avg_det = sum(r["detection"]["f1"] for r in results) / n
    avg_causal_root = sum(r["causal"]["root_accuracy"] for r in results) / n
    avg_causal_path = sum(r["causal"]["path_exact"] for r in results) / n
    avg_causal_partial = sum(r["causal"]["path_partial"] for r in results) / n
    avg_conflict = sum(r["causal"]["conflict_accuracy"] for r in results) / n
    avg_overall = sum(r["overall"] for r in results) / n

    expl_results = [r for r in results if r["explanation"]]
    if expl_results:
        avg_faith = sum(r["explanation"]["faithfulness"] for r in expl_results) / len(expl_results)
        avg_sig = sum(r["explanation"]["signal_coverage"] for r in expl_results) / len(expl_results)
        avg_order = sum(r["explanation"]["causal_order"] for r in expl_results) / len(expl_results)
    else:
        avg_faith = avg_sig = avg_order = None

    summary = {
        "dataset_size": n,
        "aggregate": {
            "detection_f1":          round(avg_det, 4),
            "root_accuracy":         round(avg_causal_root, 4),
            "path_exact_match":      round(avg_causal_path, 4),
            "path_partial_match":    round(avg_causal_partial, 4),
            "conflict_accuracy":     round(avg_conflict, 4),
            "overall_score":         round(avg_overall, 4),
        },
        "per_case": results,
    }

    if avg_faith is not None:
        summary["aggregate"]["explanation_faithfulness"] = round(avg_faith, 4)
        summary["aggregate"]["signal_coverage"] = round(avg_sig, 4)
        summary["aggregate"]["causal_order"] = round(avg_order, 4)

    # Error analysis: find failures
    errors = []
    for r in results:
        issues = []
        if r["causal"]["root_accuracy"] < 1.0:
            issues.append("wrong_root")
        if r["causal"]["path_exact"] < 1.0:
            issues.append("wrong_path")
        if r["causal"]["conflict_accuracy"] < 1.0:
            issues.append("wrong_conflict")
        if r["detection"]["f1"] < 1.0:
            issues.append("detection_error")
        if issues:
            errors.append({"case_id": r["case_id"], "issues": issues})

    if errors:
        summary["errors"] = errors

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
