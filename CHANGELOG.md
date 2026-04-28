# Changelog

All notable changes to `llm-failure-atlas` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.5] - 2026-04-28

### Added
- `retrieval.used_chunk_ids` — list of chunk IDs that overlap with the agent's response (text-overlap proxy). Approximates which retrieved chunks were actually used vs ignored.
- `retrieval.utilisation_method` — proxy method name (currently `"text_overlap_proxy"`). Carried explicitly so consumers know the field is an approximation, not ground truth.
- `compute_chunk_utilisation()` — shared helper in `base_adapter` for computing chunk utilisation. Used by `langchain_adapter` and `callback_handler` to maintain parity.
- `examples/rag_chunk_diagnosis/` — PoC example demonstrating chunk-utilisation tracking and its mapping to a four-dimensional retrieval-set diagnostic (data / composition / navigation / transformation problem).

### Documentation
- `examples/rag_chunk_diagnosis/README.md` — scenario walkthrough with two contrasting cases (coverage-diversity failure where the proxy hits its limit, navigation failure where it correctly identifies unused chunks).
- `examples/rag_chunk_diagnosis/spec_v1_1_mapping.md` — field-level mapping between the Provenance Spec v1.1 (proposed in community discussion) and Atlas/Debugger output.

### Notes
- `langsmith_adapter` does not yet emit `used_chunk_ids` / `utilisation_method`. Parity with the other two adapters is planned for a future release.

## [0.1.4] - 2026-04-21

### Added
- `retrieval.retrieved_ids` — list of document chunk IDs returned by retriever steps (langchain_adapter, callback_handler, langsmith_adapter).
- `retrieval.retrieval_scores` — list of similarity scores for each retrieved document.
- `retrieval.mean_retrieval_score` — average similarity score across the retrieval result set.
- `grounding.retrieved_ids` — list of chunk IDs from tool steps that return documents (parallel field for the grounding section).
- `examples/api_orchestration/` — non-LLM workflow failure detection example, demonstrating that retry-loop and termination patterns apply to API orchestration scenarios as well as LLM agents.

### Fixed
- `grounding.expansion_ratio` now returns `None` instead of `float("inf")` when source data is empty but a response was produced. Restores strict JSON compatibility (`json.dumps(allow_nan=False)`) for telemetry export to non-Python systems and SaaS pipelines. Affects `langchain_adapter`, `callback_handler`, and `langsmith_adapter`. The `redis_help_demo_adapter` was already JSON-safe and is unchanged.

### Documentation
- `docs/adapter_formats.md` — added retrieval/grounding field specifications for the new `retrieved_ids`, `retrieval_scores`, and `mean_retrieval_score` fields.
- `failure_graph.yaml` — corrected node.layer rule comment to include `meta` (the layer was already in use; only the comment was outdated).
- `README.md` — restructured (570 → 498 lines), Cogency Framework Mapping updated to include all 14 domain patterns and the runtime layer table (Tool failure / Genuine LLM failure).

## [0.1.3] - 2026-04-04

### Added
- Uncertainty acknowledgement markers for service unavailability scenarios (improves `grounding.uncertainty_acknowledged` detection when tools return service errors).

## [0.1.2] - 2026-04-03

### Added
- `grounding.tool_result_diversity` — measures how varied the data returned by tool calls is. Used by the debugger's execution quality assessment to detect cases where multiple tool calls returned identical (and likely useless) data.

## [0.1.1] - 2026-04-03

### Fixed
- `callback_handler`: LangGraph `on_chain_end` message extraction. Previously, `outputs={"messages": [AIMessage(content="...")]}` fell through to `str(next(iter(outputs.values())))` which stringified the entire list. Now walks messages in reverse to find the last `AIMessage` and extracts `.content`.
- `_compute_alignment`: greeting false positive. Short queries (≤3 words) containing greeting vocabulary (hi, hello, hey, thanks, etc.) were scored by word overlap, producing low alignment and incorrectly triggering the `incorrect_output` pattern. Now returns 0.8 alignment for greeting exchanges.

## [0.1.0] - 2026-03

### Added
- Initial PyPI release.
- 17 failure patterns (14 domain + 3 meta).
- 34 signals (28 domain + 6 meta).
- Causal graph with 17 nodes and 15 edges.
- 5 source-specific adapters: `callback_handler`, `langchain`, `langsmith`, `crewai`, `redis_help_demo`.
- Deterministic matcher (no ML, no LLM calls for detection).
- Cogency Framework tag mapping for all domain patterns.

[0.1.5]: https://github.com/kiyoshisasano/llm-failure-atlas/releases/tag/v0.1.5
[0.1.4]: https://github.com/kiyoshisasano/llm-failure-atlas/releases/tag/v0.1.4