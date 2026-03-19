# LLM Failure Atlas

LLM Failure Atlas is a graph-based failure system for modeling runtime failures in LLM-based systems.

It represents failures as a causal structure rather than a list.

---

## Purpose

The Atlas defines:

* what failures exist
* how they relate causally

LLM systems fail in structured, repeatable ways.  
The Atlas makes those structures explicit.

---

## Related Repositories

| Repository | Role |
|---|---|
| [agent-failure-debugger](https://github.com/kiyoshisasano/agent-failure-debugger) | Consumes matcher output + this graph → causal explanation |
| [agent-pld-metrics (PLD)](https://github.com/kiyoshisasano/agent-pld-metrics) | Behavioral stability framework this Atlas applies to |

---

## Execution Pipeline

This repository defines failure structures, not execution.

To run diagnosis:

```text
log → matcher → debugger
````

The **matcher** converts logs into detected failures (pattern-based inference).
The **debugger** takes matcher output and this graph to produce causal explanations.

A reference matcher implementation (`matcher.py`) is included for local use.
See [agent-failure-debugger](https://github.com/kiyoshisasano/agent-failure-debugger) for the full pipeline.

---

## Core Idea

Failures are nodes.
Relationships between failures are edges.
The system is defined as a graph:

```text
failure_graph.yaml
```

---

## Full Pipeline

```text
log
→ signals (pattern extraction)
→ failure detection (matcher)
→ failure graph (Atlas)
→ causal interpretation (debugger)
```

The Atlas provides the **structure** used in the final step.

---

## Structure

```text
.
├── failure_graph.yaml              # canonical causal graph
├── failures/
│   ├── rag_retrieval_drift.yaml               # pattern: signals + diagnosis
│   ├── semantic_cache_intent_bleeding.yaml    # pattern: signals + diagnosis
│   ├── premature_model_commitment.yaml        # pattern: signals + diagnosis
│   └── agent_tool_call_loop.yaml              # pattern: signals + diagnosis
├── examples/
│   ├── simple/
│   │   ├── log.json                           # input telemetry
│   │   ├── matcher_output.json                # matcher result
│   │   └── expected_debugger_output.json      # expected final output
│   └── branching/
│       ├── log.json                           # branching telemetry
│       ├── matcher_output.json                # matcher result (4 failures)
│       └── expected_debugger_output.json      # expected branching explanation
└── matcher.py                                 # reference matcher (local use)
```

---

## Causal Graph

The current Atlas models both a linear chain and a branching structure.

### Main chain

```text
premature_model_commitment      (reasoning layer)
        ↓ predisposes
semantic_cache_intent_bleeding  (system layer)
        ↓ induces
rag_retrieval_drift             (retrieval layer)
        ↓ propagates_to
incorrect_output                (output layer / planned)
```

### Branch

```text
premature_model_commitment
        ↓ predisposes
agent_tool_call_loop            (system layer)
```

### Combined view

```text
premature_model_commitment
       /                  \
      /                    \
semantic_cache_intent_bleeding   agent_tool_call_loop
              ↓
       rag_retrieval_drift
              ↓
         incorrect_output
```

**Example interpretation:**

* The model commits early to a single interpretation under ambiguity
* That committed interpretation may lead to:

  * cache reuse under the wrong intent (`semantic_cache_intent_bleeding`)
  * repeated tool use without meaningful progress (`agent_tool_call_loop`)
* In the cache path, retrieval is skipped and retrieval quality degrades
* Output quality may then drop downstream

---

## Failure Definitions

| Failure                          | Layer     | Status  | Description                                          |
| -------------------------------- | --------- | ------- | ---------------------------------------------------- |
| `premature_model_commitment`     | reasoning | defined | Early hypothesis fixation without clarification      |
| `semantic_cache_intent_bleeding` | system    | defined | Cache reuse with intent mismatch                     |
| `rag_retrieval_drift`            | retrieval | defined | Degraded retrieval due to upstream failure           |
| `agent_tool_call_loop`           | system    | defined | Repeated tool invocation without meaningful progress |
| `incorrect_output`               | output    | planned | Observable output misalignment                       |

---

## Graph Rules

```text
[ ] node.id must match failure_id in the corresponding pattern file
[ ] edge.conditions must NOT use signal names (use semantic names)
[ ] edge.relation must be one of: predisposes | induces | propagates_to
[ ] node.layer must be one of: reasoning | system | retrieval | output
[ ] status: planned nodes are excluded from runtime diagnosis
```

This graph is **not used for diagnosis**.
It is used only for: causal interpretation, path construction, explanation.

---

## Signal Contract

Signal names are system-wide contracts.

```text
[ ] A signal name must have exactly one definition across all patterns
[ ] Do not redefine the same signal name with a different rule
[ ] If a different threshold is needed, define a new signal name
```

---

## Reproducible Examples

### Simple chain

The `examples/simple/` directory contains a reproducible 3-failure chain:

```bash
# 1. Run matcher against log
python matcher.py failures/premature_model_commitment.yaml examples/simple/log.json
# ...repeat for each pattern...

# 2. Run debugger against matcher output
python ../agent-failure-debugger/main.py examples/simple/matcher_output.json failure_graph.yaml
```

Expected output matches `examples/simple/expected_debugger_output.json` exactly.

### Branching graph

The `examples/branching/` directory contains a reproducible branching case:

```bash
# 1. Run matcher against log
python matcher.py failures/premature_model_commitment.yaml examples/branching/log.json
# ...repeat for each pattern...

# 2. Run debugger against matcher output
python ../agent-failure-debugger/main.py examples/branching/matcher_output.json failure_graph.yaml
```

Expected output matches `examples/branching/expected_debugger_output.json` exactly.

---

## Principles

* **Graph-first** — no isolated failures; every failure has a structural position
* **Signal uniqueness** — no duplicated signal definitions across patterns
* **Separation of concerns** — Atlas defines structure; matcher handles diagnosis; debugger handles interpretation

---

## Relationship to PLD

This is a concrete application of [Phase Loop Dynamics (PLD)](https://github.com/kiyoshisasano/agent-pld-metrics):

| PLD Phase   | Atlas Equivalent                                                                                      |
| ----------- | ----------------------------------------------------------------------------------------------------- |
| Drift       | initiating or upstream failure (e.g., `premature_model_commitment`, `semantic_cache_intent_bleeding`) |
| Propagation | downstream failure (e.g., `rag_retrieval_drift`, `agent_tool_call_loop`)                              |
| Outcome     | system-level effect (`incorrect_output`)                                                              |

---

## Status

| Capability                     | Status                     |
| ------------------------------ | -------------------------- |
| Multi-failure modeling         | ✅ supported                |
| Linear causal chain            | ✅ defined                  |
| Branching causal graph         | ✅ defined                  |
| Machine-readable patterns      | ✅ supported                |
| Root ranking                   | ✅ supported (via debugger) |
| Reasoning-layer failures (PMC) | ✅ defined and diagnosable  |

---

## Future

* Expand graph coverage (additional failure types)
* Introduce competing root causes across branches
* Deepen reasoning-layer failure modeling
* Extend outcome-layer diagnosability

が README 上できれいに一致します。
```
