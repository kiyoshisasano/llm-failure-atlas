"""
base_adapter.py

Phase 24: Base adapter interface for log → matcher input conversion.

Adapters convert raw agent logs (LangChain, LangSmith, AgentOps, etc.)
into the telemetry format that matcher.py expects.

Adapters do NOT diagnose failures — they only normalize and extract features.

3-tier signal extraction:
  Tier 1: Deterministic extraction (direct field mapping)
  Tier 2: Computed features (embeddings, scores)
  Tier 3: LLM-assisted inference (optional, for ambiguous signals)
"""


class BaseAdapter:
    """
    Abstract base for all log adapters.

    Subclasses must implement:
      normalize()        — raw log → canonical structure
      extract_features() — canonical → matcher-compatible telemetry

    Usage:
        adapter = SomeAdapter()
        matcher_input = adapter.build_matcher_input(raw_log)
    """

    # Source identifier (override in subclass)
    source: str = "unknown"

    def normalize(self, raw_log: dict) -> dict:
        """
        Convert raw log format into a canonical intermediate structure.
        This handles vendor-specific field names and nesting.
        """
        raise NotImplementedError

    def extract_features(self, normalized: dict) -> dict:
        """
        Extract matcher-compatible telemetry from the normalized structure.

        Output must match the log format expected by matcher.py:
        {
            "input": {"ambiguity_score": ...},
            "interaction": {"clarification_triggered": ..., ...},
            "reasoning": {"replanned": ...},
            "cache": {"hit": ..., "similarity": ..., "query_intent_similarity": ...},
            "retrieval": {"skipped": ...},
            "response": {"alignment_score": ...},
            "tools": {"call_count": ..., "repeat_count": ..., ...}
        }
        """
        raise NotImplementedError

    def build_matcher_input(self, raw_log: dict) -> dict:
        """
        Full pipeline: raw log → normalized → matcher input.

        Returns the telemetry dict that matcher.py can consume directly.
        """
        normalized = self.normalize(raw_log)
        features = self.extract_features(normalized)
        return features

    def build_with_metadata(self, raw_log: dict) -> dict:
        """
        Build matcher input with source metadata attached.

        Returns:
        {
            "telemetry": {...},   # matcher-compatible
            "metadata": {
                "source": "langchain",
                "adapter_version": "1.0",
                ...
            }
        }
        """
        telemetry = self.build_matcher_input(raw_log)
        return {
            "telemetry": telemetry,
            "metadata": {
                "source": self.source,
                "adapter_version": "1.0",
            },
        }
