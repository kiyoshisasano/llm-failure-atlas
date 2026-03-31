# Failure Eligibility — Observation Gap Analysis

What conditions would need to be met before each observation gap
could be promoted to a diagnosable failure pattern.

These are NOT planned features. They are requirements analyses
documenting what is missing and why.

---

## 1. Thin Grounding (expansion_ratio)

### Current observable state

- `grounding.expansion_ratio` is computed as `response_length / source_data_length`
- Values observed in Redis demo: 0.44–0.84 (healthy RAG), 12.59 (downstream test with thin source)
- When `tool_provided_data=True`, existing grounding signals do not fire

**Phase 2 observation (2026-03-31):** Cross-model validation produced a
concrete thin grounding instance. Claude Haiku, instructed to provide a
detailed weather forecast despite the tool returning "API is down," generated
3646 characters of fabricated specifics (temperatures, precipitation, clothing
recommendations) with source_data_length=0.

Telemetry: tool_provided_data=False, expansion_ratio=inf,
uncertainty_acknowledged=True, response_length=3646, alignment_score=0.71.
The existing incorrect_output pattern did not fire (conf=0.15) because
alignment was above 0.5 and no user correction occurred.

A draft pattern was tested against this data and 5 false positive
scenarios. Results: correctly diagnosed the thin grounding case (conf=0.75),
zero false positives on healthy telemetry. The draft uses two signals:
substantial_response_without_source (tool_provided_data=False, response_length > 200)
and uncertainty_disclosed_but_specifics_generated (uncertainty_acknowledged=True,
response_length > 400).

### Why not yet diagnosable

- Data points: 3 (Redis demo healthy, Redis demo thin, Claude Haiku fabrication). The original requirement of 20+ diverse observations is not yet met
- Only one model (Claude Haiku) produced the fabrication case. Gemini and gpt-4o-mini have not been tested with the grounding_gap scenario
- The response_length > 400 threshold for the second signal is based on a single observation (3646 chars). Mid-range values (200–500 chars) have not been tested
- Independence question partially resolved: `grounding_gap_not_acknowledged` covers the case where uncertainty is NOT acknowledged, while this pattern covers the case where uncertainty IS acknowledged but specifics are still generated. These are complementary, not overlapping

### Required conditions to become diagnosable

1. **Distribution data:** expansion_ratio and response_length values from 10+ thin grounding cases across at least 2 models and 2 domains (partially met: 1 model, 1 domain)
2. **Correlation evidence:** demonstrated cases where thin grounding led to user-visible incorrect output (met: the Claude Haiku case produced fabricated temperature data)
3. **Threshold calibration:** response_length thresholds (200 and 400) validated against mid-range observations (not met)
4. **Independence from existing signals:** confirmed — covers a distinct case from grounding_gap_not_acknowledged (met)

### Likely pattern location if promoted

Standalone failure pattern (thin_grounding), not an extension of
incorrect_output. The failure mode is distinct: alignment is high, no user
correction occurs, and the agent acknowledges uncertainty — none of which
trigger incorrect_output. A new node in the causal graph would connect to
incorrect_output as a downstream effect.

---

## 2. Semantic Cache Intent Bleeding (SCIB)

### Current observable state

- `semantic_cache_intent_bleeding` pattern exists with signal `cache_query_intent_mismatch`
- Experiments confirmed that cached responses are returned for different-intent queries
- The existing signal did not trigger for observed mismatch cases
- 30 seed/probe pairs tested across 3 rounds (15 cache hits observed)

### Why not diagnosable

- Similarity values for different-intent and valid-rephrase cases overlap — no clean threshold exists
- Adjusting the threshold in either direction creates unacceptable trade-offs
- Similarity alone does not capture intent difference — two queries can be semantically similar but have different intents

### Required conditions to become diagnosable

1. **Secondary signal:** a metric beyond raw similarity that distinguishes intent mismatch from valid rephrase (e.g., answer comparison between cached and fresh RAG responses)
2. **Cross-environment validation:** confirmation that the observed behavior generalizes beyond a single demo environment

### Likely pattern location if promoted

The existing `semantic_cache_intent_bleeding` signal structure is correct. Detection improvement requires a secondary signal, not threshold adjustment.

---

## 3. Semantic Mismatch (wrong-topic tool data)

### Current observable state

- `grounding.tool_provided_data=True` even when tool returns data for a completely different topic
- `response.alignment_score` can be misleadingly high due to surface-level word overlap
- No field currently captures "relevance of tool output to user query"

### Why not diagnosable

- Detecting topic mismatch between query and tool output requires semantic comparison
- Current heuristics (word overlap, keyword matching) cannot distinguish "data about the right topic" from "data about a different topic that shares vocabulary"
- The callback handler has no access to embedding models or LLM-based comparison

### Required conditions to become diagnosable

1. **Semantic similarity computation:** ability to compute embedding-based similarity between user query and tool output content (requires an embedding model in the observation layer)
2. **Topic extraction:** ability to extract the topic/intent of the query and the topic of the tool output independently
3. **Threshold calibration:** a similarity threshold below which tool output is considered off-topic (requires labeled examples)
4. **Layer 1 architecture decision:** whether semantic comparison runs in the adapter (per-framework), the observation layer (shared), or as a separate pre-matcher step

### Likely pattern location if promoted

New signal in `incorrect_output` or `rag_retrieval_drift`, depending on whether the mismatch originates from retrieval (wrong documents) or tool invocation (wrong API called). May require a new signal name (e.g., `tool_output_topic_mismatch`).

---

## Summary

| Gap | Blocker | Effort to resolve | Status |
|---|---|---|---|
| Thin grounding | Cross-model data + threshold calibration | Medium | Draft pattern tested, 1/4 conditions remaining |
| SCIB threshold | More cache-hit data + secondary signal | Medium-High | No change |
| Semantic mismatch | Embedding model in observation layer | High | No change |

None of these should be implemented until their respective blockers are resolved.