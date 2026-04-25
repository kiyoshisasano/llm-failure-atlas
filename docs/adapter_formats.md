# Adapter Input Format Specification

Each adapter converts a raw log from a specific framework into the telemetry format that the Atlas matcher consumes. This document shows the expected input shape for each adapter, including common invalid inputs.

## Telemetry Contract (what the matcher receives)

All adapters produce this same output structure. Fields marked `# optional` are produced by some adapters but not all — patterns that depend on them will not fire if absent.

```python
{
    "input":       {"ambiguity_score": float},
    "interaction": {"clarification_triggered": bool, "user_correction_detected": bool},
    "reasoning":   {"replanned": bool, "hypothesis_count": int,
                    # optional: "contradiction_detected": bool, "hypothesis_abandoned": bool
                   },
    "cache":       {"hit": bool, "similarity": float, "query_intent_similarity": float},
    "retrieval":   {"skipped": bool, "retrieved_doc_count": int,
                    "retrieved_ids": list | None,
                    # optional: "retrieval_scores": list | None,
                    #           "mean_retrieval_score": float | None,
                    #           "contains_instruction": bool, "override_detected": bool,
                    #           "adversarial_score": float, "expected_coverage": float
                   },
    "response":    {"alignment_score": float},
    "tools":       {"call_count": int, "repeat_count": int, "soft_error_count": int,
                    # optional: "error_count": int, "unique_tools": int
                   },
    "state":       {"progress_made": bool, "tool_progress": dict,
                    "any_tool_looping": bool, "output_produced": bool,
                    "chain_error_occurred": bool},
    "grounding":   {"tool_provided_data": bool, "uncertainty_acknowledged": bool,
                    "response_length": int, "source_data_length": int,
                    "expansion_ratio": float, "tool_result_diversity": float,
                    "retrieved_ids": list | None},
    # optional sections (adapter-dependent):
    # "context":     {"truncated": bool, "critical_info_present": bool,
    #                 "external_instruction_weight": float},
    # "instruction": {"system_priority_respected": bool},
    # "output":      {"repair_attempted": bool, "regenerated": bool, "repair_quality": float},
}
```

Missing fields default to `False` (for booleans) or `0` (for numbers). Patterns that depend on missing fields won't fire — this is by design.

---

## Retrieval Output Specification

The `retrieval` section captures document retrieval behavior.

| Field | Type | Description |
|---|---|---|
| `skipped` | bool | Whether retrieval was skipped or no retriever step exists |
| `retrieved_doc_count` | int | Number of documents returned by retrievers |
| `retrieved_ids` | list[str] or null | Document/chunk IDs from retriever outputs, when available |
| `retrieval_scores` | list[float] or null | Similarity scores from retriever outputs, when available |
| `mean_retrieval_score` | float or null | Mean of retrieval_scores, when available |

`retrieved_ids` are extracted by checking each document for keys in order: `chunk_id`, `id`, `doc_id`, `document_id`. The first match is used. If no ID keys are present, `retrieved_ids` is null.

`retrieval_scores` are extracted from `score` or `similarity_score` fields in each document. If no scores are present, both fields are null.

---

## Grounding Output Specification

The `grounding` section captures evidence quality signals for the agent's response.

| Field | Type | Description |
|---|---|---|
| `tool_provided_data` | bool | Whether any tool returned usable (non-error) data |
| `uncertainty_acknowledged` | bool | Whether the response contains uncertainty markers |
| `response_length` | int | Character count of the agent's response |
| `source_data_length` | int | Total character count of usable tool outputs |
| `expansion_ratio` | float | `response_length / source_data_length`. Infinity if source is 0 but response exists |
| `tool_result_diversity` | float or null | Fraction of unique tool outputs across calls. Null if fewer than 2 usable outputs |
| `retrieved_ids` | list[str] or null | Chunk/document IDs extracted from tool outputs, when available. Null if no IDs found |

### Interpretation Guide

- `tool_provided_data = False` + `expansion_ratio = inf`: Agent generated response with zero source data (P0 risk)
- `tool_provided_data = True` + high `expansion_ratio`: Response significantly exceeds source material
- `uncertainty_acknowledged = False` + `tool_provided_data = False`: Agent did not disclose data absence
- `retrieved_ids != null`: Enables join with upstream retrieval quality systems (e.g., chunk quality scoring)

### Where `retrieved_ids` appear

IDs can appear in both `retrieval` and `grounding` sections:

- `retrieval.retrieved_ids` — extracted from retriever-type steps (explicit RAG retrieval)
- `grounding.retrieved_ids` — extracted from tool-type steps that return documents or chunk_ids

In many architectures, retrieval is done via tools rather than dedicated retriever steps. Both extraction paths are provided so that IDs are captured regardless of how the agent's retrieval is implemented.

---

