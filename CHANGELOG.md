# Changelog

All notable changes to AI Project Copilot are documented here.

## [2.0.0] - 2026-08-13

### Added

- **AIPC Context Accelerator** for task-budgeted FAST/BALANCED/DEEP execution without weakening critical verification gates;
- deterministic `token_governor.py` for workload budgeting, context caps, multi-agent policy, and client-only reasoning-effort recommendations;
- deterministic `context_accelerator.py` that batches repository reconnaissance, changed-file delta context, governing `AGENTS.md` discovery, related-test selection, manifests, and bounded CI/governance evidence into one packet;
- deterministic `tool_output_compactor.py` that preserves failure/summary neighborhoods, omitted-line counts, and the SHA-256 identity of the normalized raw log;
- conservative `evidence_cache.py` with exact command+content fingerprints, pass-only reuse, atomic local storage, path confinement, and mandatory bypass for critical/final gates;
- `references/context-accelerator.md`, context-efficiency evals, and reproducible local benchmark fixtures;
- **Maintainer Intelligence Engine** with explicit Discover, Launch, Retrofit, Maintain, Review, Release, Secure, Quality, and Showcase lanes;
- deterministic `workflow_router.py` for task-to-capability routing;
- deterministic `repo_context.py` for codebase mapping, stack/manifests, tests, CI, governance, and task-focused file selection;
- non-overwriting `ai_ready_bootstrap.py` for evidence-based `AGENTS.md` and Copilot instruction drafts;
- read-only `skill_stack_audit.py` for local Skill inventory, duplicate names, portability warnings, and trigger overlap;
- deterministic `change_risk.py` for PR/diff risk scoring across auth/security, schema/migrations, API contracts, CI/supply-chain, deploy/config, and test gaps;
- deterministic `review_convergence.py` for fix/decline/escalate review-thread convergence and explicit human handoffs;
- deterministic `release_intel.py` for SemVer recommendations, changelog grouping, breaking-change migration blockers, and draft release notes;
- deterministic `supply_chain_guard.py` for GitHub Actions permission/trigger/action-reference heuristics and optional SHA-256 skill manifests;
- deterministic `mcp_config_audit.py` for MCP secret/transport/shell/dynamic-package configuration risks without executing servers;
- capability-router, codebase-context, AI-ready/Skill-Stack, PR-review-loop/convergence, release-intelligence, security-governance, and quality-orchestration references;
- portable skill-level `evals/evals.json` with substantive positive and near-miss scenarios;
- PR review, release readiness, and run-state templates;
- `ECOSYSTEM_BENCHMARK.md` documenting the mainstream Agent Skills patterns integrated into v2.

### Changed

- Skill metadata advanced to `2.0.0`;
- Skill positioning expanded from AI project polish + maintainer basics into repository-level product engineering and open-source maintainer intelligence;
- repository workflows now start with context mapping and deterministic evidence before model synthesis;
- PR reviews now use multi-pass risk/behavior/failure verification and a fix/decline/escalate thread rubric;
- release workflows now separate version/release-note planning from the consequential publish action;
- security guidance now classifies tool actions by consequence and strengthens least-privilege, public-fork, supply-chain, and audit-trail rules;
- multi-agent-capable clients can use role isolation while writes remain serialized behind one final evidence gate.
- GitHub Actions used by this repository are pinned to immutable v7 commit SHAs while retaining version comments for Dependabot/readability;
- the core `SKILL.md` was reduced from 348 to 213 lines (17,358 to 9,174 characters in the pre-final v2 baseline) by moving active-lane detail into references.

### Stability hardening

- router matching now uses phrase/token boundaries to avoid `pr`/`map`/`tag` substring false positives;
- PR risk path classification now avoids `auth`/`secret` substring false positives and parses quoted Git diff paths;
- release intelligence blocks empty releases and recognizes both `BREAKING CHANGE` and `BREAKING-CHANGE`;
- GitHub Actions scanning ignores commented triggers/commands instead of reporting them as live configuration;
- MCP runner detection handles Windows executable paths consistently;
- AI-ready bootstrap and project-doc initialization refuse symlink/path escapes outside the target repository;
- validator/packager workflows now tolerate generated Python `__pycache__` files while excluding them from archives;
- supply-chain integrity manifests cannot be written into the directory they hash, preventing self-referential manifests.
- issue triage now uses token/phrase boundaries and no longer promotes ordinary bug reports with a minimal example to `good first issue`;
- repository context and PR-risk detection now distinguish real test paths from names such as `testimonial.py`, support monorepo `src/tests` layouts, and reject invalid negative/fractional change counts;
- release intelligence now accepts valid prerelease/build SemVer, handles uppercase Conventional Commit types, ignores prose such as “not a breaking change”, and cannot crash on blank commit entries;
- review convergence rejects empty/duplicate thread state and requires evidence for decline/escalate decisions;
- GitHub Actions scanning now inspects multiline `run: |/>` blocks for untrusted event interpolation while excluding script bodies from YAML-key/action-reference heuristics;
- integrity-manifest writes are confined to the repository and refuse symlink targets;
- MCP auditing now avoids secret-key substring false positives, validates environment-reference syntax, permits loopback HTTP endpoints, supports UTF-8 BOM JSON, confines explicit paths to the repository, and fails closed on excessive nesting;
- Skill Stack auditing now reads folded/literal YAML frontmatter descriptions used by real-world Agent Skills.

## [1.1.0] - 2026-08-13

### Added

- deterministic `maintainer_triage.py` helper for reviewable issue pre-triage;
- maintainer operations reference covering issue triage, PR review, good-first-issue quality, and release readiness;
- reproducible issue-triage fixture and tests;
- public `ROADMAP.md` and `MAINTAINER_MODE.md`;
- feature request issue template;
- maintainer-oriented trigger coverage in the Skill evaluation set.

### Changed

- Skill metadata version advanced to `1.1.0`;
- Skill scope now explicitly includes open-source maintainer workflows while preserving human confirmation for GitHub writes.

## [1.0.0] - 2026-08-13

### Added

- initial AI Project Copilot Agent Skill;
- 24 showcase project blueprints;
- deterministic blueprint ranking, project-doc initialization, and repository audit helpers;
- cross-platform CI, packaging, trigger evals, security policy, contribution guide, and release workflow.
