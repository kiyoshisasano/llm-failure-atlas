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

## Core Idea

Failures are nodes.

Relationships between failures are edges.

The system is defined as a graph:

```
failure_graph.yaml
```

---

## Pipeline

```
log
→ signals (patterns)
→ failure detection (matchers)
→ failure graph (Atlas)
→ causal interpretation (debugger)
```

The Atlas provides the **structure** used in the final step.

---

## Structure

```
.
├── failure_graph.yaml
├── pattern.yaml
├── semantic_cache_intent_bleeding.yaml
```

---

## Example

```
semantic_cache_intent_bleeding
↓ induces
rag_retrieval_drift
```

Interpretation:

* cache reuse happens with intent mismatch
* retrieval is skipped
* retrieval quality degrades

---

## Principles

* Graph-first (no isolated failures)
* No duplicated meaning (signals are unique)
* Separation of concerns (Atlas ≠ diagnosis)

---

## Relationship to PLD

This is an application of PLD:

* Drift → initiating failure
* Propagation → downstream failure
* Outcome → system-level effect

---

## Status

* multi-failure modeling: supported
* causal structure: defined
* machine-readable patterns: supported

---

## Future

* expand graph coverage
* introduce reasoning-layer failures
* integrate deeper causal chains