## `langchain` Adapter

**Use with:** `diagnose(raw_log, adapter="langchain")`

**Expected input:**

```json
{
  "inputs": {"query": "User question here"},
  "outputs": {"response": "Agent response here"},
  "steps": [
    {
      "type": "llm",
      "name": "ChatOpenAI",
      "inputs": {"prompt": "..."},
      "outputs": {"text": "..."},
      "metadata": {"model": "gpt-4"}
    },
    {
      "type": "retriever",
      "name": "VectorStoreRetriever",
      "inputs": {"query": "..."},
      "outputs": {
        "documents": [
          {"content": "...", "score": 0.72, "chunk_id": "doc_001"}
        ]
      },
      "metadata": {
        "cache_hit": true,
        "cache_similarity": 0.89
      }
    },
    {
      "type": "tool",
      "name": "search_flights",
      "inputs": {"date": "2025-03-20"},
      "outputs": {"flights": []},
      "error": null
    }
  ],
  "feedback": {
    "user_correction": "I asked about X, not Y.",
    "user_rating": 1
  },
  "latency_ms": 4500
}
```

**Key fields:**

| Field | Required | Used for |
|---|---|---|
| `inputs.query` | Recommended | Ambiguity scoring, alignment |
| `outputs.response` | Recommended | Alignment, grounding |
| `steps[].type` | Required | Step classification (llm/retriever/tool) |
| `steps[].outputs` | Required | Tool data detection, grounding |
| `steps[].outputs.documents[].chunk_id` | Optional | Chunk ID extraction for retrieval join |
| `steps[].outputs.chunk_ids` | Optional | Alternative chunk ID extraction |
| `steps[].error` | Optional | Error counting |
| `feedback.user_correction` | Optional | Correction detection |

**Common invalid input:**

```json
{"answer": "The revenue was $4.2M"}
```

This will produce empty telemetry and detect nothing. The langchain adapter requires `steps` with `type` fields, and `inputs`/`outputs` at the top level.

**What won't fire with minimal input:** `agent_tool_call_loop` (no tool steps), `semantic_cache_intent_bleeding` (no cache metadata), `context_truncation_loss` (no retriever), `prompt_injection_via_retrieval` (no documents).

---

## `langsmith` Adapter

**Use with:** `diagnose(raw_log, adapter="langsmith")`

**Expected input:** LangSmith run-tree export (hierarchical):

```json
{
  "run_type": "chain",
  "inputs": {
    "messages": [
      {"id": ["HumanMessage"], "content": "User question"}
    ]
  },
  "outputs": {
    "messages": [
      {"id": ["AIMessage"], "content": "Agent response"}
    ]
  },
  "child_runs": [
    {
      "run_type": "llm",
      "inputs": {"prompts": ["..."]},
      "outputs": {"generations": [{"text": "..."}]}
    },
    {
      "run_type": "tool",
      "name": "search",
      "inputs": {"query": "..."},
      "outputs": {"result": "..."},
      "error": null
    }
  ],
  "feedback_stats": {}
}
```

The adapter recursively flattens `child_runs` by `run_type`.

**Common invalid input:**

```json
{
  "steps": [{"type": "llm", "output": "..."}]
}
```

This is a langchain-format log. The langsmith adapter expects `child_runs` with `run_type`, not `steps` with `type`. No error is raised — it silently returns zero failures.

---

## `crewai` Adapter

**Use with:** `diagnose(raw_log, adapter="crewai")` or via `CrewAIAdapter().from_crew_output(crew_output, tasks)`

**Two modes:**

1. **Post-hoc** (from CrewOutput):
```python
from llm_failure_atlas.adapters.crewai_adapter import CrewAIAdapter
adapter = CrewAIAdapter()
telemetry = adapter.from_crew_output(crew_output, tasks)
```

2. **Real-time** (event listener):
```python
from llm_failure_atlas.adapters.crewai_adapter import AtlasCrewListener
listener = AtlasCrewListener(auto_diagnose=True)
crew.kickoff()
```

**Known limitation:** The CrewAI adapter does not yet produce `state` or `grounding` telemetry. Patterns that depend on these fields (e.g., `agent_tool_call_loop`) may not fire.

**Common invalid input:**

```json
{
  "inputs": {"query": "..."},
  "steps": [{"type": "llm", "outputs": {"text": "..."}}]
}
```

This is langchain format. The crewai adapter expects a CrewOutput object or CrewAI event data, not a flat step list.

---

## `redis_help_demo` Adapter

**Use with:** `diagnose(raw_log, adapter="redis_help_demo")`

**Expected input** (from `/api/help/chat` endpoint):

```json
{
  "answer": "Response text here",
  "sources": [
    {"content": "Source document text", "similarity": 0.85}
  ],
  "from_cache": true,
  "cache_similarity": 0.92,
  "response_time_ms": 150,
  "token_usage": {"prompt_tokens": 100},
  "blocked": false
}
```

