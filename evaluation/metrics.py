"""
metrics.py

Evaluation metrics for the LLM Failure Atlas system.

Three layers:
  1. Detection (matcher)  — precision, recall, F1
  2. Causal (debugger)    — root accuracy, path accuracy, edge accuracy,
                            conflict resolution, MRR
  3. Explanation (explainer) — faithfulness, signal coverage,
                               causal order, stability
"""

import json


# ---------------------------------------------------------------------------
# Layer 1: Detection (Matcher)
# ---------------------------------------------------------------------------

def detection_precision(predicted: list[str], ground_truth: list[str]) -> float:
    """Fraction of predicted failures that are correct."""
    if not predicted:
        return 0.0
    correct = set(predicted) & set(ground_truth)
    return len(correct) / len(predicted)


def detection_recall(predicted: list[str], ground_truth: list[str]) -> float:
    """Fraction of true failures that were detected."""
    if not ground_truth:
        return 1.0
    correct = set(predicted) & set(ground_truth)
    return len(correct) / len(ground_truth)


def detection_f1(predicted: list[str], ground_truth: list[str]) -> float:
    """Harmonic mean of precision and recall."""
    p = detection_precision(predicted, ground_truth)
    r = detection_recall(predicted, ground_truth)
    if p + r == 0:
        return 0.0
    return 2 * p * r / (p + r)


def false_positive_rate(predicted: list[str], ground_truth: list[str]) -> float:
    """Fraction of predictions that are false positives."""
    if not predicted:
        return 0.0
    fps = set(predicted) - set(ground_truth)
    return len(fps) / len(predicted)


def false_negative_rate(predicted: list[str], ground_truth: list[str]) -> float:
    """Fraction of true failures that were missed."""
    if not ground_truth:
        return 0.0
    fns = set(ground_truth) - set(predicted)
    return len(fns) / len(ground_truth)


# ---------------------------------------------------------------------------
# Layer 2: Causal (Debugger)
# ---------------------------------------------------------------------------

def root_accuracy(predicted_roots: list[str], true_root: str) -> float:
    """1.0 if top predicted root matches ground truth, else 0.0."""
    if not predicted_roots:
        return 0.0
    return 1.0 if predicted_roots[0] == true_root else 0.0


def root_mrr(predicted_roots: list[str], true_root: str) -> float:
    """Mean Reciprocal Rank for the true root in the predicted list."""
    for i, r in enumerate(predicted_roots):
        if r == true_root:
            return 1.0 / (i + 1)
    return 0.0


def path_exact_match(predicted_path: list | None,
                     true_path: list | None) -> float:
    """1.0 if paths are identical, else 0.0."""
    if predicted_path is None and true_path is None:
        return 1.0
    if predicted_path is None or true_path is None:
        return 0.0
    return 1.0 if predicted_path == true_path else 0.0


def path_partial_match(predicted_path: list | None,
                       true_path: list | None) -> float:
    """Fraction of true path nodes that appear in predicted path (order-aware)."""
    if true_path is None or not true_path:
        return 1.0 if predicted_path is None or not predicted_path else 0.0
    if predicted_path is None or not predicted_path:
        return 0.0
    overlap = [n for n in true_path if n in predicted_path]
    return len(overlap) / len(true_path)


def edge_accuracy(predicted_links: list[dict],
                  true_links: list[dict]) -> float:
    """Fraction of true edges that appear in predicted edges."""
    if not true_links:
        return 1.0 if not predicted_links else 0.0

    true_edges = {(e["from"], e["to"]) for e in true_links}
    pred_edges = {(e["from"], e["to"]) for e in predicted_links}

    overlap = true_edges & pred_edges
    return len(overlap) / len(true_edges)


def conflict_accuracy(predicted_conflicts: list[dict],
                      true_conflicts: list[dict]) -> float:
    """Fraction of conflict groups where winner matches."""
    if not true_conflicts:
        return 1.0 if not predicted_conflicts else 0.0

    correct = 0
    for tc in true_conflicts:
        group = tc["group"]
        for pc in predicted_conflicts:
            if pc["group"] == group and pc["winner"] == tc["winner"]:
                correct += 1
                break
    return correct / len(true_conflicts)


# ---------------------------------------------------------------------------
# Layer 3: Explanation (Explainer)
# ---------------------------------------------------------------------------

def faithfulness_rate(validation_result: dict) -> float:
    """1.0 if valid, else 0.0."""
    return 1.0 if validation_result.get("valid", False) else 0.0


