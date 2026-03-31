"""
sensitivity_eval.py

Threshold sensitivity analysis for Atlas failure patterns.

For each pattern with numeric thresholds, sweeps the threshold
parameter and measures how detection changes. Identifies unstable
regions where small changes flip the result.

Usage:
  cd llm-failure-atlas
  python evaluation/sensitivity_eval.py
"""

import json
import copy
import tempfile
from pathlib import Path

from llm_failure_atlas.matcher import run as run_matcher
from llm_failure_atlas.resource_loader import get_patterns_dir

FAILURES_DIR = Path(get_patterns_dir())


# Base healthy telemetry
HEALTHY = {
    "input": {"ambiguity_score": 0.3},
    "interaction": {
        "clarification_triggered": False,
        "user_correction_detected": False,
    },
    "reasoning": {
        "replanned": False,
        "hypothesis_count": 1,
    },
    "cache": {
        "hit": True,
        "similarity": 0.9,
        "query_intent_similarity": 0.9,
    },
    "retrieval": {"skipped": False},
    "response": {"alignment_score": 0.8},
    "tools": {
        "call_count": 2,
        "repeat_count": 0,
        "error_count": 0,
    },
    "state": {
        "progress_made": True,
        "any_tool_looping": False,
        "output_produced": True,
        "chain_error_occurred": False,
    },
    "grounding": {
        "tool_provided_data": True,
        "uncertainty_acknowledged": False,
        "response_length": 300,
        "source_data_length": 500,
        "expansion_ratio": 0.6,
    },
}


def sweep_field(pattern_id, field_path, values, base_overrides=None):
    """Sweep a single field through values and track detection."""
    results = []
    for val in values:
        t = copy.deepcopy(HEALTHY)
        if base_overrides:
            for section, overrides in base_overrides.items():
                if section not in t:
                    t[section] = {}
                t[section].update(overrides)

        # Set the swept field
        parts = field_path.split(".")
        node = t
        for part in parts[:-1]:
            if part not in node:
                node[part] = {}
            node = node[part]
        node[parts[-1]] = val

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        json.dump(t, tmp, default=str)
        tmp.close()

        r = run_matcher(
            str(FAILURES_DIR / "{}.yaml".format(pattern_id)), tmp.name
        )
        Path(tmp.name).unlink()
        results.append((val, r["diagnosed"], r["confidence"]))

    return results


def print_sweep(title, field, results):
    """Print sweep results with transition markers."""
    print("\n  {} (sweeping {})".format(title, field))
    prev_diag = None
    for val, diag, conf in results:
        marker = ""
        if prev_diag is not None and diag != prev_diag:
            marker = " ← TRANSITION"
        prev_diag = diag
        print("    {:>8} → diagnosed={:5s} conf={:.2f}{}".format(
            str(val), str(diag), conf, marker
        ))


def main():
    print("=" * 60)
    print("  SENSITIVITY ANALYSIS — Threshold Sweeps")
    print("=" * 60)

    # 1. ambiguity_score for clarification_failure
    #    Threshold: >= 0.7
    results = sweep_field(
        "clarification_failure",
        "input.ambiguity_score",
        [0.3, 0.5, 0.6, 0.65, 0.7, 0.75, 0.8, 0.9],
    )
    print_sweep("clarification_failure", "input.ambiguity_score", results)

    # 2. ambiguity_score for PMC (same threshold but needs user_correction)
    results = sweep_field(
        "premature_model_commitment",
        "input.ambiguity_score",
        [0.3, 0.5, 0.6, 0.65, 0.7, 0.75, 0.8, 0.9],
        base_overrides={
            "interaction": {"user_correction_detected": True},
        },
    )
    print_sweep("premature_model_commitment", "input.ambiguity_score", results)

    # 3. query_intent_similarity for SCIB
    #    Threshold: < 0.55
    results = sweep_field(
        "semantic_cache_intent_bleeding",
        "cache.query_intent_similarity",
        [0.3, 0.4, 0.5, 0.55, 0.6, 0.7, 0.8, 0.9],
        base_overrides={
            "retrieval": {"skipped": True},
            "response": {"alignment_score": 0.4},
        },
    )
    print_sweep("semantic_cache_intent_bleeding", "cache.query_intent_similarity", results)

    # 4. response.alignment_score for incorrect_output
    #    Threshold: < 0.5
    results = sweep_field(
        "incorrect_output",
        "response.alignment_score",
        [0.2, 0.3, 0.4, 0.45, 0.5, 0.55, 0.6, 0.7, 0.8],
        base_overrides={
            "interaction": {"user_correction_detected": True},
        },
    )
    print_sweep("incorrect_output", "response.alignment_score", results)

    # 5. response.alignment_score for rag_retrieval_drift
    #    Threshold: < 0.5
    results = sweep_field(
        "rag_retrieval_drift",
        "response.alignment_score",
        [0.2, 0.3, 0.4, 0.45, 0.5, 0.55, 0.6, 0.7],
        base_overrides={
            "retrieval": {"skipped": True},
            "cache": {"similarity": 0.88},
        },
    )
    print_sweep("rag_retrieval_drift", "response.alignment_score", results)

    # 6. retrieval.expected_coverage for context_truncation_loss
    results = sweep_field(
        "context_truncation_loss",
        "retrieval.expected_coverage",
        [0.2, 0.3, 0.4, 0.45, 0.5, 0.55, 0.6, 0.7],
        base_overrides={
            "context": {"truncated": True, "critical_info_present": True},
        },
    )
    print_sweep("context_truncation_loss", "retrieval.expected_coverage", results)

    print("\n" + "=" * 60)
    print("  TRANSITION = point where detection flips")
    print("  Values near transitions are unstable regions")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()