This adapter is specific to the [Redis movie-recommender workshop](https://github.com/bhavana-giri/movie-recommender-rag-semantic-cache-workshop). It is NOT a general-purpose Redis adapter.

**Common invalid input:**

```json
{
  "steps": [{"type": "llm", "outputs": {"text": "..."}}]
}
```

This is langchain format. The redis adapter expects `answer`, `sources`, and `from_cache` fields.

---

## Bypassing Adapters (Direct Telemetry)

If you already have telemetry in the matcher format, or want to test specific signal combinations, bypass adapters entirely. See the "Advanced / debugging" section in the [Quick Start Guide](quickstart.md).

---

## Writing a Custom Adapter

If your agent uses a custom orchestration (not LangChain, CrewAI, etc.), you can write an adapter that maps your trace schema to Atlas's telemetry format. The simplest reference implementation is `langchain_adapter.py`.

### Step 1: Subclass BaseAdapter

```python
from llm_failure_atlas.adapters.base_adapter import BaseAdapter

class MyAdapter(BaseAdapter):
    source = "my_platform"

    def normalize(self, raw_log: dict) -> dict:
        """Convert your raw log into an intermediate structure.
        Extract: user query, agent response, tool calls, errors, feedback."""
        return {
            "query": raw_log.get("user_input", ""),
            "response": raw_log.get("agent_output", ""),
            "tool_calls": raw_log.get("tool_calls", []),
            "errors": raw_log.get("errors", []),
            "feedback": raw_log.get("feedback", {}),
        }

    def extract_features(self, normalized: dict) -> dict:
        """Map your intermediate structure to the telemetry contract."""
        return {
            "input": self._build_input(normalized),
            "interaction": self._build_interaction(normalized),
            "reasoning": self._build_reasoning(normalized),
            "cache": self._build_cache(normalized),
            "retrieval": self._build_retrieval(normalized),
            "response": self._build_response(normalized),
            "tools": self._build_tools(normalized),
            "state": self._build_state(normalized),
            "grounding": self._build_grounding(normalized),
        }
```

### Step 2: Understand the telemetry contract

All adapters produce the same output structure. Missing fields default to `False`/`0` and patterns that depend on them will not fire — this is safe but reduces coverage.

The telemetry sections, with their fields and how to compute them:

**`input`** — User request characteristics

| Field | Type | How to compute |
|---|---|---|
| `ambiguity_score` | float (0–1) | Word count normalization + pronoun/vague term density. Higher = more ambiguous. Simple approach: `min(1.0, word_count * 0.05 + pronoun_count * 0.15)` |

**`interaction`** — User-agent interaction signals

| Field | Type | How to compute |
|---|---|---|
| `clarification_triggered` | bool | Agent response contains clarification phrases ("could you clarify", "did you mean", "which one", etc.) |
| `user_correction_detected` | bool | Feedback contains user correction, or agent response admits a mistake and pivots |

**`reasoning`** — Agent reasoning behavior

| Field | Type | How to compute |
|---|---|---|
| `replanned` | bool | Agent changed approach after initial attempt (replanning markers in LLM output) |
| `hypothesis_count` | int | Number of candidate interpretations considered (default: 1) |

**`cache`** — Semantic cache behavior (set all to defaults if no cache)

| Field | Type | How to compute |
|---|---|---|
| `hit` | bool | Cache was used for this query |
| `similarity` | float (0–1) | Cache similarity score |
| `query_intent_similarity` | float (0–1) | Intent similarity between cached query and current query. Lower = more likely intent mismatch |

**`retrieval`** — Retrieval/RAG behavior

| Field | Type | How to compute |
|---|---|---|
| `skipped` | bool | Retrieval step was skipped (e.g., due to cache hit) |
| `retrieved_doc_count` | int | Number of documents returned by retrievers. 0 if no retriever step |
| `retrieved_ids` | list[str] or null | Chunk/document IDs from retriever outputs. Check keys: `chunk_id`, `id`, `doc_id`, `document_id` |
| `retrieval_scores` | list[float] or null | Similarity scores from documents. Check keys: `score`, `similarity_score` |
| `mean_retrieval_score` | float or null | Mean of retrieval_scores |

**`response`** — Output quality signals

| Field | Type | How to compute |
|---|---|---|
| `alignment_score` | float (0–1) | Word overlap between query and response, minus topic-mismatch and negation penalties |

**`tools`** — Tool call patterns

| Field | Type | How to compute |
|---|---|---|
| `call_count` | int | Total tool invocations |
| `repeat_count` | int | Max times the same tool was called with the same arguments, minus 1 |
| `soft_error_count` | int | Tool calls that returned successfully but output contains error markers ("error", "not found", "empty", "no results", etc.) |
| `error_count` | int | Tool calls that raised exceptions |

**`state`** — Execution state (important for tool loop and termination detection)

| Field | Type | How to compute |
|---|---|---|
| `progress_made` | bool | At least one tool returned usable (non-error) output |
| `any_tool_looping` | bool | Any single tool called 3+ times with zero successes |
| `tool_progress` | dict | Per-tool breakdown: `{tool_name: {calls, successes, failures, progress}}` |
| `output_produced` | bool | Agent produced a non-empty final response |
| `chain_error_occurred` | bool | Execution ended with an exception |

**`grounding`** — Evidence grounding (important for hallucination-adjacent detection)

| Field | Type | How to compute |
|---|---|---|
| `tool_provided_data` | bool | At least one tool returned non-error, non-empty output |
| `uncertainty_acknowledged` | bool | Response contains uncertainty language ("couldn't find", "based on general", "no results", etc.) |
| `response_length` | int | Character count of final response |
| `source_data_length` | int | Total character count of usable tool outputs |
| `expansion_ratio` | float | `response_length / source_data_length` (0.0 if no source data and no response, inf if response but no source data) |
| `tool_result_diversity` | float or null | Unique tool outputs / total tool calls. Null when no usable tool outputs. 1.0 for single call. Low values (< 0.5 with 2+ calls) indicate redundant tool calls — the agent may have supplemented with unsupported content |
| `retrieved_ids` | list[str] or null | Chunk/document IDs from tool outputs (not retriever steps). Check `documents[].chunk_id` and `chunk_ids` fields |

### Step 3: Know which patterns fire from which fields

Not all fields are needed. Implement what your trace exposes and the corresponding patterns will activate:

| Pattern | Required fields |
|---|---|
| `clarification_failure` | `input.ambiguity_score`, `interaction.clarification_triggered`, `reasoning.hypothesis_count` |
| `premature_model_commitment` | `input.ambiguity_score`, `interaction.clarification_triggered`, `interaction.user_correction_detected`, `reasoning.replanned` |
| `incorrect_output` | `response.alignment_score`, `interaction.user_correction_detected`, `grounding.*` |
| `agent_tool_call_loop` | `tools.repeat_count`, `state.any_tool_looping`, `reasoning.replanned` |
| `premature_termination` | `state.output_produced`, `state.chain_error_occurred`, `tools.call_count` |
| `failed_termination` | `state.output_produced`, `state.chain_error_occurred` |
| `semantic_cache_intent_bleeding` | `cache.hit`, `cache.similarity`, `cache.query_intent_similarity`, `retrieval.skipped`, `response.alignment_score` |
| `rag_retrieval_drift` | `cache.hit`, `cache.similarity`, `retrieval.skipped`, `response.alignment_score` |
| `prompt_injection_via_retrieval` | `retrieval.contains_instruction`, `retrieval.override_detected`, `retrieval.adversarial_score` |
| `context_truncation_loss` | `context.truncated`, `context.critical_info_present`, `retrieval.expected_coverage` |
| `instruction_priority_inversion` | `instruction.system_priority_respected`, `context.external_instruction_weight` |
| `repair_strategy_failure` | `output.repair_attempted`, `output.regenerated`, `output.repair_quality` |
| `assumption_invalidation_failure` | `reasoning.hypothesis_count`, `reasoning.contradiction_detected`, `reasoning.hypothesis_abandoned` |
| `tool_result_misinterpretation` | `tool.output_valid`, `tool.output_value`, `state.updated_correctly`, `agent.decision_value` (no adapter currently produces these) |

### Step 4: Minimal viable adapter

If your trace has tool calls and a final response but no cache or retrieval, a minimal adapter that implements only `tools`, `state`, `grounding`, `response`, `interaction`, and `input` will still detect: `incorrect_output`, `agent_tool_call_loop`, `premature_termination`, `failed_termination`, `clarification_failure`, and `premature_model_commitment`.

Cache, retrieval, and instruction fields can return defaults:

```python
def _build_cache(self, normalized):
    return {"hit": False, "similarity": 0.0, "query_intent_similarity": 1.0}

def _build_retrieval(self, normalized):
    return {"skipped": False, "retrieved_doc_count": 0, "retrieved_ids": None}
```

### Step 5: Register and use

Your adapter does not need to be registered in any central file. Use it directly:

```python
adapter = MyAdapter()
matcher_input = adapter.build_matcher_input(raw_log)
```

Or with the full pipeline via direct telemetry (see [Quick Start Guide](quickstart.md#advanced--debugging-direct-telemetry)).

To use with `diagnose()`, add your adapter class to `diagnose.py`'s `_ADAPTERS` dict, or bypass `diagnose()` and call `run_pipeline()` directly with your matcher output.