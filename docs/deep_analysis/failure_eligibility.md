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

### Why not diagnosable

- No established threshold separating "healthy expansion" from "excessive expansion"
- Only 2 data points at the extremes; no mid-range observations
- High expansion is not inherently a failure — LLM supplementation from training data is normal behavior
- The failure occurs only when supplementation is not disclosed, which is already partially covered by `grounding_gap_not_acknowledged`

### Required conditions to become diagnosable

1. **Distribution data:** expansion_ratio values from 20+ diverse agent runs across different domains, showing a clear separation between acceptable and problematic ranges
2. **Correlation evidence:** demonstrated cases where high expansion_ratio led to user-visible incorrect output (not just supplementation)
3. **Threshold calibration:** a threshold value that produces acceptable false positive/negative rates across the observed distribution
4. **Independence from existing signals:** confirmation that `grounding_gap_not_acknowledged` does not already cover the failure cases

### Likely pattern location if promoted

Extension of `incorrect_output` as a new symptom modifier, similar to `grounding_data_absent`. Would not be a standalone failure pattern.

---

## 2. Semantic Cache Intent Bleeding (SCIB threshold)

### Current observable state

- `semantic_cache_intent_bleeding` pattern exists with signal `cache_query_intent_mismatch` (fires when `cache.query_intent_similarity` is below a threshold)
- Redis demo observed cache reuse at similarity=0.691 for a different-intent query (refund → cancel)
- Valid cache reuse observed at similarity=0.780 (password reset rephrase)
- Current signal threshold did not trigger for the observed mismatch case

### Why not diagnosable (at current threshold)

- The gap between observed mismatch (0.691) and valid reuse (0.780) is narrow
- Only 2 cache-hit data points exist
- Adjusting the threshold in either direction creates unacceptable trade-offs: lowering misses real cases, raising creates false positives on valid rephrases
- Similarity alone does not capture intent difference — two queries can be semantically similar but have different intents

### Required conditions to become diagnosable

1. **More cache-hit observations:** at least 10 seed/probe pairs with confirmed intent match/mismatch labels, covering the 0.6–0.9 similarity range
2. **Secondary signal:** a metric beyond raw similarity that distinguishes intent mismatch from valid rephrase (e.g., answer overlap between cached response and fresh RAG response for the same query)
3. **Answer comparison capability:** ability to compare the cached answer against what fresh RAG would have produced (currently available via `answer_hash` in experiments but not in the adapter)
4. **Cross-environment validation:** confirmation that the similarity distribution generalizes beyond the Redis workshop demo

### Likely pattern location if promoted

Threshold adjustment within existing `semantic_cache_intent_bleeding` pattern. No new pattern needed — the existing signal structure is correct, only the detection boundary needs calibration.

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

| Gap | Blocker | Effort to resolve |
|---|---|---|
| Thin grounding | Distribution data needed | Medium (data collection + threshold calibration) |
| SCIB threshold | More cache-hit data + secondary signal | Medium-High (data + potentially answer comparison) |
| Semantic mismatch | Embedding model in observation layer | High (Layer 1 architecture change) |

None of these should be implemented until their respective blockers are resolved.