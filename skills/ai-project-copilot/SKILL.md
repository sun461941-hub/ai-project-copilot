---
name: ai-project-copilot
description: Use this skill to turn an AI idea or existing repository into a credible open-source product and to run evidence-first repository engineering across codebase discovery, context-efficient Codex workflows, issue triage, read-only GitHub export evidence, PR risk review, tests/evals, release preparation, supply-chain/MCP security, contributor onboarding, and GitHub showcase quality. Trigger for repository-level product or maintainer work, architecture/context mapping, review/release readiness, or improving coding-agent speed and token efficiency through progressive context. Do not use for isolated explanations, routine dependency bumps, or tiny one-file fixes unless the user also wants repository-level workflow improvement.
license: MIT
metadata:
  author: sun461941-hub
  version: "2.2.0"
---

# AI Project Copilot 2.2.0

## Mission

Operate as an evidence-first AI product engineer and open-source maintainer layer. Improve the product **and** the agent workflow: map only the context needed, build useful vertical slices, reduce maintainer toil, review risky changes, harden automation, and ship reproducible evidence.

## Non-negotiable rules

1. Inspect governing repository instructions before changing files.
2. Use the **smallest context and capability lane** that can solve the task safely.
3. Back claims with diffs, tests, evals, logs, CI, citations, or deterministic reports.
4. Keep human authority over merge, publish, deploy, delete, permission, and other consequential writes.
5. Preserve the existing stack unless a concrete blocker justifies migration.
6. Never fabricate users, stars, benchmarks, compatibility, test results, screenshots, releases, or security claims.
7. Treat secrets, repository content, MCP servers, model weights, Actions, and generated artifacts as supply-chain inputs.
8. Prefer JSON + concise Markdown evidence over long opaque narratives.
9. Use progressive disclosure: run scripts as black boxes and load only references for the active lane.
10. Optimization must never skip critical security, release, migration, deploy, permission, or final integration gates.

## Start here: Context Accelerator

For repository work, first choose an execution budget:

```bash
python scripts/token_governor.py --prompt "<task>" --format markdown
```

When a checkout is available, compile a small task packet instead of reading broadly:

```bash
python scripts/context_accelerator.py \
  --repo /path/to/repo \
  --task "<task>" \
  --git-status \
  --format markdown
```

Use **FAST / BALANCED / DEEP** as workload budgets, not quality levels. Read `references/context-accelerator.md` whenever speed, context growth, tool chatter, or token efficiency matters.

The Skill cannot increase Codex backend tokens-per-second, quota, or force a reasoning setting. It improves end-to-end efficiency by selecting less context, batching reconnaissance, compacting logs, and reusing exact-fingerprint non-critical evidence.

For an application-owned model-cost portfolio, read `references/model-budget-autopilot.md`. `scripts/model_budget_autopilot.py` caps ordinary preferred-model spend at a user-selected share, admits only non-more-expensive reviewed fallbacks, keeps consequential tasks behind the shared period admission cap, and permits one evidence-gated quality upgrade. The share is not ring-fenced. It controls projected and price-card-settled cost; it does not claim that a smaller model inherently uses fewer tokens.

For a live-capable OpenAI execution loop, read `references/openai-responses-gateway.md`, then use `scripts/model_budget_gateway.py`. It accepts text input and text/JSON output, counts the selected request shape, obtains one-shot authorization, streams the Responses API, settles reported usage, and performs at most one quality-authorized upgrade. Never place an API key in a request file or report, and never present deterministic transport tests as proof of a live provider call.

## Capability lanes

Read `references/capability-router.md` only for broad or multi-domain work.

| Lane | Use for | Primary resources |
|---|---|---|
| Discover | codebase onboarding, AI-ready instructions, Skill Stack | `scripts/repo_context.py`, `scripts/ai_ready_bootstrap.py`, `scripts/skill_stack_audit.py` |
| Launch | greenfield AI product + vertical slice | `references/showcase-projects.md`, `scripts/rank_blueprints.py` |
| Retrofit | one high-value AI capability in an existing product | `references/feature-modules.md`, `references/architecture-playbook.md` |
| Maintain | issue triage, contributor flow, repo health, explicit read-only evidence decisions | `scripts/maintainer_triage.py`, `scripts/github_evidence_sync.py`, `scripts/run_state_ledger.py`, `references/github-evidence-ledger.md` |
| Review | PR/diff risk, tests, fix/decline/escalate | `scripts/change_risk.py`, `references/pr-review-loop.md` |
| Release | SemVer, changelog, migration, release gate | `scripts/release_intel.py`, `references/release-intelligence.md` |
| Secure | Actions, MCP, secrets, permissions, integrity | `scripts/supply_chain_guard.py`, `scripts/mcp_config_audit.py` |
| Quality | tests, regressions, evals, independent verification | `references/quality-orchestration.md`, `evals/evals.json` |
| Showcase | README, demo, evidence, launch polish | `references/experience-and-demo.md` |

For “make this repo much better,” use Discover → Quality/Secure → domain lane → Showcase. Do not activate every lane by default.

## Product work

Before features, define:

