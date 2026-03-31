# LLM Failure Atlas

The detection and pattern library for LLM agent failures. Defines failure patterns, signals, and adapters. Fully deterministic, no ML required.

**Atlas detects.** For root cause diagnosis, explanation, and fixes, use [agent-failure-debugger](https://github.com/kiyoshisasano/agent-failure-debugger).

| When | Use | What you get |
|---|---|---|
| Agent is running | Atlas `watch()` | Live detection, optional detection reporting |
| You have a log file | Debugger `diagnose()` | Root cause + explanation + fix proposal |
| Atlas only (no debugger) | `auto_diagnose=True` | Detected failures (pattern matching + signals) + telemetry |
| Full pipeline | `auto_pipeline=True` or `diagnose()` | Detection + diagnosis + explanation + fix proposal (with optional auto-apply) |

```python
# Detection only (Atlas)
from adapters.callback_handler import watch
graph = watch(workflow.compile(), auto_diagnose=True)

# Full diagnosis (via Debugger)
from diagnose import diagnose
result = diagnose(raw_log, adapter="langchain")
```

---

## End-to-End Example (Atlas + Debugger)

Run a full example including detection (Atlas) and diagnosis (Debugger):

```bash
git clone https://github.com/kiyoshisasano/llm-failure-atlas.git
cd llm-failure-atlas
pip install -r requirements.txt

# Clone debugger (for full pipeline)
cd ..
git clone https://github.com/kiyoshisasano/agent-failure-debugger.git
cd agent-failure-debugger && pip install -r requirements.txt && cd ../llm-failure-atlas

python quickstart_demo.py
```

---

## How to Use

Add one line to your LangGraph agent for failure detection:

```python
from adapters.callback_handler import watch

graph = watch(workflow.compile(), auto_diagnose=True)
result = graph.invoke({"messages": [HumanMessage(content="...")]})
# → detected failures printed on completion
```

**Flags:**
- `auto_diagnose=True` — run Atlas detection only (pattern matching + signals, no causal analysis)
- `auto_pipeline=True` — also run the [debugger](https://github.com/kiyoshisasano/agent-failure-debugger) pipeline (root cause, explanation, fix proposal) automatically

The original graph behavior is unchanged. Requires `pip install langchain-core`. Core pipeline requires only `pyyaml`.

**Other integration options:**

Adapters normalize raw logs from different frameworks into the format Atlas expects.

| Method | Use when | Code |
|---|---|---|
| Callback handler | Any LangChain/LangGraph agent | `config={"callbacks": [AtlasCallbackHandler(auto_diagnose=True)]}` |
| CrewAI listener | CrewAI crews | `AtlasCrewListener(auto_diagnose=True)` — auto-registers on event bus |
| Batch adapter | Post-hoc analysis from JSON exports | `LangChainAdapter().build_matcher_input(raw_trace)` |
| Redis help demo | [Redis workshop](https://github.com/redis-developer/movie-recommender-rag-semantic-cache-workshop) /api/help/chat | `RedisHelpDemoAdapter().build_matcher_input(response)` |

See `adapters/` for full examples of each method.

---

## What You Get

When a failure is detected:

```
Root cause:  agent_tool_call_loop (conf=0.55)
Failures:    1
Gate:        proposal_only (score=0.0)

Explanation:
  Context: Root cause identified: the agent repeatedly invoked tools
           without making meaningful state progress.
  Risk: MEDIUM
  Action: Review the proposed fix before applying.
```

When no failure is detected but grounding signals indicate a risk:

```
Failures:   none detected
Grounding:  tool_provided_data=False  uncertainty_acknowledged=True
```

This specific combination (no data + disclosed) is acceptable behavior. Other grounding states — such as no data without disclosure, or thin grounding with high expansion ratio — may indicate risk. See the [Operational Playbook](docs/operational_playbook.md) for the full decision framework.

For real-world walkthroughs, see [Applied Debugging Examples](docs/applied_debugging_examples.md).

---

## Minimal Detection Example (Matcher Only)

Run a single failure pattern against a prepared matcher input — no agent, no debugger:

```python
from matcher import run

result = run("failures/incorrect_output.yaml", "examples/sample_matcher_input.json")

print(result["failure_id"])   # "incorrect_output"
print(result["diagnosed"])    # True
print(result["confidence"])   # 0.7
print(result["signals"])      # which signals fired
```

This is useful for understanding how detection works, testing custom adapters, or debugging signal behavior. For full pipeline usage (diagnosis, explanation, fixes), use [agent-failure-debugger](https://github.com/kiyoshisasano/agent-failure-debugger).

---

## What It Detects

15 failure patterns (12 domain + 3 meta) across 5 layers, connected by a causal graph with 12 edges.

**Domain failures:**

| Failure | Layer | Description |
|---|---|---|
| `clarification_failure` | reasoning | Fails to request clarification under ambiguous input |
| `assumption_invalidation_failure` | reasoning | Persists with invalidated hypothesis |
| `premature_model_commitment` | reasoning | Early fixation on a single interpretation |
| `repair_strategy_failure` | reasoning | Patches errors instead of regenerating |
| `semantic_cache_intent_bleeding` | retrieval | Cache reuse with intent mismatch |
| `prompt_injection_via_retrieval` | retrieval | Adversarial instructions in retrieved content |
| `context_truncation_loss` | retrieval | Critical information lost during truncation |
| `rag_retrieval_drift` | retrieval | Degraded retrieval relevance |
| `instruction_priority_inversion` | instruction | Lower-priority instructions override higher |
| `agent_tool_call_loop` | tool | Repeated tool invocation without progress |
| `tool_result_misinterpretation` | tool | Misinterpretation of tool output |
| `incorrect_output` | output | Final output misaligned with user intent |

**Meta failures** (model limitation indicators, not part of causal graph):

| Failure | Fires when |
|---|---|
| `unmodeled_failure` | Symptoms present but no domain pattern matched |
| `insufficient_observability` | Too many expected telemetry fields missing |
| `conflicting_signals` | Signals point in contradictory directions |

---

## Causal Graph

You don't need to memorize this graph. The tool traverses it for you and reports the root cause automatically.

```mermaid
graph TD
    CF[clarification_failure] --> AIF[assumption_invalidation_failure]
    AIF --> PMC[premature_model_commitment]
    PMC --> SCIB[semantic_cache_intent_bleeding]
    PMC --> PIVR[prompt_injection_via_retrieval]
    PMC --> ATCL[agent_tool_call_loop]
    PMC --> RSF[repair_strategy_failure]
    IPI[instruction_priority_inversion] --> PIVR
    CTL[context_truncation_loss] --> RRD[rag_retrieval_drift]
    SCIB --> RRD
    PIVR --> RRD
    RRD --> IO((incorrect_output))
    ATCL --> TRM[tool_result_misinterpretation]
```

The same downstream failure can have multiple upstream causes. The graph makes competing causal paths explicit.

---

## Observation Layer

The callback handler infers telemetry fields not directly observable from agent events.

| Field | Inference method |
|---|---|
| `input.ambiguity_score` | Word count + pronoun/vague term detection |
| `interaction.user_correction_detected` | Response admits failure + pivots to different topic |
| `response.alignment_score` | Word overlap - topic mismatch penalty - negation penalty |
| `state.progress_made` | All tool outputs contain negative/error markers |
| `tools.soft_error_count` | Tool output text contains error/empty markers |
| `tools.error_count` | Tool-level exceptions (HTTP errors, timeouts, MCP failures) |
| `tools.hard_error_detected` | True if any tool raised an exception (vs returning empty) |
| `grounding.tool_provided_data` | At least one tool returned non-error output |
| `grounding.uncertainty_acknowledged` | Response contains staleness/uncertainty language |
| `grounding.source_data_length` | Total character count of usable tool outputs |
| `grounding.expansion_ratio` | response_length / source_data_length |
| `retrieval.adversarial_score` | Keyword scan of retrieved documents for injection patterns |
| `context.truncated` | Input tokens exceed 85% of model context window |

30 signals across 15 patterns. Each signal tracks observation quality: unobserved signals receive 0.6x confidence decay.

---

## Tested with Real Agents

Verified with real LangGraph agents under controlled scenarios, using OpenAI, Anthropic, and Google APIs.

| Scenario | gpt-4o-mini | Claude Haiku 4.5 | Gemini 2.5 Flash |
|---|---|---|---|
| Forced topic pivot | `incorrect_output` (0.7) | `incorrect_output` (0.7) | `incorrect_output` (0.7) |
| Forced tool retry loop | `agent_tool_call_loop` (0.7) | `agent_tool_call_loop` (0.7) | `agent_tool_call_loop` (0.7) |
| No clarification allowed | `clarification_failure` (0.7) | `clarification_failure` (0.7) | `clarification_failure` (0.7) |

Scenarios use system prompts to induce specific failure behaviors, ensuring reproducibility across model versions. Without constraints, the three models handle the same inputs differently: Claude asks for clarification where gpt-4o-mini guesses, Gemini asks for dates where Claude asks for IDs. Atlas correctly reports 0 failures when no failure occurs, regardless of model.

Both `watch()` and `diagnose()` code paths produce identical telemetry and diagnoses. See [Cross-Model Validation](docs/cross_model_validation.md) for the full report.

**Adapter verification status:**

| Adapter | Verified | Notes |
|---|---|---|
| callback_handler (watch) | ✅ 3 models | gpt-4o-mini, Claude Haiku, Gemini 2.5 Flash |
| langchain_adapter | ✅ Parity confirmed | Identical telemetry and detection to watch() |
| langsmith_adapter | ✅ Parity confirmed | Identical telemetry and detection to langchain_adapter |
| crewai_adapter | ⚠ Stage 1 verified | Functional but does not yet produce `state` or `grounding` telemetry. Core detection (tool calls, alignment, clarification) works; `agent_tool_call_loop` may not fire via `diagnose()` path |
| redis_help_demo_adapter | ⚠ Stage 1 verified | Tested with Redis workshop; not re-verified after Phase 2 marker changes |

Additional: 10/10 regression tests, 7/7 false positive tests (0 domain failures on healthy telemetry), 5 derailment tests (5/5 PASS), 25 observation logic checks (25/25 PASS).

**Redis Semantic Cache experiment:**

Tested with a Redis RAG + Semantic Cache demo (30 seed/probe pairs across 3 rounds, 15 cache hits observed). Cache reuse with different-intent queries occurred frequently. Similarity values for different-query and valid-rephrase cases overlapped, confirming that a single similarity threshold cannot reliably separate the two. The current `semantic_cache_intent_bleeding` signal did not trigger for any observed case. Detection improvement requires a secondary signal beyond similarity. See [SCIB Observation Results](docs/deep_analysis/scib_observation_results.md) for details.

---

## Writing a Custom Adapter

Extend `BaseAdapter`:

```python
from adapters.base_adapter import BaseAdapter

class MyAdapter(BaseAdapter):
    source = "my_platform"

    def normalize(self, raw_log: dict) -> dict: ...

    def extract_features(self, normalized: dict) -> dict:
        return {
            "input": {"ambiguity_score": ...},
            "interaction": {"clarification_triggered": ..., "user_correction_detected": ...},
            "reasoning": {"replanned": ..., "hypothesis_count": ...},
            "cache": {"hit": ..., "similarity": ..., "query_intent_similarity": ...},
            "retrieval": {"skipped": ...},
            "response": {"alignment_score": ...},
            "tools": {"call_count": ..., "repeat_count": ..., "soft_error_count": ...},
            "state": {"progress_made": ...},
            "grounding": {"tool_provided_data": ..., "uncertainty_acknowledged": ...,
                          "response_length": ..., "source_data_length": ...,
                          "expansion_ratio": ...},
        }
```

---

## Pipeline

```
[Your Agent] → Adapter → Telemetry → Matcher → Debugger → Fix + Explanation
```

The Atlas provides structure, detection, and adapters. The [debugger](https://github.com/kiyoshisasano/agent-failure-debugger) provides causal interpretation, explanation, fix generation, and auto-apply.

---

## KPIs

| KPI | Prevents | Target |
|---|---|---|
| threshold_boundary_rate | Detection instability | < 5% |
| fix_dominance | Fix overfitting | < 60% |
| failure_monotonicity | System runaway | > 90% |
| rollback_rate | Auto-apply safety risk | < 10% |
| no_regression_rate | Explicit degradation | > 95% |
| causal_consistency_rate | Policy drift | > 90% |

---

## Design Principles

- **Deterministic** — same input, same diagnosis. No probabilistic inference.
- **Symbolic** — all rules are human-readable. No learned models in the core.
- **Consistent over correct** — best-supported cause, not necessarily the "true" cause.
- **Detection is local** — each failure is scored independently. The graph is for interpretation only.
- **Signal uniqueness** — one definition per signal across all patterns.
- **Learning is suggestion-only** — patterns, graph, and templates are never auto-modified.
- **Observation quality** — unobserved signals receive 0.6x confidence decay.

---

## Known Limitations

Some failure-like behaviors are observable but not yet diagnosable as failure patterns:

- **Thin grounding** — the agent produces detailed specifics without source data, sometimes while acknowledging the lack of data. Observed across gpt-4o-mini and Claude Haiku in 3 domains (weather, stock, restaurant). A draft pattern has been validated (5 cases detected, 0 false positives) but threshold calibration for mid-range responses is still needed before promotion.
- **Cache intent mismatch** — a semantically similar but different-intent query receives a cached response. Tested with 30 seed/probe pairs; similarity ranges for valid and invalid reuse overlap, confirming that similarity alone is insufficient. Requires a secondary signal.
- **Semantic mismatch** — a tool returns data for a completely different topic. Not detectable with current heuristics; requires embedding-based comparison (Layer 1 ML).

These are tracked as observation gaps, not planned features. See [Failure Eligibility](docs/deep_analysis/failure_eligibility.md) for the conditions required to promote each to a diagnosable pattern.

---

## Structure

```
llm-failure-atlas/
  matcher.py                       # detection engine
  failure_graph.yaml               # causal graph (15 nodes, 12 edges)
  compute_kpi.py                   # 6 operational KPIs
  quickstart_demo.py               # end-to-end demo
  failures/                        # 15 pattern definitions (YAML)
  adapters/
    callback_handler.py            # real-time callback + watch()
    crewai_adapter.py              # CrewAI event listener
    langchain_adapter.py           # LangChain batch adapter
    langsmith_adapter.py           # LangSmith batch adapter
    redis_help_demo_adapter.py     # Redis workshop adapter
    base_adapter.py                # abstract interface
  docs/
    applied_debugging_examples.md  # 7 real-world cases
    operational_playbook.md        # 9-pattern decision framework
    cross_model_validation.md      # Claude Haiku validation results
    deep_analysis/
      failure_eligibility.md       # observation gap → failure requirements
      scib_observation_results.md  # cache reuse experiments
      decision_failure_exploration.md
      observation_layer_gap_analysis.md
  examples/                        # 10 reproducible test cases + sample input
  evaluation/                      # metrics + evaluation runner
  validation/                      # 30 scenarios + annotations
  learning/                        # suggestion-only learning store
```

---

## Related Repositories

| Repository | Role |
|---|---|
| [agent-failure-debugger](https://github.com/kiyoshisasano/agent-failure-debugger) | Causal diagnosis, fix generation, auto-apply, explanation |
| [agent-pld-metrics](https://github.com/kiyoshisasano/agent-pld-metrics) | Behavioral stability framework (PLD) |

## Cogency Framework Mapping

Failure patterns are tagged with `cogency_tags` referencing Cliff Rosen's [Diagnostic Framework for Agent Failure](https://www.orchestratorstudios.com/articles/agent-failure-diagnostics.html). Rosen diagnoses input specification quality; Atlas diagnoses runtime symptoms. The mapping is an interpretive projection. See `failures/*.yaml` for tags.

## Relationship to PLD

This system implements a single control step (analysis + intervention + evaluation) within the [PLD](https://github.com/kiyoshisasano/agent-pld-metrics) loop. It is not a PLD runtime. Root causes explain drift structurally; fixes feed Repair strategies; evaluate_fix provides structural reentry checks.

---

## License

MIT License. See [LICENSE](LICENSE).
