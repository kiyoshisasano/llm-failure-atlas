"""
LLM Failure Atlas - Minimal Matcher (v7)
Pipeline: log → signals → modifiers → confidence → diagnosis
"""

import yaml
import json
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Field access
# ---------------------------------------------------------------------------

def get_field(log: dict, path: str):
    """Traverse dotted path. Return None if any key is missing."""
    parts = path.split(".")
    node = log
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------

def eval_rule(rule_str: str, bindings: dict) -> bool:
    """
    Evaluate a rule string against a flat bindings dict.
    Supported: comparisons (<, <=, >, >=, ==), boolean literals (true/false),
    logical operators (and, or).
    No exec/eval on user input - rule strings are controlled by pattern.yaml.
    """
    # Normalize boolean literals
    expr = rule_str.strip()
    expr = re.sub(r'\btrue\b', 'True', expr)
    expr = re.sub(r'\bfalse\b', 'False', expr)
    expr = re.sub(r'\band\b', 'and', expr)
    expr = re.sub(r'\bor\b', 'or', expr)

    # Replace dotted field references with their values
    def replace_ref(match):
        key = match.group(0)
        if key in bindings:
            val = bindings[key]
            if isinstance(val, str):
                return f'"{val}"'
            return str(val)
        return key

    # Match dotted identifiers (e.g. cache.similarity, retrieval.skipped)
    expr = re.sub(r'[a-z_][a-z_0-9]*(?:\.[a-z_][a-z_0-9]*)+', replace_ref, expr)

    # Replace bare 'value' alias
    if 'value' in bindings:
        val = bindings['value']
        expr = re.sub(r'\bvalue\b', str(val), expr)

    try:
        code = compile(expr, "<rule>", "eval")
        # Whitelist: only names that were substituted from bindings are allowed.
        # This blocks attribute access, builtins, and injected identifiers.
        allowed_names = {"True", "False"}
        for name in code.co_names:
            if name not in allowed_names:
                return False
        result = eval(code, {"__builtins__": {}})  # noqa: S307
        return bool(result)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Signal extraction
# ---------------------------------------------------------------------------

def extract_signals(pattern: dict, log: dict) -> tuple:
    """
    Extract signals from log using pattern rules.

    Returns:
        (signals_output, observation_quality) where:
          signals_output: dict of signal_name → bool (backward-compatible)
          observation_quality: dict of signal_name → {"observed": bool, "missing": bool}

    observation_quality semantics:
      observed=True:  all source fields existed and the rule was evaluated normally.
      observed=False: one or more source fields were absent (missing=True).
                      The signal value falls back to missing_field_default (currently
                      always False across all 15 patterns). Future adapters may also
                      set observed=False to indicate heuristic-inferred values.
    """
    evaluation = pattern["signal_extraction"].get("evaluation", {})
    missing_field_default = evaluation.get("missing_field", False)
    multi_field_policy = evaluation.get("multi_field_policy", "strict_all_required")

    signals_output = {}
    observation_quality = {}

    for rule_def in pattern["signal_extraction"]["rules"]:
        signal_name = rule_def["signal"]
        from_spec = rule_def["from"]
        rule_str = rule_def["rule"]

        # Normalize from to list
        if isinstance(from_spec, str):
            fields = [from_spec]
        else:
            fields = list(from_spec)

        # Collect field values
        bindings = {}
        missing_any = False

        for field_path in fields:
            val = get_field(log, field_path)
            if val is None:
                missing_any = True
            else:
                bindings[field_path] = val

        # Apply missing field policy
        if missing_any:
            if multi_field_policy == "strict_all_required":
                signals_output[signal_name] = missing_field_default  # false
                observation_quality[signal_name] = {
                    "observed": False,
                    "missing": True,
                }
                continue
            # future policies (e.g. any_available) would go here

        # Single-field: also expose as 'value' alias
        if len(fields) == 1:
            bindings["value"] = bindings.get(fields[0])

        signals_output[signal_name] = eval_rule(rule_str, bindings)
        observation_quality[signal_name] = {
            "observed": True,
            "missing": False,
        }

    return signals_output, observation_quality


# ---------------------------------------------------------------------------
# Diagnosis
# ---------------------------------------------------------------------------

def diagnose(pattern: dict, signals: dict,
             observation_quality: dict | None = None) -> dict:
    """
    Compute confidence and determine diagnosis.

    Args:
        pattern: Failure pattern definition (from YAML).
        signals: dict of signal_name → bool (backward-compatible input).
        observation_quality: Optional dict of signal_name → {"observed", "missing"}.
            When provided, unobserved signals receive a 0.6× decay on their
            confidence contribution. This is a fixed deterministic factor.
    """
    # Decay factor for signals whose source fields were missing or inferred.
    # Deterministic constant — not learned, not configurable per-pattern.
    UNOBSERVED_DECAY = 0.6

    diag = pattern["diagnosis"]
    threshold = diag["threshold"]
    conf_spec = diag["confidence"]

    confidence = float(conf_spec.get("initial", 0.0))
    applied_modifiers = []

    def _effective_add(sig_name: str, base_add: float) -> float:
        """Apply observed decay if observation_quality is available.

        When observation_quality is None (legacy callers), no decay is
        applied. When observation_quality is provided but sig_name is
        absent, the signal is treated as unobserved (decay applied).
        Unknown observation status is not promoted to observed.
        """
        if observation_quality is not None:
            sig_q = observation_quality.get(sig_name)
            # None  → key missing, treat as unobserved
            # {}    → malformed, treat as unobserved
            if sig_q is None or not sig_q.get("observed", False):
                return base_add * UNOBSERVED_DECAY
        return base_add

    # Evidence modifiers
    for mod in diag.get("evidence_modifiers", []):
        sig = mod["signal"]
        if signals.get(sig):
            add_value = _effective_add(sig, mod["add"])
            confidence += add_value
            applied_modifiers.append({
                "type": "evidence",
                "signal": sig,
                "add": add_value,
            })

    # Symptom modifiers
    for mod in diag.get("symptom_modifiers", []):
        sig = mod["signal"]
        if signals.get(sig):
            add_value = _effective_add(sig, mod["add"])
            confidence += add_value
            applied_modifiers.append({
                "type": "symptom",
                "signal": sig,
                "add": add_value,
            })

    # Clamp
    clamp = conf_spec.get("clamp", {})
    confidence = max(clamp.get("min", 0.0), min(clamp.get("max", 1.0), confidence))

    result = {
        "failure_id": pattern["failure_id"],
        "diagnosed": confidence >= threshold,
        "confidence": round(confidence, 4),
        "threshold": threshold,
        "signals": signals,
        "applied_modifiers": applied_modifiers,
    }

    if observation_quality is not None:
        result["observation_quality"] = observation_quality

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(pattern_path: str, log_path: str) -> dict:
    pattern = yaml.safe_load(Path(pattern_path).read_text(encoding="utf-8"))
    log = json.loads(Path(log_path).read_text(encoding="utf-8"))

    signals, observation_quality = extract_signals(pattern, log)
    result = diagnose(pattern, signals, observation_quality)
    return result


if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else "pattern.yaml"
    l = sys.argv[2] if len(sys.argv) > 2 else "log.json"
    result = run(p, l)
    print(json.dumps(result, indent=2))