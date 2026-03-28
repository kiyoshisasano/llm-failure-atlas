# Semantic Cache Reuse — Observation Results

Date: 2026-03-28

## Purpose

Observed whether cached responses are reused for queries with different intent in a real Redis semantic cache environment, and whether Atlas's existing signals capture this behavior.

## Environment

- Redis demo: movie-recommender-rag-semantic-cache-workshop (Docker compose)
- Backend: FastAPI + Redis Vector Search + OpenAI gpt-4o-mini
- Help Center RAG: 29 articles ingested via `/api/help/ingest`
- Semantic cache: Redis-based, cosine similarity, auto-populated on first query
- Adapter: `adapters/redis_demo_adapter.py`

## Experiment Design

8 seed/probe pairs. For each pair:
1. Clear cache
2. Seed query (cache on) — always RAG
3. Probe query (cache on) — may cache hit
4. Probe query (cache off) — always RAG (baseline)

Compare answer_hash across the three runs to determine if the cache returned the seed's answer for a different probe query.

## Results

| Pair | Cache hit | Reused seed answer | Notes |
|---|---|---|---|
| refund vs cancel | YES | YES | Different query pair |
| password reset rephrase | YES | YES | Close rephrase pair |
| change plan vs cancel | no | - | - |
| payment declined vs unexpected charge | no | - | - |
| password vs account delete | no | - | - |
| buffering vs payment | no | - | - |
| parental controls vs buffering | no | - | - |
| download vs offline | BLOCKED | - | Guardrail blocked probe |

Cache hit occurred in only 2 of 8 pairs. The remaining 6 were cache misses. One pair was blocked by guardrails.

## Cache Reuse Case: refund vs cancel

**Seed:** "I want a refund for my subscription" → RAG response (hash: `f13e0162`)

**Probe (cache on):** "How do I cancel my subscription?" → cache hit, returned the seed's response (hash: `f13e0162`)

**Probe (cache off):** "How do I cancel my subscription?" → fresh RAG response about cancellation steps (hash: `8cac4497`)

The probe received a response about refunds when the user asked about cancellation. The answer_hash confirms the cached response was returned verbatim.

## Cache Reuse Case: password reset rephrase

**Seed:** "I forgot my password" → RAG response (hash: `561ca107`)

**Probe (cache on):** "How do I reset my password?" → cache hit, returned the seed's response (hash: `561ca107`)

This is a close rephrase of the same question. Cache reuse appears appropriate in this case.

## Atlas Detection

Matcher was run on the cache reuse case (refund vs cancel). The current `semantic_cache_intent_bleeding` signal did not trigger, while grounding-related symptom modifiers appeared at low confidence.

## Observations on Similarity

In the observed cache-hit cases, similarity values alone did not cleanly distinguish all reuse situations. More observations are needed before making any change to signal thresholds.

## Not Implemented (and why)

- **Threshold adjustment:** Only 2 cache-hit observations. Insufficient data for calibration.
- **cache_behavior adapter field:** Rejected — duplicates existing `cache.similarity`. Analysis belongs in analysis scripts.

## Guardrails Discovery

"Can I watch movies without internet?" was blocked by the demo's guardrails (`blocked=True`). The adapter handles this by setting safe telemetry values to prevent false failure detection.

## Conclusion

Cache reuse with a different follow-up query was observed and confirmed in this environment. The current `semantic_cache_intent_bleeding` signal did not trigger for this case.

With only 2 cache-hit observations, it is premature to adjust any thresholds. Next steps:
- Collect more seed/probe pairs across diverse query relationships
- Continue evaluating cache reuse behavior in real environments

## Reproduction

```powershell
cd C:\Users\teiki\atlas-workspace\llm-failure-atlas
python experiments/experiment_scib_observation.py
python experiments/analyze_cache_divergence.py
```