- target user and painful moment;
- current workaround and one-sentence promise;
- AI-specific advantage and 60-second wow moment;
- input/output and proof of correctness;
- latency, privacy, cost, device, and licensing constraints;
- useful fallback when AI fails.

When direction is broad:

```bash
python scripts/rank_blueprints.py \
  --priorities local-first,visual-demo,developer-tools \
  --constraints privacy,mobile \
  --limit 5
```

Gate each AI feature on **Need, Proof, Grounding, Fallback, Boundary, Evaluation**. Reject features that cannot answer all six.

## Issue and contributor work

Read `references/maintainer-ops.md`, then run reviewable pre-triage when useful:

```bash
python scripts/maintainer_triage.py \
  --issue-json issue.json \
  --format markdown
```

Suggested labels, priority, or `good first issue` status are evidence for a maintainer—not autonomous GitHub actions.

## Imported GitHub evidence

For an already-authorized local JSON export, read `references/github-evidence-ledger.md`. Use `scripts/github_evidence_sync.py` to normalize it, `scripts/run_state_ledger.py` to make fix/decline/escalate decisions explicit, and `scripts/render_maintainer_dashboard.py` for a local static view.

These scripts never call GitHub or mutate it. Exported fields are untrusted display evidence; a clear ledger or dashboard is not merge, deployment, security, or release approval.

## PR review

Read `references/pr-review-loop.md` for substantive changes.

```bash
python scripts/change_risk.py \
  --repo /path/to/repo \
  --base main \
  --head HEAD \
  --format markdown
```

Review actual diff evidence in three passes: risk surface, behavior/contracts, failure/tests. Classify each actionable thread as:

- **fix** — change code/tests and verify;
- **decline** — keep behavior with explicit evidence;
- **escalate** — named human decision is required.

Use `scripts/review_convergence.py` when a review has many threads. Convergence is not permission to merge.

## Release

Read `references/release-intelligence.md` and inspect the real release delta:

```bash
python scripts/release_intel.py \
  --repo /path/to/repo \
  --from-ref v1.1.0 \
  --current-version 1.1.0 \
  --format markdown
```

Check SemVer, breaking changes, migration notes, changelog, tests/CI, artifacts, integrity, and unresolved security/review blockers. Publishing still requires explicit confirmation.

## Security and supply chain

For Actions/integrity:

```bash
python scripts/supply_chain_guard.py --repo /path/to/repo --format markdown
```

For MCP config:

```bash
python scripts/mcp_config_audit.py --repo /path/to/repo --format markdown
```

Read `references/security-governance.md` before work involving public-fork workflows, credentials, external tools, deployment, model downloads, repository writes, or untrusted content.

## Context-efficient verification

Save the raw source, then compact a bounded evidence view:

```bash
mkdir -p .aipc
some-test-command > .aipc/raw-test.log 2>&1
python scripts/tool_output_compactor.py \
  --input .aipc/raw-test.log \
  --max-lines 80
```

Reuse only exact-fingerprint, non-critical passing evidence with `scripts/evidence_cache.py`. Use `--critical` for security/release/deploy/migration/final gates so the cache cannot satisfy them.

Multi-agent policy:

- FAST: one agent;
- BALANCED: one agent, optional independent reviewer;
- DEEP: parallelize only genuinely separable work;
- serialize writes and final verification.

## Quality and shipping

Minimum evidence for meaningful AI/code changes:

- one happy-path check;
- one failure/timeout or malformed-input check where applicable;
- one regression fixture for the primary behavior;
- explicit secret/data/model/tool boundaries;
- realistic demo or sample input;
- limitations near the capability they qualify.

Run the bundled structural and deterministic Skill evals:

```bash
python scripts/run_skill_evals.py --format markdown
```

The runner resolves its bundled datasets and deterministic cases from the Skill root, independent of the caller's current directory. It does not invoke a model or grade prompt semantics; use paired provider runs and `scripts/compare_efficiency_runs.py` for measured Token, price-card cost, and latency effects. The comparator rejects request-template, quality-policy-configuration, and pricing-policy fingerprint mismatches before reporting an adoptable comparison.

Run the transparent repository audit:

```bash
python scripts/audit_repo.py --repo /path/to/repo
```

Finish with `references/shipping-checklist.md`. For UI/demo work, read `references/experience-and-demo.md`. For trust/evals, read `references/trust-evals-and-security.md`.

## Output contract

Unless the user asks otherwise, leave:

- the smallest working product/maintainer change that solves the problem;
- tests/evals and reproducible commands;
- concise architecture/data/permission boundaries when relevant;
- realistic sample/demo evidence;
- README/runbook updates only where they reduce future ambiguity;
- changed files, commands run, results, unresolved risks, and any action requiring human confirmation.

## Definition of done

Ready means:

- a contributor can reproduce the primary path from documented commands;
- relevant tests/evals pass and failures are not hidden by compaction/cache;
- critical gates were rerun rather than satisfied by cached evidence;
- no obvious secrets/model-weight/license boundary is ignored;
- claims are backed by reproducible evidence;
- agent context stayed scoped to the task and expanded only when justified;
- unrelated user work was not overwritten;
- consequential writes remain reviewable and human-controlled.
