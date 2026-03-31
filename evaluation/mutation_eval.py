"""
mutation_eval.py

Mutation testing for Atlas failure patterns.

Takes healthy (clean) telemetry scenarios and applies mutations that
should trigger specific failure patterns. Measures detection rate
per pattern.

Mutation operators:
  Each operator transforms a healthy trace into one that should trigger
  a specific failure. If the matcher detects the expected pattern,
  the mutation is "killed" (detected). If not, it "survived" (missed).

Usage:
  cd llm-failure-atlas
  python evaluation/mutation_eval.py

No API keys required. Uses only existing matcher and pattern files.
"""

import json
import sys
import copy
import tempfile
from pathlib import Path
from collections import defaultdict

ATLAS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ATLAS_ROOT))

from matcher import run as run_matcher

FAILURES_DIR = ATLAS_ROOT / "failures"


# ---------------------------------------------------------------------------
# Base healthy telemetry (used as seed for mutations)
# ---------------------------------------------------------------------------

HEALTHY_BASE = {
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
        "hit": False,
        "similarity": 0.3,
        "query_intent_similarity": 0.9,
    },
    "retrieval": {
        "skipped": False,
        "expected_coverage": 0.7,
        "contains_instruction": False,
        "override_detected": False,
        "adversarial_score": 0.1,
    },
    "context": {
        "truncated": False,
        "critical_info_present": True,
    },
    "response": {"alignment_score": 0.8},
    "tools": {
        "call_count": 2,
        "repeat_count": 0,
        "error_count": 0,
        "soft_error_count": 0,
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
    "output": {
        "repair_attempted": False,
        "regenerated": True,
        "repair_quality": 0.8,
    },
}


# ---------------------------------------------------------------------------
# Mutation operators: each returns (mutated_trace, expected_failure_id)
# ---------------------------------------------------------------------------

def mutate_tool_loop(base):
    """Inject tool call loop: repeated calls with no progress."""
    t = copy.deepcopy(base)
    t["tools"]["repeat_count"] = 4
    t["tools"]["call_count"] = 5
    t["state"]["any_tool_looping"] = True
    t["state"]["progress_made"] = False
    t["reasoning"]["replanned"] = False
    return t, "agent_tool_call_loop"


def mutate_clarification_failure(base):
    """Inject clarification failure: high ambiguity, no clarification."""
    t = copy.deepcopy(base)
    t["input"]["ambiguity_score"] = 0.8
    t["interaction"]["clarification_triggered"] = False
    t["reasoning"]["hypothesis_count"] = 1
    return t, "clarification_failure"


def mutate_incorrect_output(base):
    """Inject incorrect output: low alignment + user correction."""
    t = copy.deepcopy(base)
    t["response"]["alignment_score"] = 0.3
    t["interaction"]["user_correction_detected"] = True
    return t, "incorrect_output"


def mutate_pmc(base):
    """Inject premature model commitment: high ambiguity, no branching,
    user corrected but no replanning."""
    t = copy.deepcopy(base)
    t["input"]["ambiguity_score"] = 0.75
    t["reasoning"]["hypothesis_count"] = 1
    t["reasoning"]["replanned"] = False
    t["interaction"]["clarification_triggered"] = False
    t["interaction"]["user_correction_detected"] = True
    return t, "premature_model_commitment"


def mutate_assumption_invalidation(base):
    """Inject assumption invalidation: contradiction ignored, single hypothesis."""
    t = copy.deepcopy(base)
    t["reasoning"]["hypothesis_count"] = 1
    t["reasoning"]["contradiction_detected"] = True
    t["reasoning"]["hypothesis_abandoned"] = False
    return t, "assumption_invalidation_failure"


def mutate_scib(base):
    """Inject semantic cache intent bleeding: cache hit with intent mismatch,
    retrieval skipped, low alignment."""
    t = copy.deepcopy(base)
    t["cache"]["hit"] = True
    t["cache"]["similarity"] = 0.92
    t["cache"]["query_intent_similarity"] = 0.4
    t["retrieval"]["skipped"] = True
    t["response"]["alignment_score"] = 0.4
    return t, "semantic_cache_intent_bleeding"


def mutate_retrieval_drift(base):
    """Inject RAG retrieval drift: cache-mediated bypass with low alignment."""
    t = copy.deepcopy(base)
    t["retrieval"]["skipped"] = True
    t["retrieval"]["expected_coverage"] = 0.3
    t["cache"]["hit"] = True
    t["cache"]["similarity"] = 0.88
    t["response"]["alignment_score"] = 0.4
    return t, "rag_retrieval_drift"


def mutate_prompt_injection(base):
    """Inject prompt injection via retrieval."""
    t = copy.deepcopy(base)
    t["retrieval"]["contains_instruction"] = True
    t["retrieval"]["override_detected"] = True
    t["retrieval"]["adversarial_score"] = 0.8
    return t, "prompt_injection_via_retrieval"


def mutate_context_truncation(base):
    """Inject context truncation loss: truncated with critical info lost."""
    t = copy.deepcopy(base)
    t["context"]["truncated"] = True
    t["context"]["critical_info_present"] = True  # was present = was lost
    t["retrieval"]["expected_coverage"] = 0.3
    return t, "context_truncation_loss"


