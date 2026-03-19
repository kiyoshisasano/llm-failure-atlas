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
│   ├── rag_retrieval_drift.yaml
│   ├── semantic_cache_intent_bleeding.yaml
│   ├── premature_model_commitment.yaml
│   ├── agent_tool_call_loop.yaml
│   └── prompt_injection_via_retrieval.yaml
├── examples/
│   ├── simple/        # linear chain
│   ├── branching/     # diverging paths
│   └── competing/     # competing upstream causes (merge)
└── matcher.py
```

---

## Causal Graph

The Atlas now models **three structural patterns**:

### 1. Linear chain

```text
premature_model_commitment
        ↓
semantic_cache_intent_bleeding
        ↓
rag_retrieval_drift
        ↓
incorrect_output
```

---

### 2. Branching (diverging causes)

```text
premature_model_commitment
       ├─→ semantic_cache_intent_bleeding
       │        ↓
       │   rag_retrieval_drift
       └─→ agent_tool_call_loop
```

---

### 3. Competing upstreams (merging causes)

```text
premature_model_commitment
       ├─→ semantic_cache_intent_bleeding ─→
       │                                  │
       └─→ prompt_injection_via_retrieval ─┘
                       ↓
              rag_retrieval_drift
```

---

## Why This Matters

Failures are not independent.

The same downstream failure (e.g. `rag_retrieval_drift`) can be caused by:

* cache misuse (`semantic_cache_intent_bleeding`)
* adversarial retrieval (`prompt_injection_via_retrieval`)

The Atlas makes these **competing causal paths explicit**.

---

## Failure Definitions

| Failure                          | Layer     | Status  | Description                                           |
| -------------------------------- | --------- | ------- | ----------------------------------------------------- |
| `premature_model_commitment`     | reasoning | defined | Early hypothesis fixation without clarification       |
| `semantic_cache_intent_bleeding` | system    | defined | Cache reuse with intent mismatch                      |
| `prompt_injection_via_retrieval` | retrieval | defined | Adversarial or instruction-altering retrieved context |
| `rag_retrieval_drift`            | retrieval | defined | Degraded retrieval due to upstream failure            |
| `agent_tool_call_loop`           | system    | defined | Repeated tool invocation without meaningful progress  |
| `incorrect_output`               | output    | planned | Observable output misalignment                        |

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

### Simple (linear)

```
examples/simple/
```

3-failure chain.

---

### Branching

```
examples/branching/
```

Diverging paths from a common upstream failure.

---

### Competing (key example)

```
examples/competing/
```

Multiple upstream failures explain the same downstream failure.

This is the **core use case** for causal debugging.

---

## Principles

* **Graph-first** — failures are defined by their position in a structure
* **Signal uniqueness** — no duplicated signal definitions
* **Separation of concerns** — Atlas (structure), matcher (diagnosis), debugger (interpretation)

---

## Relationship to PLD

This is a concrete application of [Phase Loop Dynamics (PLD)](https://github.com/kiyoshisasano/agent-pld-metrics):

| PLD Phase   | Atlas Equivalent              |
| ----------- | ----------------------------- |
| Drift       | initiating / upstream failure |
| Propagation | downstream failure            |
| Outcome     | system-level effect           |

---

## Status

| Capability | Status |
|---|---|
| Multi-failure modeling | ✅ supported |
| Linear causal chain | ✅ defined |
| Branching graph | ✅ defined |
| Competing causal paths | ✅ defined |
| Multi-system failure modeling (retrieval + tool) | ✅ defined |
| Machine-readable patterns | ✅ supported |
| Root ranking | ✅ supported (via debugger) |

---

## Future

* Path scoring for competing causes
* Stronger explanation generation (signal-aware)
* Additional failure types across layers
