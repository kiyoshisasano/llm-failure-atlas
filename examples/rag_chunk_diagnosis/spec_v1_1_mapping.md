# Provenance Spec v1.1 ↔ Atlas/Debugger Output Mapping

This document maps each field in the **Provenance Spec v1.1** (proposed in
the forum thread; v1.2 in progress at time of writing) to its current
implementation status on the behavioral-side toolchain (Atlas v0.1.5 +
Debugger v0.4.0).

The spec separates concerns between the two sides:

- **Retrieval side** owns chunk-level pre-ingestion quality, coverage
  metrics, and the chunk-set envelope.
- **Behavioral side** owns runtime tracking of which chunks were used,
  how, and the resulting grounding signal.

The spec defines a passive-consumption integration: the retrieval side
emits structured envelopes; the behavioral side reads what it can from
the execution trace; joining happens in a shared analysis layer using
`chunk_set_hash`.

## Field mapping

### CHUNK IDENTITY

| Spec v1.1 field | Atlas/Debugger output | Status |
|---|---|---|
| `chunk_id` (content hash) | `telemetry.retrieval.retrieved_ids[i]` | ✅ Carried through from retriever step metadata |

The behavioral side does not generate `chunk_id`; it forwards whatever
identifier the retrieval pipeline placed in the trace. Common keys
recognised by adapters: `chunk_id`, `id`, `doc_id`, `document_id`.

### RETRIEVAL SET ENVELOPE

| Spec v1.1 field | Atlas/Debugger output | Status |
|---|---|---|
| `retrieval_set_id` (unique per event) | — | ❌ Not generated. Could be derived from step ordering if needed. |
| `chunk_set_hash` (sorted hash) | — | ⚠️ Not stored. Trivially derivable: `sha256(",".join(sorted(retrieved_ids)))`. Adding as a derived field on the behavioral side is one line. |
| `query_text` | `telemetry.input.*` (partial) | ⚠️ Available in the underlying log but not surfaced as a top-level field in adapter output. |
| `method` (dense/sparse/hybrid) | — | ❌ Not captured. Would require retriever-step metadata that the adapter currently does not parse. |
| `chunks[].chunk_id` | `telemetry.retrieval.retrieved_ids[i]` | ✅ |
| `chunks[].rank` | implicit (array order) | ✅ Order preserved. |
| `chunks[].similarity_score` | `telemetry.retrieval.retrieval_scores[i]` | ✅ |
| `chunks[].source_document_id` | — | ❌ Not extracted. Could be added if retriever metadata exposes it. |
| `chunks[].pre_ingestion_quality` | — | ❌ Behavioral-side scope: not generated. **Receives** these from the retrieval side via the chunk metadata if present. |
| `set_quality.quality_density` | — | ❌ Retrieval-side concern. |
| `set_quality.coverage_diversity` | — | ❌ Retrieval-side concern. |
| `set_quality.redundancy_ratio` | — | ❌ Retrieval-side concern. |

The retrieval-side blocks (`pre_ingestion_quality`, `set_quality`) are
correctly outside the behavioral-side scope. The behavioral side
provides identifiers and trace fields that can be **joined** with these
blocks via `chunk_id` / `chunk_set_hash` in a shared analysis layer.

### TRANSFORMATION ANCHOR

| Spec v1.1 field | Atlas/Debugger output | Status |
|---|---|---|
| `step_id` | — | ⚠️ Retrieval-side responsibility (decision: 2026-04-28). Behavioral side consumes this passively if present in the trace, but does not generate it. |
| `ancestor_retrieval_set_ids` | — | ⚠️ Same as above — retrieval-side responsibility. |
| `transformation_type` | — | ⚠️ Same as above. |

This block is **retrieval-side responsibility** (decided in spec dialogue,
2026-04-28). The behavioral side does not infer step-level lineage because
doing so would compound paraphrase-related proxy errors at every step
boundary; recorded facts on the retrieval side do not have this
degradation.

### UTILISATION (added in spec discussion 4/26)

| Spec v1.1 field | Atlas/Debugger output | Status |
|---|---|---|
| `utilisation_ratio` | `summary.execution_quality.utilisation.ratio` | ✅ Computed in Debugger v0.4.0 |
| (proxy) `used_chunk_ids` | `telemetry.retrieval.used_chunk_ids` | ✅ Computed in adapter (Atlas v0.1.5) |
| (proxy method) | `telemetry.retrieval.utilisation_method` | ✅ Currently always `"text_overlap_proxy"` |

The behavioral side's contribution to the four-dimensional diagnostic.
See `README.md` for proxy method, threshold, and known limitations.

## Layered separation

The implementation respects the layer separation discussed in the spec:

```
adapter (Atlas)              → observation: what was retrieved, what was used (proxy)
                                fields: retrieved_ids, retrieval_scores,
                                        used_chunk_ids, utilisation_method

execution_quality (Debugger) → aggregation: how observations combine into a ratio
                                fields: utilisation.ratio, used_count,
                                        retrieved_count, method
```

`utilisation_ratio` is computed at the Debugger summary layer rather
than embedded in the adapter, because it is a derived metric over two
adapter observations (`retrieved_ids` and `used_chunk_ids`), not a
direct observation itself.

This matches the design principle that adapters extract evidence and
the Debugger interprets it.

## What's needed for a full join with ChunkScore

Minimum viable join, in increasing implementation cost:

1. **Add `chunk_set_hash` to adapter output.** One-line derivation from
   `sorted(retrieved_ids)`. Useful as a stable join key for the shared
   analysis layer regardless of order.
2. **Surface retriever-step metadata.** When the retriever passes
   `pre_ingestion_quality` blocks via document metadata, the adapter
   should forward them to the telemetry rather than discarding.
3. **Step-level retrieval tracking.** Resolved as retrieval-side
   responsibility (2026-04-28). The behavioral side passively consumes
   `step_id` when present in the trace.

Items 1 and 2 are tractable for a v0.1.6 / v0.4.1 release.