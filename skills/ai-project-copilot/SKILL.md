---
name: ai-project-copilot
description: Use this skill to turn an AI product idea or existing repository into a credible, demo-ready open-source project, or to run evidence-first maintainer workflows such as issue triage, PR readiness review, contributor onboarding, release preparation, evals, privacy, security, and OSS health checks. Trigger when the user wants product-level AI architecture, a demonstrable vertical slice, a GitHub showcase, or repository maintenance that benefits from structured AI assistance. Do not use for isolated bug fixes, routine dependency bumps, generic code review, or one-off explanations unless the user also wants product-level or maintainer-level workflow improvements.
license: MIT
compatibility: Designed for ChatGPT and Codex clients that support the open Agent Skills standard. Bundled helper scripts require Python 3.10 or newer.
metadata:
  author: sun461941-hub
  version: "1.1.0"
---

# AI Project Copilot

## Goal

Build the smallest complete AI product or maintainer workflow that proves a meaningful outcome with visible evidence. Prefer reproducible checks, usability, and human review over a long feature list or autonomous-looking theater.

## Apply this skill when

- The user has a vague AI idea and needs a strong project direction.
- An existing repository needs a useful AI layer rather than a decorative chatbot UI.
- The user wants a launch-ready GitHub project, hackathon demo, portfolio piece, or open-source showcase.
- The task includes RAG, agents, multimodal input, voice, image/video generation, on-device inference, evals, observability, or AI safety.
- An open-source maintainer wants structured issue triage, PR readiness review, contributor onboarding, release preparation, or repository-health evidence.
- The user wants to make a project more attractive without fabricating capabilities, activity, users, benchmarks, or maintenance history.

Do not activate for a narrow bug fix, routine dependency update, generic review, or concept explanation unless the request also includes product design or maintainer workflow improvement.

## Non-negotiable rules

1. **Inspect before editing.** Read `AGENTS.md`, `README`, manifests, architecture docs, tests, CI, issue/PR templates, and relevant source paths first.
2. **Preserve the existing stack.** Change frameworks only when the current stack blocks the required outcome, and document that decision.
3. **No AI theater.** Every AI feature must solve a named user problem, expose a visible result, and have a failure fallback.
4. **Build a vertical slice first.** Connect real input to real model/tool behavior, verification, UI, and output before adding breadth.
5. **Keep claims honest.** Never invent benchmark numbers, stars, downloads, screenshots, users, integrations, hardware support, test results, issue activity, or release history.
6. **Protect data and secrets.** Make network use, retention, model providers, telemetry, repository permissions, and local/cloud boundaries explicit.
7. **Respect model licenses.** Never download, bundle, or redistribute model weights without an explicit request and verified permission.
8. **Make risky actions reversible.** Tool calls that write, merge, publish, close, delete, message, purchase, or deploy require preview, confirmation, and an audit trail.
9. **Design all states.** Include useful empty, loading, streaming, success, partial, offline, cancelled, and error states when UI is involved.
10. **Human maintainers keep authority.** Triage scores and summaries are suggestions. Do not auto-close issues, approve PRs, merge branches, or cut releases solely from a model/classifier result.
11. **Leave the repository healthier.** Add focused tests, documentation, and reproducible commands; do not create unnecessary scaffolding.

## Operating modes

Choose one mode after repository inspection:

- **Launch:** turn a new idea into a working repository and demo.
- **Retrofit:** add a focused AI capability to an existing product.
- **Showcase:** preserve the product but improve the demo path, UI clarity, README, examples, and evidence.
- **Rescue:** reduce an overbuilt or unreliable AI prototype to one trustworthy vertical slice.
- **Maintain:** improve issue triage, PR review evidence, contributor onboarding, release readiness, and repository health without hiding maintainer decisions.

When the user did not specify a stack, choose boring, widely supported defaults that fit the target platform. Record assumptions instead of blocking on minor ambiguity.

## Workflow

### 1. Establish the product or maintainer wedge

For product work, write a compact opportunity map:

- target user and painful moment;
- current workaround;
- one-sentence product promise;
- the AI-specific advantage;
- the 60-second wow moment;
- input, output, and proof of correctness;
- latency, privacy, cost, device, and licensing constraints;
- the smallest useful fallback when AI fails.

For maintainer work, identify:

- the maintenance bottleneck;
- the evidence available from issues, diffs, tests, CI, or releases;
- which suggestions may be automated safely;
- which actions still require a maintainer decision;
- the smallest workflow that reduces review time without weakening quality.

Copy `assets/templates/project-brief.md` into the target repository when product work is larger than a small retrofit.

### 2. Select a blueprint and modules

Read `references/showcase-projects.md` when project direction is broad. The structured catalog is in `references/blueprints.json`.

For deterministic ranking, run:

```bash
python scripts/rank_blueprints.py \
  --priorities local-first,visual-demo,developer-tools \
  --constraints privacy,android \
  --limit 5
```

Choose one primary blueprint. Combine at most two secondary ideas only when they share the same user journey and architecture.

Then read `references/feature-modules.md`. Add a module only when it passes the feature gate below.

### 3. Pass the feature gate

For every proposed AI feature, answer all six questions:

