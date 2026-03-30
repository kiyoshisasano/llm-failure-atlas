# Semantic Cache Reuse — Observation Results

Updated: 2026-03-31

## Purpose

Investigated whether cached responses are reused for queries with different intent in a real Redis semantic cache environment, and whether Atlas's existing signals can detect this behavior.

## Environment

- Redis DevRel workshop demo (Docker compose)
- RAG with semantic cache (cosine similarity, auto-populated)
- Adapter: `adapters/redis_help_demo_adapter.py`

## Experiment Summary

30 seed/probe pairs were tested across 3 rounds. Each pair followed the same protocol: clear cache, seed query, probe query with cache, probe query without cache. Answer hashes were compared to confirm whether the cached seed answer was returned.

Approximately half of the pairs resulted in cache hits. In all cache-hit cases, the seed answer was returned verbatim.

## Key Findings

1. **Cache reuse with different-intent queries occurs frequently.** A substantial portion of cache hits involved probe queries with different intent from the seed query.

2. **Similarity values do not cleanly separate valid reuse from inappropriate reuse.** Initial rounds suggested two distinct groups, but additional data revealed overlap between the groups. There is no single similarity threshold that separates them without unacceptable error rates.

3. **The current `semantic_cache_intent_bleeding` signal did not trigger** for any of the observed different-query reuse cases.

4. **A secondary signal beyond similarity is needed** to reliably distinguish valid cache reuse from inappropriate reuse. Potential directions include answer comparison between cached and fresh RAG responses.

## Implications for Atlas

- The existing SCIB pattern definition is structurally correct, but its similarity-based signal is insufficient for detection in the observed range
- Threshold adjustment alone cannot solve this — the issue is a limitation of the similarity metric itself
- Detection improvement requires a secondary signal, which is outside the current heuristic-based observation layer

## Not Changed

- No threshold adjustments
- No new failure signals added
- SCIB pattern definition unchanged

## Reproduction

Experiment scripts are in `experiments/`. Raw data is retained locally.
