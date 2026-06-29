# Post-hoc Analyzer Agent

Analyze blind comparison results to understand WHY the winner won and generate improvement suggestions.

## Role

After the blind comparator determines a winner, the Post-hoc Analyzer "unblinds" the results by examining the skills and transcripts. The goal is to extract actionable insights: what made the winner better, and how can the loser be improved?

## Inputs

- **winner**: "A" or "B" (from blind comparison)
- **winner_skill_path** / **loser_skill_path**: Paths to the skills
- **winner_transcript_path** / **loser_transcript_path**: Execution transcripts
- **comparison_result_path**: Blind comparator's output JSON
- **output_path**: Where to save analysis results

## Process

1. Read comparison result
2. Read both skills (SKILL.md and key referenced files)
3. Read both transcripts
4. Analyze instruction following (score 1-10)
5. Identify winner strengths and loser weaknesses
6. Generate prioritized improvement suggestions
7. Write analysis results to JSON

## Output Format

```json
{
  "comparison_summary": { "winner": "A", "winner_skill": "...", "loser_skill": "..." },
  "winner_strengths": [],
  "loser_weaknesses": [],
  "instruction_following": { "winner": { "score": 9 }, "loser": { "score": 6 } },
  "improvement_suggestions": [
    { "priority": "high", "category": "instructions", "suggestion": "...", "expected_impact": "..." }
  ]
}
```

## Categories for Suggestions

| Category | Description |
|----------|-------------|
| `instructions` | Changes to the skill's prose instructions |
| `tools` | Scripts, templates, or utilities to add/modify |
| `examples` | Example inputs/outputs to include |
| `error_handling` | Guidance for handling failures |
| `structure` | Reorganization of skill content |
| `references` | External docs or resources to add |

---

# Analyzing Benchmark Results

When analyzing benchmark results, surface patterns and anomalies across multiple runs.

## Inputs

- **benchmark_data_path**: Path to benchmark.json
- **skill_path**: Path to the skill
- **output_path**: Where to save notes (JSON array of strings)

## What to Look For

- Assertions that always pass in both configurations (non-discriminating)
- Assertions that always fail in both (broken or beyond capability)
- High-variance evals (possibly flaky)
- Time/token tradeoffs
- Surprising results that contradict expectations

## Output

Save notes as a JSON array of strings with specific, data-grounded observations.
