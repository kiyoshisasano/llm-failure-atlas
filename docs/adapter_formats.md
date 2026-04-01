# Adapter Input Format Specification

Each adapter converts a raw log from a specific framework into the telemetry format that the Atlas matcher consumes. This document shows the expected input shape for each adapter, including common invalid inputs.

## Telemetry Contract (what the matcher receives)

All adapters produce this same output structure:

```python
{
    "input":       {"ambiguity_score": float},
    "interaction": {"clarification_triggered": bool, "user_correction_detected": bool},
    "reasoning":   {"replanned": bool, "hypothesis_count": int},
    "cache":       {"hit": bool, "similarity": float, "query_intent_similarity": float},
    "retrieval":   {"skipped": bool},
    "response":    {"alignment_score": float},
    "tools":       {"call_count": int, "repeat_count": int, "soft_error_count": int, ...},
    "state":       {"progress_made": bool, "tool_progress": dict,
                    "any_tool_looping": bool, "output_produced": bool,
                    "chain_error_occurred": bool},
    "grounding":   {"tool_provided_data": bool, "uncertainty_acknowledged": bool,
                    "response_length": int, "source_data_length": int,
                    "expansion_ratio": float},
}
```

Missing fields default to `False` (for booleans) or `0` (for numbers). Patterns that depend on missing fields won't fire — this is by design.

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
          {"content": "...", "score": 0.72}
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

This adapter is specific to the [Redis movie-recommender workshop](https://github.com/redis-developer/movie-recommender-rag-semantic-cache-workshop). It is NOT a general-purpose Redis adapter.

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