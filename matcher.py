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

def extract_signals(pattern: dict, log: dict) -> dict:
    """
    Returns dict: signal_name → bool
    Applies missing_field and multi_field_policy from evaluation block.
    """
    evaluation = pattern["signal_extraction"].get("evaluation", {})
    missing_field_default = evaluation.get("missing_field", False)
    multi_field_policy = evaluation.get("multi_field_policy", "strict_all_required")

    signals = {}

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
                signals[signal_name] = missing_field_default  # false
                continue
            # future policies (e.g. any_available) would go here

        # Single-field: also expose as 'value' alias
        if len(fields) == 1:
            bindings["value"] = bindings.get(fields[0])

        signals[signal_name] = eval_rule(rule_str, bindings)

    return signals


# ---------------------------------------------------------------------------
# Diagnosis
# ---------------------------------------------------------------------------

def diagnose(pattern: dict, signals: dict) -> dict:
    diag = pattern["diagnosis"]
    threshold = diag["threshold"]
    conf_spec = diag["confidence"]

    confidence = float(conf_spec.get("initial", 0.0))
    applied_modifiers = []

    # Evidence modifiers
    for mod in diag.get("evidence_modifiers", []):
        sig = mod["signal"]
        if signals.get(sig):
            confidence += mod["add"]
            applied_modifiers.append({
                "type": "evidence",
                "signal": sig,
                "add": mod["add"]
            })

    # Symptom modifiers
    for mod in diag.get("symptom_modifiers", []):
        sig = mod["signal"]
        if signals.get(sig):
            confidence += mod["add"]
            applied_modifiers.append({
                "type": "symptom",
                "signal": sig,
                "add": mod["add"]
            })

    # Clamp
    clamp = conf_spec.get("clamp", {})
    confidence = max(clamp.get("min", 0.0), min(clamp.get("max", 1.0), confidence))

    return {
        "failure_id": pattern["failure_id"],
        "diagnosed": confidence >= threshold,
        "confidence": round(confidence, 4),
        "threshold": threshold,
        "signals": signals,
        "applied_modifiers": applied_modifiers
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(pattern_path: str, log_path: str) -> dict:
    pattern = yaml.safe_load(Path(pattern_path).read_text(encoding="utf-8"))
    log = json.loads(Path(log_path).read_text(encoding="utf-8"))

    signals = extract_signals(pattern, log)
    result = diagnose(pattern, signals)
    return result


if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else "pattern.yaml"
    l = sys.argv[2] if len(sys.argv) > 2 else "log.json"
    result = run(p, l)
    print(json.dumps(result, indent=2))