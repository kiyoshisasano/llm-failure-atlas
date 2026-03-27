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

---

## How to Read Combined Signals

Atlas may report multiple signals simultaneously. A diagnosed failure, a grounding risk indicator, and normal telemetry can all appear in the same output. Here is how to interpret them.

### What to look at first

Start with diagnosed failures. If Atlas reports `agent_tool_call_loop` or `prompt_injection_via_retrieval`, that is the primary issue. Address it before examining grounding signals.

If no failure is diagnosed, look at the grounding section. `tool_provided_data`, `uncertainty_acknowledged`, and `expansion_ratio` together tell you whether the agent's response is well-supported.

If both grounding fields are clean (`tool_provided_data=True`, `uncertainty_acknowledged` not relevant), the output is likely acceptable. You can move on.

### When failure and risk appear together

A diagnosed failure takes priority for action. The risk indicators provide context for understanding *why* the failure is severe.

**Example: tool loop + grounding issues.** The agent called a tool 4 times, all returned errors (`agent_tool_call_loop` diagnosed). After the loop, it answered from training data (`tool_provided_data=False`, `uncertainty_acknowledged=True`). The failure (loop) is what to fix. The grounding signals tell you the agent recovered gracefully after the loop — it did not hallucinate. This means the fix priority is the loop behavior, not the output quality.

If the same scenario had `uncertainty_acknowledged=False`, the situation is worse: the agent looped AND produced ungrounded output without disclosure. Both the loop fix and a grounding review are warranted.

### When multiple risks appear without failure

No diagnosed failure does not mean everything is fine. Multiple risk signals together can indicate a systemic issue.

**Example: thin grounding without failure.** `tool_provided_data=True`, `expansion_ratio=12`, `uncertainty_acknowledged=False`. No failure fires because the tool did return data. But the agent expanded thin evidence into a detailed response without disclosure. If this pattern repeats across multiple runs, it suggests the agent's system prompt needs a grounding instruction: "If your answer includes information beyond what the tools returned, label it as supplemental."

This is not something to fix in Atlas. It is something to fix in your agent's prompt.

### The edge case: no data vs supplementation

These two situations look similar but are fundamentally different:

**No data (acceptable):** `tool_provided_data=False`, `uncertainty_acknowledged=True`, short response. The agent said "I couldn't find data" and stopped or gave a brief caveat. This is correct behavior.

**Supplementation without disclosure:** `tool_provided_data=False`, `uncertainty_acknowledged=True`, `expansion_ratio > 5` (if computed against a near-zero source). The agent acknowledged the gap but then wrote a long response anyway, drawing from training data. The disclosure is present ("I couldn't find specific data, however..."), but the volume of supplemented content may give users false confidence in the answer's grounding.

The difference is in response length relative to available evidence. A one-line caveat followed by a 500-word answer is structurally different from a one-line caveat followed by a one-line suggestion. Both have `uncertainty_acknowledged=True`, but the risk profile is different. Use `expansion_ratio` to distinguish them.

### What can be safely ignored

- `alignment_score` in isolation — it measures word overlap, not semantic correctness
- `grounding.response_length` in isolation — long responses are not inherently problematic
- `context.truncated=False` — means context window was not exhausted, which is the normal case
- Single-run anomalies — one unusual `expansion_ratio` is not a pattern; look for consistency across runs

### What indicates a systemic issue

- The same failure appearing across multiple runs with different inputs
- `expansion_ratio` consistently above 5 with `uncertainty_acknowledged=False`
- `soft_error_count` consistently high — suggests the tool itself is unreliable
- `tool_provided_data=False` on most runs — suggests tool integration problems, not agent problems