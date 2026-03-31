"""
run_calibration.py — SCIB threshold calibration via grid search.
Includes regression check against existing examples.
"""

import json
import sys
import os
import copy
import yaml
import tempfile
from pathlib import Path

CALIBRATION_DIR = Path(__file__).parent
ATLAS_DIR = CALIBRATION_DIR.parent
DEBUGGER_DIR = ATLAS_DIR.parent / "debugger"
VALIDATION_DIR = ATLAS_DIR / "validation"

sys.path.insert(0, str(ATLAS_DIR))
sys.path.insert(0, str(DEBUGGER_DIR))

SCIB_PATH = ATLAS_DIR / "failures" / "semantic_cache_intent_bleeding.yaml"

# Examples that require SCIB to be diagnosed (regression constraint)
SCIB_REQUIRED_EXAMPLES = [
    "simple", "branching", "competing", "multi_root",
    "closed_graph", "decompose", "three_way_conflict",
]


def load_baseline():
    with open(SCIB_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_pattern(pattern):
    with open(SCIB_PATH, "w", encoding="utf-8") as f:
        yaml.dump(pattern, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def apply_config(baseline, config):
    pattern = copy.deepcopy(baseline)
    intent_t = config.get("intent_threshold")
    if intent_t is not None:
        for rule in pattern["signal_extraction"]["rules"]:
            if rule["signal"] == "cache_query_intent_mismatch":
                rule["rule"] = f"cache.query_intent_similarity < {intent_t}"

    for mod in pattern["diagnosis"].get("evidence_modifiers", []):
        if mod["signal"] == "cache_query_intent_mismatch" and "s1_weight" in config:
            mod["add"] = config["s1_weight"]
        if mod["signal"] == "retrieval_skipped_after_cache_hit" and "s2_weight" in config:
            mod["add"] = config["s2_weight"]
    for mod in pattern["diagnosis"].get("symptom_modifiers", []):
        if mod["signal"] == "retrieved_docs_low_intent_alignment" and "s3_weight" in config:
            mod["add"] = config["s3_weight"]

    if "diagnosis_threshold" in config:
        pattern["diagnosis"]["threshold"] = config["diagnosis_threshold"]
    return pattern


def check_regression():
    """Check that SCIB is still diagnosed in all required examples."""
    from matcher import run as matcher_run
    for case in SCIB_REQUIRED_EXAMPLES:
        log_path = str(ATLAS_DIR / "examples" / case / "log.json")
        r = matcher_run(str(SCIB_PATH), log_path)
        if not r["diagnosed"]:
            return False
    return True


def run_eval_suite():
    from matcher import run as matcher_run
    scenarios_dir = VALIDATION_DIR / "scenarios"
    annotations_dir = VALIDATION_DIR / "annotations"

    annotations = {}
    for path in annotations_dir.glob("*.json"):
        ann = json.load(open(path, encoding="utf-8"))
        annotations[ann["scenario_id"]] = ann

    total = 0
    false_positives = 0
    threshold_boundary = 0
    root_score_sum = 0
    annotated_count = 0

    for spath in sorted(scenarios_dir.glob("*.json")):
        scenario = json.load(open(spath, encoding="utf-8"))
        sid = scenario["scenario_id"]
        category = scenario.get("category", "")
        log = scenario["log"]
        alignment = log.get("response", {}).get("alignment_score", 0)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(log, f)
            log_path = f.name

        matcher_results = []
        for pp in sorted((ATLAS_DIR / "failures").glob("*.yaml")):
            r = matcher_run(str(pp), log_path)
            matcher_results.append(r)
        os.unlink(log_path)

        diagnosed_ids = [r["failure_id"] for r in matcher_results if r.get("diagnosed")]
        total += 1

        if category in ("clean", "false_positive") and alignment >= 0.7:
            if "semantic_cache_intent_bleeding" in diagnosed_ids:
                false_positives += 1

        for d in matcher_results:
            if d.get("diagnosed") and d["confidence"] == d["threshold"]:
                threshold_boundary += 1

        if sid in annotations:
            annotated_count += 1
            root_score_sum += annotations[sid]["root_score"]

    avg_root = root_score_sum / annotated_count if annotated_count else 0
    return {
        "total": total,
        "false_positives": false_positives,
        "threshold_boundary": threshold_boundary,
        "avg_root_score": round(avg_root, 4),
    }


def score_config(metrics, regression_pass):
    if not regression_pass:
        return -1.0
    fp_rate = metrics["false_positives"] / max(metrics["total"], 1)
    boundary_rate = metrics["threshold_boundary"] / max(metrics["total"], 1)
    root_quality = metrics["avg_root_score"] / 2.0
    return round(0.4 * root_quality + 0.3 * (1 - fp_rate) + 0.3 * (1 - boundary_rate), 4)


def grid_search():
    baseline = load_baseline()
    baseline_copy = copy.deepcopy(baseline)

    configs = []
    for intent_t in [0.55, 0.6, 0.65, 0.7, 0.75]:
        for s1_w in [0.2, 0.3, 0.4]:
            for s2_w in [0.3, 0.4]:
                for diag_t in [0.6, 0.65, 0.7]:
                    configs.append({
                        "intent_threshold": intent_t,
                        "s1_weight": s1_w,
                        "s2_weight": s2_w,
                        "s3_weight": 0.3,
                        "diagnosis_threshold": diag_t,
                    })

    print(f"Grid search: {len(configs)} configurations", file=sys.stderr)

    # Baseline
    metrics_bl = run_eval_suite()
    reg_bl = check_regression()
    score_bl = score_config(metrics_bl, reg_bl)
    print(f"Baseline: score={score_bl} fp={metrics_bl['false_positives']} "
          f"boundary={metrics_bl['threshold_boundary']} regression={'PASS' if reg_bl else 'FAIL'}", file=sys.stderr)

    best_score = score_bl
    best_config = None
    best_metrics = metrics_bl
    all_results = []

    for i, config in enumerate(configs):
        pattern = apply_config(baseline_copy, config)
        save_pattern(pattern)

        reg = check_regression()
        if not reg:
            all_results.append({"config": config, "score": -1, "regression": False})
            if (i + 1) % 20 == 0:
                print(f"  {i+1}/{len(configs)}...", file=sys.stderr)
            continue

        metrics = run_eval_suite()
        score = score_config(metrics, True)
        all_results.append({"config": config, "metrics": metrics, "score": score, "regression": True})

        if score > best_score:
            best_score = score
            best_config = config
            best_metrics = metrics

        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(configs)}...", file=sys.stderr)

    save_pattern(baseline_copy)
    passed = [r for r in all_results if r.get("regression")]
    passed.sort(key=lambda x: x["score"], reverse=True)

    output = {
        "baseline": {"metrics": metrics_bl, "score": score_bl, "regression": reg_bl},
        "best": {
            "config": best_config,
            "metrics": best_metrics,
            "score": best_score,
            "improvement": round(best_score - score_bl, 4),
        } if best_config else {"note": "no improvement over baseline"},
        "regression_stats": {
            "total": len(configs),
            "passed": len(passed),
            "failed": len(configs) - len(passed),
        },
        "top_5": passed[:5],
    }

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    grid_search()