# 2026 Best Practices Rubric (34 points)

Consumed by: `.claude/skills/workspace-deep-audit/SKILL.md` Phase 4.

**Last Updated:** 2026-05-15
**Refresh cadence:** every 90 days. Refresh by running a WebSearch sweep against the Anthropic engineering blog, OWASP Agentic risk list, Braintrust observability articles, Helicone/LangSmith comparison posts, and any new Anthropic cookbook publications. Add new best-practice rows; do NOT remove rows without CEO approval.

Each point scored as:
- **LEAD** - workspace implements the practice better than the public reference standard
- **MATCH** - workspace meets the standard
- **GAP** - workspace lacks the practice or has known shortfall
- **N/A** - practice does not apply to this workspace's scope (e.g., MCP OAuth when workspace doesn't expose MCP)

---

## Category 1 - Anthropic Official Guidance (7 points)

| # | Best practice | How to score |
|---|---|---|
| 1 | CLAUDE.md ≤200 lines, nested per-dir CLAUDE.md where scope demands | Check workspace CLAUDE.md word count. LEAD if ≤200 and rules cover similar role; MATCH if ≤300; GAP if >300 |
| 2 | Skill/rule descriptions specific and testable, never aspirational | Sample 10 rules + 10 skills. Count those with concrete observable triggers vs vague language. LEAD if ≥18/20 specific |
| 3 | Two-agent runtime pattern (initializer + working agent + progress.txt) for tasks > 1 context window | Check for explicit pattern in orchestrator skills. GAP if absent; MATCH if compaction + persistent state used in lieu; LEAD if explicit pattern present |
| 4 | Separate generation from evaluation into distinct agents | Check if `/scrutinize`, `/evaluate`, `/council` exist as distinct from generation skills. LEAD if all 3 present |
| 5 | Anchor compaction; never regenerate from scratch | Auto-handled by Claude Code; check for auto-memory + plans + threads as persistent layers. MATCH baseline |
| 6 | Compact at 70% context fill, not at the wall | Check `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` in settings. MATCH if set in 70-90 range |
| 7 | Tier models by task complexity (Opus/Sonnet/Haiku) | Count `model:` fields in SKILL.md frontmatter. LEAD if 30+ skills declare model |

---

## Category 2 - Skill & Agent Design (7 points)

| # | Best practice | How to score |
|---|---|---|
| 1 | Skill descriptions "pushy" and trigger-rich | Sample 10 skill descriptions. LEAD if 8+ have explicit trigger phrases AND explicit "do NOT use for" exclusions |
| 2 | SKILL.md ≤500 lines + progressive disclosure via references/ | Count skills ≤300, ≤500, >500. LEAD if ≥95% ≤300; MATCH if ≥90% ≤500; GAP if ≥5% >500 |
| 3 | Use orchestrator-subagent as default multi-agent pattern | Check `.claude/rules/skill-orchestrator.md` exists and has ≥5 patterns. LEAD if 7+ patterns documented |
| 4 | Run subagents on isolated contexts | Check if Agent dispatch is used in orchestrator patterns. LEAD if every dispatched agent gets self-contained prompt |
| 5 | Build evals/ directory inside every skill (Anthropic skill-creator workflow) | Count skills with `evals/cases/` AND `evals/benchmark.json`. LEAD if 10+ critical skills covered; MATCH if 5-9; GAP if <5 |
| 6 | Wave-dispatch parallel agents with concurrency caps | Check `skill-orchestrator.md` Principle 5 (cap ≤5). LEAD if explicit cap + wave-batching documented |
| 7 | Treat plugins as first-class composition units | Count enabled plugins in `.claude/settings.json`. LEAD if 6+ plugins integrated; MATCH if 3-5 |

---

## Category 3 - Cost Optimization (5 points)

| # | Best practice | How to score |
|---|---|---|
| 1 | Static-prefix + dynamic-tail for prompt caching | Grep for `cache_control` in scripts. LEAD if every Anthropic SDK caller uses it; MATCH if 2/3; GAP if <50% |
| 2 | 5-min TTL default; 1h only when paused >5 min | Check pattern docs - LEAD if `ephemeral` (5-min) is default and 1h only on specific calls |
| 3 | Combine caching + Batch API for ~95% savings | N/A for real-time CEO workflow; only LEAD if batch jobs exist |
| 4 | Per-task / per-user cost caps in harness | Check for `check_tool_budget` or equivalent in `_dispatch.py`. LEAD if 30-min rolling + same-args repeat + per-tool cap |
| 5 | Stream only on user-facing surfaces | Verify Claude Code default + daemons don't stream. MATCH if true |

---

## Category 4 - Observability & Eval (5 points)

| # | Best practice | How to score |
|---|---|---|
| 1 | Score trajectories, not just outputs | Check if Langfuse traces capture full LLM call chain. MATCH if `@observe()` decorators present |
| 2 | Close production-to-eval flywheel (issue → regression test) | Check if `/scrutinize` findings auto-promote to eval cases. GAP if no automated promotion; MATCH if manual workflow documented |
| 3 | Pick observability by primary pain | Check for production observability tool. LEAD if Langfuse / LangSmith / Braintrust / Helicone integrated with vault-aware disable rule |
| 4 | Separate offline (pre-release) and online (production drift) evals | Check if eval suite has both. GAP if only offline; MATCH if both |
| 5 | Budget eval cost explicitly | N/A unless eval framework runs autonomously |

---

## Category 5 - Security & Compliance (6 points)

