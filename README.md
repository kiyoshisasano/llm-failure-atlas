# LLM Failure Atlas

A graph-based failure modeling system for LLM agent runtimes.

Failures are nodes. Relationships between failures are edges. The system is defined as a causal graph.

---

## Related Repositories

| Repository | Role |
|---|---|
| [agent-failure-debugger](https://github.com/kiyoshisasano/agent-failure-debugger) | Consumes matcher output + this graph → causal diagnosis, fix generation, auto-apply |
| [agent-pld-metrics (PLD)](https://github.com/kiyoshisasano/agent-pld-metrics) | Behavioral stability framework this Atlas applies to |

---

## Purpose

The Atlas defines:

- **What failures exist** — 12 failure patterns across 5 layers
- **How they relate causally** — a directed graph with 12 edges
- **How to detect them** — signal-based pattern matching (22 signals)
- **How to measure system health** — 6 operational KPIs

LLM systems fail in structured, repeatable ways. The Atlas makes those structures explicit and machine-readable.

---

## Quickstart

```bash
git clone https://github.com/kiyoshisasano/llm-failure-atlas.git
cd llm-failure-atlas
pip install -r requirements.txt
```

Detect failures from a sample log:

```bash
# Run matcher on sample log
python matcher.py examples/simple/log.json

# Pass result to debugger (separate repository)
python ../agent-failure-debugger/pipeline.py examples/simple/matcher_output.json --use-learning

# Measure KPIs
python compute_kpi.py
```

---

## Execution Pipeline

```
log
  → signals (pattern extraction)
  → failure detection (matcher)
  → failure graph (Atlas)
  → causal interpretation + fix (debugger)
```

The Atlas provides the **structure and detection**. The [debugger](https://github.com/kiyoshisasano/agent-failure-debugger) provides interpretation, explanation, fix generation, and auto-apply.

---

## Core Idea

Failures are not independent. The same downstream failure (e.g. `rag_retrieval_drift`) can be caused by:

- Cache misuse (`semantic_cache_intent_bleeding`)
- Adversarial retrieval (`prompt_injection_via_retrieval`)
- Context window overflow (`context_truncation_loss`)

The Atlas makes these **competing causal paths explicit**.

---

## Causal Graph

```
clarification_failure
  → assumption_invalidation_failure
    → premature_model_commitment
      ├─ semantic_cache_intent_bleeding → rag_retrieval_drift → incorrect_output
      ├─ prompt_injection_via_retrieval → rag_retrieval_drift → incorrect_output
      ├─ agent_tool_call_loop → tool_result_misinterpretation
      └─ repair_strategy_failure

instruction_priority_inversion → prompt_injection_via_retrieval
context_truncation_loss → rag_retrieval_drift → incorrect_output
```

Exclusivity constraint: `semantic_cache_intent_bleeding`, `prompt_injection_via_retrieval`, and `context_truncation_loss` cannot share the same root (soft exclusivity).

---

## Failure Definitions

| Failure | Layer | Description |
|---|---|---|
| `clarification_failure` | reasoning | Fails to request clarification under ambiguous input |
| `assumption_invalidation_failure` | reasoning | Persists with invalidated hypothesis despite contradicting evidence |
| `premature_model_commitment` | reasoning | Early fixation on a single interpretation |
| `repair_strategy_failure` | reasoning | Patches errors instead of regenerating from corrected assumptions |
| `semantic_cache_intent_bleeding` | retrieval | Cache reuse with intent mismatch |
| `prompt_injection_via_retrieval` | retrieval | Adversarial instructions in retrieved content |
| `context_truncation_loss` | retrieval | Critical information lost during context truncation |
| `rag_retrieval_drift` | retrieval | Degraded retrieval relevance due to upstream failure |
| `instruction_priority_inversion` | instruction | Lower-priority instructions override higher-priority ones |
| `agent_tool_call_loop` | tool | Repeated tool invocation without progress |
| `tool_result_misinterpretation` | tool | Misinterpretation of tool output |
| `incorrect_output` | output | Final output misaligned with user intent |

---

## Signal Contract

22 unique signals across 12 patterns. Signal names are system-wide contracts:

- A signal name must have exactly one definition across all patterns
- Do not redefine the same signal with a different rule
- If a different threshold is needed, define a new signal name

---

## Structure

```
llm-failure-atlas/
  failure_graph.yaml           # canonical causal graph (12 nodes, 12 edges)
  matcher.py                   # log → signals → diagnosed failures
  compute_kpi.py               # 6 operational KPIs
  failures/                    # 12 failure pattern definitions (YAML)
  examples/                    # 10 example cases (log + matcher_output + expected)
  evaluation/                  # metrics.py + run_eval.py + 10 gold datasets
  validation/                  # 30 scenarios + 30 annotations + errors.json
  calibration/                 # run_calibration.py (SCIB grid search)
  learning/
    update_policy.py           # learning store update (suggestion-only)
    threshold_policy.json      # threshold adjustment proposals
    (runtime generated)        # fix_effectiveness.json, calibration_history.json,
                               # suggestions.json, run_history.json
```

---

## KPIs

`python compute_kpi.py` measures 6 operational indicators:

| KPI | Prevents | Target |
|---|---|---|
| threshold_boundary_rate | Detection instability | < 5% |
| fix_dominance | Fix overfitting | < 60% |
| failure_monotonicity | System runaway | > 90% |
| rollback_rate | Auto-apply safety risk | < 10% |
| no_regression_rate | Explicit degradation | > 95% |
| causal_consistency_rate | Policy drift | > 90% |

---

## Validation Results

30-scenario validation:

```
Root correctness:      92%
Path correctness:      84%
Explanation clarity:   82%
Errors: 2 (over_detection, legitimate edge cases)
```

---

## Graph Rules

```
[ ] node.id must match failure_id in the corresponding pattern file
[ ] edge.conditions must NOT use signal names (use semantic names)
[ ] edge.relation must be one of: predisposes | induces | propagates_to
[ ] node.layer must be one of: reasoning | system | retrieval | output
[ ] status: planned nodes are excluded from runtime diagnosis
```

This graph is **not used for diagnosis**. It is used only for causal interpretation, path construction, and explanation.

---

## Design Principles

- **Graph-first** — failures are defined by their position in a causal structure
- **Signal uniqueness** — no duplicated signal definitions across patterns
- **Separation of concerns** — Atlas (structure + detection), debugger (interpretation + fix)
- **Learning is suggestion-only** — patterns, graph, and templates are never auto-modified

---

## Reproducible Examples

10 examples covering structural patterns:

| Example | Pattern |
|---|---|
| `simple` | Linear causal chain |
| `branching` | Diverging paths from common root |
| `competing` | Multiple upstream causes for same downstream |
| `multi_root` | Multiple independent root causes |
| `decompose` / `full_decompose` | Complex multi-layer cascades |
| `priority_inversion` | Instruction layer failure |
| `tool_chain` | Tool layer cascade |
| `three_way_conflict` | Three-way exclusivity conflict |
| `closed_graph` | Fully connected subgraph |

Each contains `log.json`, `matcher_output.json`, and `expected_debugger_output.json`.

---

## Relationship to PLD

This Atlas is a concrete application of [Phase Loop Dynamics (PLD)](https://github.com/kiyoshisasano/agent-pld-metrics):

| PLD Phase | Atlas Equivalent |
|---|---|
| Drift | Initiating / upstream failure |
| Propagation | Downstream failure cascade |
| Repair | Fix generation (via debugger) |
| Outcome | System-level effect (`incorrect_output`) |

PLD provides the behavioral stability framework. The Atlas provides the failure taxonomy and causal structure that operates within it.

---

## Why This Matters

Failures are not independent.

A single root cause (`premature_model_commitment`) can cascade through cache misuse, retrieval degradation, and tool loops — producing an `incorrect_output` that appears to be a simple hallucination.

The Atlas makes the **full causal chain** visible, so that fixes target the root — not the symptom.

---

## License

MIT License. See [LICENSE](LICENSE).
