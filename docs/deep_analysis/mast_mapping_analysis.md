# MAST Taxonomy Mapping Analysis

## Purpose

Map Atlas's 15 failure patterns against MAST's 14 failure modes to
understand coverage overlap, gaps, and fundamental differences in
taxonomy design.

Source: "Why Do Multi-Agent LLM Systems Fail?" (Cemri et al., 2025,
NeurIPS 2025 Datasets and Benchmarks Track). 1600+ annotated traces,
7 MAS frameworks, Cohen's Kappa = 0.88.

---

## Taxonomies

**MAST:** 14 failure modes in 3 categories — (1) Specification and System
Design, (2) Inter-Agent Misalignment, (3) Task Verification and Termination.
Designed for multi-agent systems.

**Atlas:** 15 failure patterns in 4 layers + meta — reasoning, retrieval,
instruction, tool, output. Designed for single-agent runtime behavior.

---

## Mapping

### Atlas → MAST

| Atlas Pattern | MAST Mode | Quality |
|---|---|---|
| clarification_failure | FM-2.2 Lack of clarification seeking | Direct |
| incorrect_output | FM-3.4 Incorrect result | Direct |
| agent_tool_call_loop | FM-2.5 Redundant/circular actions | Partial |
| premature_model_commitment | FM-2.1 Agent derailing | Partial |
| unmodeled_failure | FM-3.5 Unintended agent behavior | Partial |
| tool_result_misinterpretation | FM-3.4 Incorrect result | Weak |
| assumption_invalidation_failure | — | No match |
| repair_strategy_failure | — | No match |
| semantic_cache_intent_bleeding | — | No match |
| prompt_injection_via_retrieval | — | No match |
| context_truncation_loss | — | No match |
| rag_retrieval_drift | — | No match |
| instruction_priority_inversion | — | No match |
| insufficient_observability | — | No match |
| conflicting_signals | — | No match |

### MAST → Atlas

| MAST Mode | Atlas Pattern | Notes |
|---|---|---|
| FM-1.1 Flawed task decomposition | — | Multi-agent only |
| FM-1.2 Ill-defined agent role | — | Design-time, not runtime |
| FM-1.3 Problematic orchestration | — | Multi-agent only |
| FM-1.4 Overly complex specification | — | Design-time, not runtime |
| FM-2.1 Agent derailing | premature_model_commitment (partial) | |
| FM-2.2 Lack of clarification seeking | clarification_failure (direct) | |
| FM-2.3 Unresolved inter-agent conflict | — | Multi-agent only |
| FM-2.4 Information withholding | — | Multi-agent only |
| FM-2.5 Redundant/circular actions | agent_tool_call_loop (partial) | |
| FM-3.1 Premature termination | — | Not modeled |
| FM-3.2 Failure to terminate | agent_tool_call_loop (partial) | |
| FM-3.3 Verification inadequacy | — | Not modeled |
| FM-3.4 Incorrect result | incorrect_output (direct) | |
| FM-3.5 Unintended agent behavior | unmodeled_failure (partial) | |

---

## Findings

**Complementary, not competing.** MAST covers multi-agent coordination
failures that Atlas does not address. Atlas covers runtime infrastructure
failures (retrieval, caching, injection, truncation) that MAST does not
address.

**2 direct overlaps:** clarification_failure and incorrect_output.

**8 Atlas patterns with no MAST equivalent:** These represent Atlas's
unique contribution — runtime-level, infrastructure-aware failure detection
for single-agent systems.

**8 MAST modes with no Atlas equivalent:** These require multi-agent
awareness (orchestration, role definition, inter-agent conflict).

**Evaluation:** MAST-Data traces are multi-agent conversation logs, not
compatible with Atlas's single-agent telemetry format. Direct evaluation
against MAST-Data is not feasible without trace-level adaptation.