| # | Best practice | How to score |
|---|---|---|
| 1 | Treat retrieved content as untrusted data | Check for adversarial CI suite + prompt-guard hook. LEAD if both present |
| 2 | Avoid Lethal Trifecta (private data + untrusted input + exfiltration) | Check vault system + classification + adversarial test for exfiltration. LEAD if all 3 |
| 3 | PreToolUse hooks as deterministic security gates | Count `check_*` functions in `_dispatch.py`. LEAD if ≥5 checks with per-check try/except |
| 4 | Combine PreToolUse + PostToolUse + pre-commit | Count gates across all 3 stages. LEAD if 20+ total gates |
| 5 | Standardize on OAuth 2.1 for MCP | N/A if workspace doesn't expose MCP |
| 6 | Inventory + audit every credential | Check `.env` gitignored, `pre-commit secret-scanner`, password manager policy. LEAD if all 3 + rotation policy |

---

## Category 6 - Multi-User / Enterprise (4 points)

| # | Best practice | How to score |
|---|---|---|
| 1 | Agent governance as first-class platform layer | Check routing-map.yaml + write-isolation + permissions. LEAD if all three formalized |
| 2 | Adversarial testing in CI on prompt/model swap | Check `tests/security/prompt-injection/`. LEAD if 3+ attacks defended; GAP if absent |
| 3 | Human-in-the-loop on high-stakes output | Check approval gates in `/push-updates`, `/email-respond`, drafting skills. MATCH if soft gates; LEAD if hard gates with explicit "send" / "go" approval |
| 4 | Default to zero-data-retention for strategic context | Check vault rule + observability disable in vault. LEAD if both layers present |

---

## Anti-Practices for This Workspace

Some 2026 industry trend lists name practices that **structurally do not apply** to a file-sovereign CEO workspace. The audit should NOT score these as GAPs even when industry sources call them best practice. These were pattern-matched into v1 audit (P3.4) and excluded by CEO 2026-05-15 after architectural analysis.

| Industry trend | Why it doesn't apply here | Correct audit verdict |
|---|---|---|
| "Adopt a persistent memory layer (Mem0 / Letta / Cognee)" | Workspace has 4-layer memory architecture (auto-memory + Odin brain + knowledge + DataStore), all file-based and git-tracked. DB-backed memory breaks file sovereignty, classification, vault, and multi-exec sync. | **N/A — architectural mismatch.** Do not score as GAP. |
| "Use cloud-hosted vector DB (Pinecone / Weaviate Cloud / Zep Cloud)" | Violates workspace sovereignty principle (CEO data stays local + git). Same air-gap principle as `_secure/` vault. | **N/A — sovereignty mismatch.** |
| "Add knowledge graph DB as primary substrate" | Files are ground truth. Graph DB as substrate breaks all 7 layers of defense-in-depth. | **N/A — substrate mismatch.** |
| "Integrate with AI CoS SaaS (Carly / Sintra / Lindy)" | These are substitute products, not complementary integrations. | **N/A — they're competitors, not tools.** |
| "Expose workspace as MCP server" (P3.2 from v1) | EXPLICITLY EXCLUDED by CEO. Workspace stays CEO-personal. | **EXCLUDED — do not re-propose.** |
| "Build web UI / mobile companion" (P3.3 from v1) | EXPLICITLY EXCLUDED by CEO. CLI / Claude Code surface remains primary. | **EXCLUDED — do not re-propose.** |
| "Public commercial offering / pricing model" (P3.5 from v1) | EXPLICITLY EXCLUDED by CEO. ceo-main stays personal. | **EXCLUDED — do not re-propose.** |

**Allowed alternative when retrieval need arises:** **read-only vector index pointing at existing files** (e.g., ChromaDB embedded mode persisting to `knowledge/.index/`, opt-in `--semantic` flag on `/odin recall`). Files remain ground truth. Index is regeneratable, gitignored, respects classification. Trigger condition: knowledge corpus exceeds **~1500 entries** OR semantic-query pattern appears repeatedly in CEO usage. Current scale (430 entries) is below threshold.

**Audit discipline:** Before recommending any new substrate / SaaS / external system, ask three filtering questions:

1. Does it preserve **file sovereignty** (artifact stays as grep-able markdown)?
2. Does it preserve **routing-map.yaml semantics** (per-file engine/private/corporate routing)?
3. Does it preserve **multi-exec sync** (can propagate via corporate repo git push)?

If any answer is "no," the recommendation is structurally incompatible and must be marked **N/A — architectural mismatch**, not GAP.

---

## Scoring Summary Format

After scoring all 34 points, produce a summary table:

| Category | LEAD | MATCH | GAP | N/A | Total |
|---|---|---|---|---|---|
| Anthropic Official | X | X | X | X | 7 |
| Skill & Agent Design | X | X | X | X | 7 |
| Cost Optimization | X | X | X | X | 5 |
| Observability & Eval | X | X | X | X | 5 |
| Security & Compliance | X | X | X | X | 6 |
| Multi-User/Enterprise | X | X | X | X | 4 |
| **TOTAL** | **X** | **X** | **X** | **X** | **34** |

Then compute percentages of applicable items (excluding N/A):

- LEAD %
- MATCH %
- GAP %

If running `--vs <prev_audit>`, add a delta column showing v(prev) → v(current) for each category.

---

## Refresh Trigger

This rubric expires 90 days after `Last Updated`. To refresh:

1. Run WebSearch sweeps:
   - `site:anthropic.com engineering 2026 best practices`
   - `site:owasp.org agentic AI risks 2026`
   - `Claude Code production patterns 2026`
   - `LLM observability comparison 2026`
   - `prompt caching best practices 2026`
2. Identify any new canonical practices published since `Last Updated`
3. Propose rubric additions/changes inline in the audit output, then update this file with CEO approval
4. Bump `Last Updated` date
