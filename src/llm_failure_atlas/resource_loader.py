"""
resource_loader.py — Resolve paths to bundled YAML/JSON resources.

Resolution order:
  1. Environment variable override (if set and path exists)
  2. Package default via importlib.resources

Environment variables:
  LLM_FAILURE_ATLAS_PATTERNS_DIR  — directory containing *.yaml pattern files
  LLM_FAILURE_ATLAS_GRAPH_PATH    — path to failure_graph.yaml
  LLM_FAILURE_ATLAS_LEARNING_DIR  — directory containing run_history.json etc.
"""

import os
import importlib.resources as ir
from pathlib import Path


def get_patterns_dir() -> Path:
    """Resolve patterns directory: env var > package default."""
    env_dir = os.environ.get("LLM_FAILURE_ATLAS_PATTERNS_DIR")
    if env_dir:
        p = Path(env_dir)
        if p.is_dir():
            return p
    return ir.files("llm_failure_atlas.resources.failures")


def get_graph_path() -> Path:
    """Resolve failure graph path: env var > package default."""
    env_path = os.environ.get("LLM_FAILURE_ATLAS_GRAPH_PATH")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p
    return ir.files("llm_failure_atlas.resources.graph").joinpath("failure_graph.yaml")


def get_learning_dir() -> Path:
    """Resolve learning data directory: env var > package default."""
    env_dir = os.environ.get("LLM_FAILURE_ATLAS_LEARNING_DIR")
    if env_dir:
        p = Path(env_dir)
        if p.is_dir():
            return p
    return ir.files("llm_failure_atlas.resources.learning")
