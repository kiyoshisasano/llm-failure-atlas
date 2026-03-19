# Annotation Guide

## Scoring

For each scenario, assign scores on a 0-2 scale:

### root_score
- **2**: The identified root cause is correct and the most meaningful explanation
- **1**: The root cause is partially correct or a reasonable alternative
- **0**: The root cause is wrong

### path_score
- **2**: The causal path accurately describes what went wrong
- **1**: The path is partially correct but misses a key step or includes an unnecessary one
- **0**: The path does not reflect what happened

### explanation_score
- **2**: The explanation is clear, accurate, and actionable
- **1**: The explanation is understandable but imprecise or incomplete
- **0**: The explanation is confusing, wrong, or unhelpful

## Comment field
Use the comment field for:
- Why a score is low
- Suggested corrections
- Alternative explanations that the system missed

## Special cases
- **clean scenarios**: If no failure is detected (correct), score all as 2
- **missing fields**: If the system gracefully handles missing data, score based on what it did detect
- **over-detection**: If the system detects too many failures, lower path_score