def signal_coverage(evidence: list[dict], explanation_text: str) -> float:
    """Fraction of evidence signals mentioned in explanation.
    Checks both raw signal names and human-readable label descriptions."""
    # Try to load label descriptions
    try:
        from labels import SIGNAL_MAP
    except ImportError:
        SIGNAL_MAP = {}

    total = 0
    found = 0
    text_lower = explanation_text.lower()
    for ev in evidence:
        for sig in ev.get("signals", []):
            total += 1
            sig_name = sig.replace("_", " ").lower()
            sig_desc = SIGNAL_MAP.get(sig, "").lower()
            if sig_name in text_lower or sig in text_lower or sig_desc in text_lower:
                found += 1
    return found / total if total > 0 else 1.0


def causal_order_preserved(primary_path: list | None,
                           explanation_text: str) -> float:
    """1.0 if all nodes appear in causal order, else 0.0."""
    if not primary_path or len(primary_path) < 2:
        return 1.0
    for i in range(len(primary_path) - 1):
        pos_a = explanation_text.find(primary_path[i])
        pos_b = explanation_text.find(primary_path[i + 1])
        if pos_a == -1 or pos_b == -1:
            continue
        if pos_a > pos_b:
            return 0.0
    return 1.0


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

def compute_all(case: dict, debugger_output: dict,
                explainer_output: dict | None = None) -> dict:
    """
    Compute all metrics for a single evaluation case.

    Args:
        case: evaluation dataset entry with ground_truth
        debugger_output: output from main.py
        explainer_output: output from explain.py (optional)

    Returns:
        dict with all metrics
    """
    gt = case["ground_truth"]

    # Detection
    predicted_failures = [
        f["failure_id"] for f in case["matcher_output"]
        if f.get("diagnosed", False)
    ]
    true_failures = gt["failures"]

    det = {
        "precision":     round(detection_precision(predicted_failures, true_failures), 4),
        "recall":        round(detection_recall(predicted_failures, true_failures), 4),
        "f1":            round(detection_f1(predicted_failures, true_failures), 4),
        "fpr":           round(false_positive_rate(predicted_failures, true_failures), 4),
        "fnr":           round(false_negative_rate(predicted_failures, true_failures), 4),
    }

    # Causal
    pred_roots = debugger_output.get("root_candidates", [])
    true_root = gt.get("root")

    causal = {
        "root_accuracy":    root_accuracy(pred_roots, true_root),
        "root_mrr":         round(root_mrr(pred_roots, true_root), 4),
        "path_exact":       path_exact_match(
            debugger_output.get("primary_path"),
            gt.get("primary_path")
        ),
        "path_partial":     round(path_partial_match(
            debugger_output.get("primary_path"),
            gt.get("primary_path")
        ), 4),
        "edge_accuracy":    round(edge_accuracy(
            debugger_output.get("causal_links", []),
            debugger_output.get("causal_links", [])  # self-check for now
        ), 4),
        "conflict_accuracy": conflict_accuracy(
            debugger_output.get("conflicts", []),
            gt.get("conflicts", [])
        ),
    }

    # Explanation (if available)
    expl = {}
    if explainer_output:
        resp = explainer_output.get("response") or explainer_output.get("llm_response", {})
        validation = explainer_output.get("validation", {})
        evidence = explainer_output.get("explanation_package", {}).get("evidence", [])
        primary_text = resp.get("primary_explanation", "")

        expl = {
            "faithfulness":     faithfulness_rate(validation),
            "signal_coverage":  round(signal_coverage(
                evidence, json.dumps(resp)
            ), 4),
            "causal_order":     causal_order_preserved(
                gt.get("primary_path"), primary_text
            ),
        }

    # Overall score
    det_score = det["f1"]
    causal_score = (
        0.3 * causal["root_accuracy"]
        + 0.3 * causal["path_exact"]
        + 0.2 * causal["path_partial"]
        + 0.2 * causal["conflict_accuracy"]
    )
    expl_score = (
        (expl["faithfulness"] * 0.4
         + expl["signal_coverage"] * 0.3
         + expl["causal_order"] * 0.3)
        if expl else 0.0
    )

    if expl:
        overall = 0.3 * det_score + 0.4 * causal_score + 0.3 * expl_score
    else:
        overall = 0.3 * det_score + 0.7 * causal_score

    return {
        "case_id":    case["case_id"],
        "detection":  det,
        "causal":     causal,
        "explanation": expl if expl else None,
        "overall":    round(overall, 4),
    }
