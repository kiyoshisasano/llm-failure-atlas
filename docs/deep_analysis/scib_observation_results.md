# Semantic Cache Reuse — Observation Results

Updated: 2026-03-31

## Purpose

Observed whether cached responses are reused for queries with different intent in a real Redis semantic cache environment, and whether Atlas's existing signals capture this behavior.

## Environment

- Redis demo: movie-recommender-rag-semantic-cache-workshop (Docker compose)
- Backend: FastAPI + Redis Vector Search + OpenAI gpt-4o-mini
- Help Center RAG: 29 articles ingested via `/api/help/ingest`
- Semantic cache: Redis-based, cosine similarity, auto-populated on first query
- Adapter: `adapters/redis_help_demo_adapter.py`

## Experiment Design

20 seed/probe pairs across 2 rounds (8 + 12). For each pair:
1. Clear cache
2. Seed query (cache on) — always RAG
3. Probe query (cache on) — may cache hit
4. Probe query (cache off) — always RAG (baseline)

Compare answer_hash across the three runs to determine if the cache returned the seed's answer for a different probe query.

## Results

### Round 1 (8 pairs)

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

### Round 2 (12 pairs)

| Pair | Cache hit | Similarity | Reused seed answer | Notes |
|---|---|---|---|---|
| upgrade vs downgrade plan | YES | 0.696 | YES | Different query pair |
| cancel vs delete account | BLOCKED | - | - | Guardrail blocked |
| change email vs change password | YES | 0.609 | YES | Different query pair |
| buffering vs no sound | no | - | - | - |
| refund vs billing history | no | - | - | - |
| can't log in vs forgot username | BLOCKED | - | - | Guardrail blocked |
| update payment vs change card | BLOCKED | - | - | Guardrail blocked |
| subscription help vs subscription question | YES | 0.847 | YES | Close rephrase pair |
| contact support vs customer service | no | - | - | - |
| account locked vs locked out | YES | 0.909 | YES | Close rephrase pair |
| add profile vs remove profile | YES | 0.614 | YES | Different query pair |
| charged twice vs wrong amount | YES | 0.656 | YES | Different query pair |

### Combined Summary

| Category | Count |
|---|---|
| Total pairs tested | 20 |
| Cache hits | 8 |
| Cache misses | 8 |
| Blocked by guardrails | 4 |
| Seed answer reused | 8 (all cache hits) |

## Similarity Distribution

All 8 cache-hit cases resulted in the seed answer being reused verbatim (confirmed by answer_hash). The similarity values fall into two groups:

**Different-query reuse (5 cases):**

| Similarity | Pair |
|---|---|
| 0.609 | change email → change password |
| 0.614 | add profile → remove profile |
| 0.656 | charged twice → wrong amount |
| 0.691 | refund → cancel |
| 0.696 | upgrade plan → downgrade plan |

**Close rephrase reuse (3 cases):**

| Similarity | Pair |
|---|---|
| 0.780 | forgot password → reset password |
| 0.847 | subscription help → subscription question |
| 0.909 | account locked → locked out |

The two groups do not overlap. Different-query cases range from 0.609 to 0.696. Close rephrase cases range from 0.780 to 0.909. A gap of approximately 0.08 exists between the two groups.

## Atlas Detection

The current `semantic_cache_intent_bleeding` signal did not trigger for any of the observed different-query reuse cases.

## Observations

- In this environment, different-query reuse and close rephrase reuse occupy distinct similarity ranges with no overlap in the observed data
- The gap between the two groups suggests that similarity-based separation may be feasible for this domain, but 8 data points are insufficient for confident threshold calibration
- All cache hits resulted in verbatim seed answer reuse (no partial reuse observed)
- Guardrails blocked 4 queries, preventing cache behavior observation for those pairs

## Not Changed

- No threshold adjustments made (data still limited)
- No new failure signals added
- SCIB pattern definition unchanged

## Next Steps

- Continue collecting seed/probe pairs to fill the observed gap (0.70-0.78 range)
- Test with query pairs that are likely to fall in the gap region
- Evaluate whether the distribution holds across different help center article sets

## Reproduction

```powershell
cd C:\Users\teiki\atlas-workspace
python experiment_scib_round2.py
```

Previous round: `llm-failure-atlas/experiments/experiment_scib_observation.py`