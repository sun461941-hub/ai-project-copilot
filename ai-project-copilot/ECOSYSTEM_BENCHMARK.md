# Agent Skill Ecosystem Benchmark — 2026

AI Project Copilot v2.0 was redesigned after comparing recurring patterns in major Agent Skills ecosystems and official skill collections. The goal is not to copy any one skill; it is to combine the strongest reusable patterns into one coherent, evidence-first open-source engineering workflow.

## Sources reviewed

- Agent Skills open specification: https://agentskills.io/
- Anthropic public skills and skill-creator: https://github.com/anthropics/skills
- GitHub Agent Skills / Copilot documentation: https://docs.github.com/en/copilot/concepts/agents/about-agent-skills
- GitHub community skill collection: https://github.com/github/awesome-copilot
- Vercel open skills ecosystem: https://github.com/vercel-labs/skills
- Gemini CLI Extensions guidance: https://codelabs.developers.google.com/getting-started-gemini-cli-extensions

## Recurring capabilities in mature skills

| Pattern | Seen in the ecosystem | AI Project Copilot v2.0 |
|---|---|---|
| Progressive disclosure | Small SKILL.md + references/scripts loaded on demand | Capability router + lane-specific references |
| Deterministic helper scripts | Scanners, validators, packaging, test utilities | Context map, issue triage, change risk, release intel, supply-chain guard |
| Repository reconnaissance | Codebase maps, stack detection, task context | `repo_context.py` + Discover lane |
| AI-ready repo setup | Agent instructions, Copilot instructions, CI/onboarding context | non-overwriting `ai_ready_bootstrap.py` |
| Skill discovery/stack hygiene | Local inventory, overlap/conflict checks, smallest compatible stack | read-only `skill_stack_audit.py` |
| PR review loops | Diff review, thread triage, fix/decline/escalate, re-verification | Review lane + `change_risk.py` + `review_convergence.py` |
| Release automation | SemVer, changelog, release blockers, artifacts | Release lane + `release_intel.py` |
| Agent security/governance | Least privilege, audit trails, action restrictions | Security lane + consequence classes + confirmation gates |
| Supply-chain checks | Action/plugin integrity and provenance | Workflow heuristics + optional SHA-256 manifest |
| Quality/evals | Regression fixtures, rubrics, evaluator loops | Quality lane + portable `evals/evals.json` |
| Multi-agent orchestration | Planner/implementer/reviewer/security roles | Optional role isolation with serialized writes |
| MCP/external-tool integration | Skills/extensions bundle tools and teach safe use | Tool boundaries + `mcp_config_audit.py` + read-only default + preview/confirmation |
| Cross-agent portability | Shared Agent Skills format and multiple install locations | Keeps core skill agent-neutral and Python helpers dependency-free |
| Structured output | Machine-readable JSON plus human summaries | JSON-first reports; Markdown/human summaries where the helper is report-oriented |
| Context efficiency | Scripts as black boxes, targeted reference loading | FAST/BALANCED/DEEP budgets, sparse FAST path, batched context packets, log compaction, exact-fingerprint evidence reuse |

## What v2 deliberately does not do

- It does not auto-merge PRs or auto-publish releases by default.
- It does not claim a heuristic score is a security audit or product-quality proof.
- It does not install third-party skills, plugins, models, or MCP servers without an explicit request.
- It does not fabricate repository activity, usage, benchmarks, stars, or compatibility.
- It does not depend on a single agent vendor's proprietary orchestration API.

## Design target

The v2 architecture treats the Skill as a **portable maintainer intelligence layer**:

1. discover the repository;
2. route the task;
3. establish deterministic evidence;
4. use model reasoning for ambiguity and tradeoffs;
5. verify with tests/evals/security/release gates;
6. require human confirmation for consequential writes;
7. record honest remaining risks.

## Context-efficiency design note

The final v2.0 core Skill is deliberately lean and points into lane-specific references. The Context Accelerator adds four deterministic helpers:

- `token_governor.py` — workload budget and multi-agent policy;
- `context_accelerator.py` — changed-file + instruction-chain + related-test context packet;
- `tool_output_compactor.py` — failure/summary-preserving log reduction with raw-log SHA-256;
- `evidence_cache.py` — exact command/input fingerprints for pass-only, non-critical evidence reuse.

The benchmark suite reports path/log character reduction and local wall-clock runtime. These are reproducible engineering proxies, not measured model-token savings. Actual token claims require runtime usage telemetry from the client/API.