1. **Need:** what user or maintainer problem does it remove?
2. **Proof:** what visible output demonstrates value?
3. **Grounding:** what data, diff, test, tool, or source keeps it accurate?
4. **Fallback:** what happens when the model is wrong, slow, unavailable, or offline?
5. **Boundary:** what data leaves the device or repository, and what permissions are required?
6. **Evaluation:** what deterministic check or rubric can catch regressions?

Reject or postpone features that cannot answer these questions.

### 4. Choose the architecture

Read `references/architecture-playbook.md` and select local-first, cloud, or hybrid deliberately.

Create a short architecture decision record from `assets/templates/architecture-decision.md` when the choice affects privacy, licensing, cost, portability, model support, or repository permissions.

Keep model/provider code behind a narrow adapter. Keep domain logic independent from prompts and SDK calls. Make cancellation, retries, timeouts, streaming events, and error types explicit.

### 5. Plan the vertical slice

Define one end-to-end path with:

- a realistic sample input;
- context or retrieval preparation;
- a real model or deterministic mock selected explicitly;
- tool calls with permission boundaries;
- validation, citations, diff evidence, or test evidence;
- progressive feedback;
- a useful final artifact;
- a visible failure path;
- at least one automated test or eval.

Do not build a dashboard full of inactive cards. Complete the core loop first.

### 6. Implement in evidence-sized increments

Work in small changes that can be checked independently:

1. data contract and fixture;
2. model/tool adapter;
3. core workflow;
4. validation and fallback;
5. UI states and cancellation when relevant;
6. persistence only if the user journey needs it;
7. tests and evals;
8. demo assets, maintainer evidence, and README.

Use streaming only when it improves perceived progress or lets users intervene. Expose real state rather than fake progress percentages.

### 7. Polish the product and demo

Read `references/experience-and-demo.md` before final UI and README work.

The first screen must explain the product without a paragraph. The demo must reach the wow moment quickly, use realistic data, and show one trust signal such as citations, trace replay, local-only status, an eval result, or an approval checkpoint.

Copy `assets/templates/demo-script.md` and `assets/templates/readme-ai-section.md` when useful.

### 8. Add trust, evaluations, and security

Read `references/trust-evals-and-security.md` for any project that handles private data, external content, tool calls, autonomous steps, model downloads, repository writes, or public claims.

At minimum, include:

- one happy-path test;
- one failure/timeout test;
- one adversarial or malformed-input test;
- one regression fixture for the main AI behavior;
- secret scanning or a documented secret-handling rule;
- explicit data, permission, and model-license boundaries.

### 9. Run maintainer operations when requested

Read `references/maintainer-ops.md` for issue triage, PR review, contributor onboarding, or release work.

For deterministic issue pre-triage, run:

```bash
python scripts/maintainer_triage.py \
  --issue-json /path/to/issue.json \
  --format markdown
```

Use the result as a reviewable suggestion. Do not apply labels or close issues automatically unless the user explicitly requests the write and the action is previewed first.

For PR review, inspect the actual diff and tests before summarizing. For releases, inspect the release diff, CI, changelog, migration impact, and artifact reproducibility before recommending a tag.

### 10. Audit and ship

Run the bundled repository audit from the skill folder:

```bash
python scripts/audit_repo.py --repo /path/to/project
```

Use the output as a checklist, not as proof of product or security quality. Finish with `references/shipping-checklist.md`.

## Output contract

Unless the user asks for a different format, leave these artifacts in the target repository:

- a working vertical slice or maintainer workflow;
- a concise project brief, maintenance report, or updated product section;
- a documented architecture/data/permission boundary;
- realistic sample data or a reproducible demo mode;
- tests and/or eval fixtures;
- a README with one-command setup, a clear demo path, and honest limitations;
- a short demo or maintainer runbook when useful;
- a list of changed files, commands run, results, and remaining risks.

For maintainer tasks, also report which suggestions require human confirmation before any GitHub write.

## Definition of done

A project or maintenance workflow is ready only when:

- a new contributor can start it from documented commands;
- the primary path works end to end with realistic input;
- loading, cancellation, failure, and fallback behavior are visible where applicable;
- no secrets or model weights are committed accidentally;
- claims are backed by tests, evals, citations, diffs, traces, or reproducible steps;
- the README states what is local, what uses a network, and what data is retained;
- maintainer automation separates suggestions from irreversible writes;
- the implementation does not overwrite unrelated user work.

## Bundled resources

- `references/showcase-projects.md` — 24 project blueprints, including Codex Build Visualizer and Android Local Video Runtime.
- `references/feature-modules.md` — selection guidance for RAG, agents, multimodal, voice, media generation, local AI, memory, evals, observability, and more.
- `references/architecture-playbook.md` — local/cloud/hybrid architecture, adapters, streaming, reliability, and data boundaries.
- `references/experience-and-demo.md` — UI states, visual hierarchy, demo narrative, and GitHub presentation.
- `references/trust-evals-and-security.md` — evaluation, prompt injection, privacy, model licensing, and tool safety.
- `references/maintainer-ops.md` — issue triage, PR review, good-first-issue quality, and release readiness.
- `references/shipping-checklist.md` — final release checklist.
- `scripts/rank_blueprints.py` — rank project blueprints from priorities and constraints.
- `scripts/init_project_docs.py` — copy project, architecture, and demo templates without overwriting existing files.
- `scripts/audit_repo.py` — emit a transparent repository-readiness report.
- `scripts/maintainer_triage.py` — deterministic, read-only issue pre-triage for maintainers.
