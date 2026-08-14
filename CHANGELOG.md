# Changelog

All notable changes to AI Project Copilot are documented here.

## [Unreleased]

## [2.1.0] - 2026-08-14

### Added

- **Model Budget Autopilot**, a provider-neutral SQLite control plane for user-selected preferred-model cost allocation, cost-checked fallback routing, protected-task handling, payload-bound immutable decisions, concurrent reservations, provider-response deduplication, lease renewal, usage settlement, hysteresis, and one quality-gated upgrade;
- **OpenAI Responses Budget Gateway**, a live-capable text-input/text-or-JSON-output bridge that counts every reviewed ladder model through the official input-token endpoint, atomically binds the selected request bytes, streams output, renews reservations, settles reported usage, records TTFT/E2E/provider request evidence, runs an optional bounded deterministic quality command, and performs at most one authorized upgrade;
- paired `compare_efficiency_runs.py` reports for measured Token, price-card cost, TTFT, and end-to-end latency effects without dropping failed or retried attempts;
- self-contained `run_skill_evals.py` validation for 25 static Skill eval records, 20 trigger fixtures, and three bundled deterministic command cases, with explicit disclosure that semantic grading is not performed;
- request and quality-policy templates plus a live-gateway integration reference; the quality template reads the response/output but intentionally proves only completed, non-empty output until replaced with a task-specific evaluator;
- deterministic offline `simulate` evidence that reports every fallback/upgrade attempt and the final quality-gated selected model without claiming unmeasured Token savings;
- adversarial tests for cold start, exact budget boundaries, block/settlement idempotency, cache-write projection, reservation renewal/expiry, usage overruns, price snapshots, protected tasks, incomplete responses, duplicate provider IDs, database permissions, and concurrent routing.

### Safety and accuracy

- model prices remain explicit configuration snapshots rather than a hard-coded live catalog;
- fallback routing never selects a higher projected request cost, and unknown input-cache mix uses the highest configured input rate for admission;
- reservation deadlines round outward so sub-second start times never shorten the promised TTL, and fallback search skips unrepresentable intermediate candidates;
- a configuration change invalidates both late and still-unconsumed automatic upgrades derived from the previous model ladder, preventing permanent reconfiguration locks and stale upgrade execution;
- actual usage settlement is independent of whether the requested-model counterfactual exceeds SQLite's integer range;
- initialized ledgers no longer reacquire a schema writer lock on every request, and transient SQLite writer contention now receives bounded busy retry before surfacing an error;
- test-owned SQLite connections now close deterministically after transaction exit, preventing Python 3.14 resource warnings and Windows temporary-file cleanup failures;
- only the first reservation-creating route response carries execution authority; every replay clears the executable model, exposes `execution_authorized=false`, and returns a distinct nonzero CLI status;
- schema-version and structural schema-contract checks reject incompatible ledgers before DDL and recheck after the writer lock to stop rolling-upgrade races; fact inserts explicitly abort on conflicts, and CLI nano-USD fields use decimal strings to preserve wire precision;
- all money uses integer nano-USD and all failed/retried attempts stay in final cost and Token totals;
- `token_savings` remains unknown without a task-aligned baseline, because changing models does not inherently reduce Token usage.
- provider generation through the gateway has no automatic HTTP retry; malformed or usage-free terminal streams remain unsettled for manual reconciliation rather than being recorded as zero;
- multimodal input, prompt templates, tools, and background responses fail closed in the v2.1 gateway until their counting, execution, variable-charge, and lease lifecycles can be reconciled;
- paired-run comparison rejects request-template, quality-policy-configuration, and pricing-policy mismatches; the request fingerprint binds the requested model and task class, while pricing binds the reviewed ladder/price cards, protected-task policy, served-model map, fixed extra cost, and default service tier; no fingerprint claims to hash external evaluator binaries or reconcile provider invoices;
- CI uses deterministic injected transports, so its passing gateway tests are not presented as proof of a live provider call;
- `.github` and `.env` paths retain their leading dots, continuous Chinese authentication tasks select relevant source files, resolved escalations still require owners, vendor-prefixed API keys are audited, and Skill integrity scans both supported project installation locations;
- the Skill frontmatter now follows the current official validator's allowed keys, while compatibility guidance remains in documentation.

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