def mutate_instruction_priority(base):
    """Inject instruction priority inversion."""
    t = copy.deepcopy(base)
    t["instruction"] = {
        "system_priority_respected": False,
    }
    t["context"]["external_instruction_weight"] = 0.85
    return t, "instruction_priority_inversion"


def mutate_repair_strategy(base):
    """Inject repair strategy failure."""
    t = copy.deepcopy(base)
    t["output"]["repair_attempted"] = True
    t["output"]["regenerated"] = False
    t["output"]["repair_quality"] = 0.3
    return t, "repair_strategy_failure"


def mutate_premature_termination(base):
    """Inject premature termination: no output, no error."""
    t = copy.deepcopy(base)
    t["state"]["output_produced"] = False
    t["state"]["chain_error_occurred"] = False
    t["tools"]["call_count"] = 3
    return t, "premature_termination"


def mutate_failed_termination(base):
    """Inject failed termination: error, no output."""
    t = copy.deepcopy(base)
    t["state"]["chain_error_occurred"] = True
    t["state"]["output_produced"] = False
    return t, "failed_termination"


# Mutations that cannot be tested (adapter doesn't produce required fields):
# - tool_result_misinterpretation (needs tool.output_valid, state.updated_correctly)
# Meta patterns are excluded (they fire on meta conditions, not domain signals)

MUTATIONS = [
    mutate_tool_loop,
    mutate_clarification_failure,
    mutate_incorrect_output,
    mutate_pmc,
    mutate_assumption_invalidation,
    mutate_scib,
    mutate_retrieval_drift,
    mutate_prompt_injection,
    mutate_context_truncation,
    mutate_instruction_priority,
    mutate_repair_strategy,
    mutate_premature_termination,
    mutate_failed_termination,
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_mutation(mutated, expected_failure_id):
    """Run matcher against mutated trace, check if expected failure fires."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    )
    json.dump(mutated, tmp, default=str)
    tmp.close()

    pattern_file = FAILURES_DIR / "{}.yaml".format(expected_failure_id)
    if not pattern_file.exists():
        Path(tmp.name).unlink()
        return {"status": "SKIP", "reason": "pattern file not found"}

    result = run_matcher(str(pattern_file), tmp.name)
    Path(tmp.name).unlink()

    return {
        "status": "KILLED" if result["diagnosed"] else "SURVIVED",
        "confidence": result["confidence"],
        "signals": result["signals"],
    }


def run_false_positive_check(healthy):
    """Run all patterns against healthy trace, check for false positives."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    )
    json.dump(healthy, tmp, default=str)
    tmp.close()

    false_positives = []
    for pf in sorted(FAILURES_DIR.glob("*.yaml")):
        result = run_matcher(str(pf), tmp.name)
        if result.get("diagnosed"):
            false_positives.append(
                (result["failure_id"], result["confidence"])
            )

    Path(tmp.name).unlink()
    return false_positives


def main():
    print("\n" + "=" * 60)
    print("  MUTATION TESTING — Atlas Failure Patterns")
    print("=" * 60)

    # Step 1: Verify healthy base produces no failures
    print("\n--- Baseline check (healthy trace) ---")
    fps = run_false_positive_check(HEALTHY_BASE)
    if fps:
        print("  WARNING: Healthy base triggers: {}".format(fps))
    else:
        print("  OK: Healthy base produces 0 failures")

    # Step 2: Run all mutations
    print("\n--- Mutation results ---")
    results = defaultdict(list)
    killed = 0
    survived = 0
    skipped = 0

    for mutate_fn in MUTATIONS:
        mutated, expected = mutate_fn(HEALTHY_BASE)
        result = run_mutation(mutated, expected)

        status = result["status"]
        conf = result.get("confidence", 0)

        if status == "KILLED":
            killed += 1
            mark = "✓"
        elif status == "SURVIVED":
            survived += 1
            mark = "✗"
        else:
            skipped += 1
            mark = "—"

        results[expected].append(result)
        print("  {} {:40s} {} (conf={:.2f})".format(
            mark, expected, status, conf
        ))

    # Step 3: Summary
    total = killed + survived
    score = killed / total * 100 if total > 0 else 0

    print("\n" + "=" * 60)
    print("  MUTATION SCORE")
    print("=" * 60)
    print("  Killed:   {} / {}".format(killed, total))
    print("  Survived: {} / {}".format(survived, total))
    print("  Skipped:  {}".format(skipped))
    print("  Score:    {:.1f}%".format(score))
    print()

    # Step 4: Per-pattern breakdown
    print("  Pattern coverage:")
    all_patterns = set()
    for pf in FAILURES_DIR.glob("*.yaml"):
        all_patterns.add(pf.stem)

    tested = set(results.keys())
    untested = all_patterns - tested
    # Exclude meta patterns from untested
    meta = {"unmodeled_failure", "insufficient_observability",
            "conflicting_signals"}
    untested -= meta

    for pattern in sorted(tested):
        statuses = [r["status"] for r in results[pattern]]
        print("    {:40s} {}".format(
            pattern, " ".join(statuses)
        ))

    if untested:
        print("\n  Untested patterns (no mutation operator):")
        for p in sorted(untested):
            print("    {}".format(p))

    print()


if __name__ == "__main__":
    main()