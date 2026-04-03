"""
llm_failure_atlas — Detection and pattern library for LLM agent failures.

Core API:
    from llm_failure_atlas.matcher import run
    from llm_failure_atlas.resource_loader import get_patterns_dir, get_graph_path
"""

__version__ = "0.1.2"

from llm_failure_atlas.resource_loader import get_patterns_dir, get_graph_path