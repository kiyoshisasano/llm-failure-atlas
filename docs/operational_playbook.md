# Operational Playbook

Decision framework for interpreting Atlas output in production. Based on current telemetry — no additional signals or patterns required.

For concrete examples of each pattern, see [Applied Debugging Examples](applied_debugging_examples.md).

---

## When should you use Atlas in production?

**Entry conditions:**

- You have a LangChain/LangGraph or CrewAI agent that calls tools or retrievers
- The agent occasionally produces wrong, incomplete, or misleading output
- You need to understand *why* it fails, not just *that* it fails

**Expected outcomes:**

- Root cause identification for known failure patterns (15 patterns, 30 signals)
- Risk indicators for grounding quality (data availability, evidence sufficiency)
- Actionable fix proposals with safety-gated auto-apply

**Limitations:**

- Atlas detects runtime execution failures, not judgment quality or specification errors
- Semantic mismatch (tool returns data for wrong topic) is observable but not detectable without ML
- Thin grounding (agent supplements sparse data without disclosure) is a risk indicator, not a diagnosable failure
- Detection quality depends on the observation layer — callback mode infers some fields heuristically

---

## Pattern: Tool Call Loop

**Detection condition:**

```
tools.repeat_count >= 2
state.progress_made == False
```

**Classification:** Failure (`agent_tool_call_loop`)

**Required action:** Fix

Apply the recommended patch: `max_repeat_calls: 3` + `require_progress_between_calls: True`. Gate mode is `proposal_only` for workflow patches (hard blocker on fix_type).

**Escalation rule:** N/A — this is already a diagnosed failure.

**Confidence requirements:**

- `tools.repeat_count` is directly observed (reliable)
- `state.progress_made` uses soft error markers (reliable for error strings, may miss subtle failures)
- `tools.soft_error_count` distinguishes real errors from retries on valid-but-empty results

---

## Pattern: No Data, Graceful Handling

**Detection condition:**

```
grounding.tool_provided_data == False
grounding.uncertainty_acknowledged == True
```

**Classification:** Acceptable

**Required action:** Ignore

The agent had no data and said so. This is correct behavior. No failure should fire.

**Escalation rule:** Escalate to "risk" if `expansion_ratio > 5` — the agent acknowledged the gap but still produced a long response, suggesting supplementation from training data.

**Confidence requirements:**

- `tool_provided_data` depends on soft error markers matching tool output (reliable for standard error messages)
- `uncertainty_acknowledged` depends on keyword matching in the response (may miss unusual phrasing)

---

## Pattern: No Data, No Disclosure

**Detection condition:**

```
grounding.tool_provided_data == False
grounding.uncertainty_acknowledged == False
grounding.response_length > 200
```

**Classification:** Risk (potential hallucination)

**Required action:** Block

If this occurs within a diagnosed `incorrect_output` failure, `grounding_gap_not_acknowledged` fires as a symptom modifier (+0.15 confidence). The auto-apply gate adds a hard blocker: "grounding gap not acknowledged."

If no failure is diagnosed (edge case), monitor and flag for review.

**Escalation rule:** Escalate to failure if a downstream user reports incorrect information. Add the trace to validation scenarios for future pattern refinement.

**Confidence requirements:**

- `uncertainty_acknowledged=False` is a negative signal — absence of markers. May produce false negatives if the agent uses phrasing not in the marker list.
- Review the marker list in `_build_grounding()` if false negatives are suspected.

---

## Pattern: Thin Grounding (Evidence Expansion)

**Detection condition:**

```
grounding.tool_provided_data == True
grounding.expansion_ratio > 5
grounding.uncertainty_acknowledged == False
```

**Classification:** Risk

**Required action:** Monitor

Atlas cannot diagnose this as a failure because `tool_provided_data=True` prevents grounding signals from firing. The `expansion_ratio` is a risk indicator only.

**Escalation rule:** Escalate to failure candidate if:
1. Multiple instances are observed across different agents
2. Downstream users report being misled by supplemented content
3. A reliable threshold for expansion_ratio emerges from production data

Do NOT add as a failure pattern based on a single observation.

**Confidence requirements:**

- `source_data_length` counts all usable tool output characters (may overestimate if some tool output is metadata)
- `expansion_ratio` is meaningful only when `source_data_length > 0`
- The threshold (5) is exploratory — calibrate based on your domain

---

## Pattern: Irrelevant Data Used as Grounding

**Detection condition:**

```
grounding.tool_provided_data == True
grounding.uncertainty_acknowledged == False
(semantic mismatch between query and tool output — not currently detectable)
```

**Classification:** Risk (observation gap)

**Required action:** Monitor

Atlas cannot detect this pattern. The telemetry shows `tool_provided_data=True` and `alignment_score` may be high (surface-level word overlap with the query).

**Escalation rule:** N/A — this requires observation layer enhancement (semantic similarity between query and tool output). Until then, mitigate at the tool level by validating tool output relevance before passing to the LLM.

**Confidence requirements:**

- This pattern cannot be reliably detected with current heuristics
- `alignment_score` is unreliable for this case (measures query-response overlap, not query-evidence overlap)

---

## Pattern: Prompt Injection via Retrieval

**Detection condition:**

```
retrieval.contains_instruction == True
retrieval.adversarial_score >= 0.7
```

**Classification:** Failure (`prompt_injection_via_retrieval`)

**Required action:** Fix

Apply the retrieval filter guard patch. Gate mode is `staged_review` (safety=medium).

**Escalation rule:** N/A — this is already a diagnosed failure.

**Confidence requirements:**

- Requires retriever callback to fire (RAG applications with VectorStoreRetriever only)
- Does NOT work with tool-based search (tool output is not scanned for adversarial patterns)
- Keyword scan may miss novel injection patterns not in the `ADVERSARIAL_PATTERNS` list
- Verified via observation logic tests (25/25 PASS) with injected state, not yet with live RAG

---

## Quick Reference

| Pattern | Classification | Action | Key Signal |
|---|---|---|---|
| Tool call loop | Failure | Fix | `tools.repeat_count >= 2` + `progress_made=False` |
| No data + disclosed | Acceptable | Ignore | `tool_provided_data=False` + `uncertainty_acknowledged=True` |
| No data + not disclosed | Risk | Block | `tool_provided_data=False` + `uncertainty_acknowledged=False` |
| Thin grounding | Risk | Monitor | `expansion_ratio > 5` + `uncertainty_acknowledged=False` |
| Irrelevant data | Risk (gap) | Monitor | Not detectable with current telemetry |
| Prompt injection | Failure | Fix | `adversarial_score >= 0.7` |