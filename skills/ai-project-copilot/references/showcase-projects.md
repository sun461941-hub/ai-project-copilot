# Showcase Project Catalog

Use this catalog when the user needs a compelling AI project direction, a portfolio or hackathon concept, or a stronger product wedge for an existing repository.

## Contents

- [Developer tools](#developer-tools)
- [On-device AI](#on-device-ai)
- [Knowledge and research](#knowledge-and-research)
- [Automation](#automation)
- [Education and accessibility](#education-and-accessibility)
- [Data and evaluation](#data-and-evaluation)
- [Creative and multimodal](#creative-and-multimodal)

## Selection rule

Choose one primary blueprint. Combine at most two secondary ideas only when they strengthen the same user journey. A project that tries to demonstrate every AI capability usually demonstrates none of them convincingly.

## Developer tools

### Codex Build Visualizer

**Pitch:** Turn agent traces, tool calls, file edits, builds, and tests into a privacy-safe interactive timeline and dependency graph.

**60-second wow moment:** Drop in a JSONL trace and scrub through the entire build while files, commands, approvals, failures, and recovery events animate in sync.

**Minimum vertical slice:** Trace ingestion, strict redaction, timeline view, file/tool graph, failure markers, session replay, and static export.

**Core modules:** `observability-replay`, `streaming-progress`, `privacy-security`, `export-share`

**Tags:** `developer-tools`, `observability`, `visual-demo`, `local-first`, `privacy`, `open-source` · **Complexity:** `medium`

### AI Codebase Cartographer

**Pitch:** Map an unfamiliar repository into architecture, ownership, dependency, risk, and change-impact views with evidence linked to source files.

**60-second wow moment:** Ask “what breaks if I change this interface?” and watch the graph highlight affected modules, tests, owners, and risky call paths.

**Minimum vertical slice:** Repository index, symbol/dependency graph, cited answers, impact query, architecture map, and exportable onboarding report.

**Core modules:** `cited-retrieval`, `tool-agents`, `collaborative-canvas`, `export-share`

**Tags:** `developer-tools`, `rag`, `graph`, `visual-demo`, `open-source` · **Complexity:** `medium`

### AI PR Maintainer

**Pitch:** Triage pull requests, group review feedback, propose minimal fixes, and produce evidence-backed maintainer summaries without silently merging anything.

**60-second wow moment:** Open a noisy pull request and instantly see a clean action board: blockers, duplicate comments, suggested patches, test evidence, and unresolved risk.

**Minimum vertical slice:** PR ingestion, review-thread clustering, patch proposal, test-plan generation, human approval gates, and final summary.

**Core modules:** `tool-agents`, `cited-retrieval`, `evaluation-arena`, `privacy-security`

**Tags:** `developer-tools`, `github`, `agents`, `automation`, `open-source` · **Complexity:** `medium`

### Test Failure Replay Studio

**Pitch:** Convert CI logs and traces into a deterministic failure narrative that links symptoms, environment differences, suspected causes, and reproduction steps.

**60-second wow moment:** Compare Windows, macOS, and Linux failures side by side and replay the exact divergence point in one synchronized view.

**Minimum vertical slice:** Multi-platform log parser, failure clustering, environment diff, minimal repro generator, and fix verification report.

**Core modules:** `observability-replay`, `cited-retrieval`, `evaluation-arena`, `export-share`

**Tags:** `developer-tools`, `ci`, `debugging`, `cross-platform`, `visual-demo` · **Complexity:** `medium`

### Agent Memory Inspector

**Pitch:** Make agent memory visible, editable, scoped, and auditable so users can understand what was remembered, why, and where it will be used.

**60-second wow moment:** Open a session and inspect a live memory graph with provenance, confidence, expiry, conflicts, and one-click forget controls.

**Minimum vertical slice:** Memory event log, provenance links, scope controls, conflict detection, expiry policy, and safe export/delete.

**Core modules:** `memory-personalization`, `observability-replay`, `privacy-security`, `collaborative-canvas`

**Tags:** `agents`, `memory`, `privacy`, `observability`, `visual-demo` · **Complexity:** `medium`

### AI Security Review Copilot

**Pitch:** Review code changes against threat models, data flows, and repository policy while keeping every finding linked to concrete evidence.

**60-second wow moment:** Select a diff and receive an attack-path graph showing source, trust boundary, exploit preconditions, affected assets, and a minimal patch.

**Minimum vertical slice:** Diff analysis, threat-model template, evidence-linked findings, severity rationale, patch suggestions, and regression tests.

**Core modules:** `tool-agents`, `cited-retrieval`, `privacy-security`, `evaluation-arena`

**Tags:** `security`, `developer-tools`, `code-review`, `graph`, `trust` · **Complexity:** `medium`

## On-device AI

### Android Local Video Runtime

**Pitch:** A model-agnostic Android runtime that lets users import legally obtained video models and run short generation jobs through GPU, NPU, or CPU backends.

**60-second wow moment:** Import a model package, generate a short clip fully offline, and show live memory, thermal, latency, and backend telemetry on the phone.

**Minimum vertical slice:** One supported architecture, model manifest, user import flow, one backend adapter, 384×384 short clip pipeline, MP4 export, and benchmark screen.

**Core modules:** `local-on-device`, `image-video`, `observability-replay`, `privacy-security`, `model-routing`

**Tags:** `android`, `local-first`, `video`, `systems`, `privacy`, `visual-demo`, `mobile` · **Complexity:** `large`

### On-Device Multimodal Assistant

**Pitch:** Run useful text, image, and audio tasks locally with explicit hardware capability detection and graceful cloud-free fallbacks.

**60-second wow moment:** Disconnect the network, point the phone at an object or document, and complete a useful task while showing exactly what stayed on device.

**Minimum vertical slice:** Capability probe, one vision task, one text task, optional speech input, model manager, resource controls, and privacy dashboard.

**Core modules:** `local-on-device`, `multimodal-input`, `voice-realtime`, `privacy-security`

**Tags:** `local-first`, `mobile`, `multimodal`, `privacy`, `offline` · **Complexity:** `large`

## Knowledge and research

### Multimodal Research Canvas

**Pitch:** Combine papers, web pages, images, tables, notes, and hypotheses on a spatial canvas with citation-preserving AI synthesis.

**60-second wow moment:** Drag a chart and two papers onto the canvas, ask a question, and watch claims connect back to exact passages and visual regions.

**Minimum vertical slice:** Document/image ingestion, canvas nodes, cited synthesis, contradiction view, note linking, and shareable research snapshot.

**Core modules:** `cited-retrieval`, `multimodal-input`, `collaborative-canvas`, `export-share`

**Tags:** `research`, `multimodal`, `rag`, `citations`, `visual-demo` · **Complexity:** `medium`

### Cited Document Copilot

**Pitch:** Answer, compare, and draft from private documents while preserving page-level citations, uncertainty, and access boundaries.

**60-second wow moment:** Ask a cross-document question and receive a claim table where every sentence opens the exact supporting page and conflicting evidence.

**Minimum vertical slice:** Private ingestion, chunking, cited retrieval, comparison table, uncertainty labels, and export with provenance.

**Core modules:** `cited-retrieval`, `privacy-security`, `model-routing`, `export-share`

**Tags:** `documents`, `rag`, `citations`, `privacy`, `enterprise` · **Complexity:** `small`

### AI Science Notebook

**Pitch:** Turn experiments, data, code, plots, and hypotheses into a reproducible notebook with AI-assisted planning and claim tracking.

**60-second wow moment:** Change one assumption and instantly see which results, plots, conclusions, and follow-up experiments become invalid.

**Minimum vertical slice:** Experiment records, data/code links, hypothesis graph, reproducibility checks, claim provenance, and report export.

**Core modules:** `tool-agents`, `cited-retrieval`, `observability-replay`, `export-share`

**Tags:** `science`, `reproducibility`, `data`, `research`, `graph` · **Complexity:** `medium`

### Personal Knowledge OS

**Pitch:** A local-first knowledge workspace that connects notes, files, tasks, and conversations without turning everything into an opaque chatbot.

**60-second wow moment:** Ask “what have I changed my mind about?” and see a time-aware graph of notes, evidence, contradictions, and decisions.

**Minimum vertical slice:** Local index, linked notes, cited answers, timeline, memory controls, and portable export.

**Core modules:** `cited-retrieval`, `memory-personalization`, `local-on-device`, `privacy-security`

**Tags:** `knowledge`, `local-first`, `memory`, `privacy`, `graph` · **Complexity:** `medium`

## Automation

### Browser Workflow Studio

**Pitch:** Record, explain, edit, and safely replay browser workflows with explicit permissions, checkpoints, and human approval for risky actions.

**60-second wow moment:** Demonstrate a messy multi-site task once, then watch the workflow become a visual, editable automation with live checkpoints.

**Minimum vertical slice:** Recorder, step graph, parameterization, permission model, dry run, approval gates, and replay log.

**Core modules:** `tool-agents`, `observability-replay`, `privacy-security`, `collaborative-canvas`

**Tags:** `automation`, `browser`, `agents`, `visual-demo`, `productivity` · **Complexity:** `large`

### Meeting-to-Execution Copilot

**Pitch:** Convert meeting audio and notes into decisions, owners, tasks, risks, and follow-ups while preserving who said what and requiring confirmation.

**60-second wow moment:** End a meeting and immediately see a decision map, disputed points, owner-ready tasks, and a draft follow-up that links to exact moments.

**Minimum vertical slice:** Transcription import, speaker-aware decisions, task extraction, uncertainty review, confirmation flow, and export.

**Core modules:** `voice-realtime`, `cited-retrieval`, `tool-agents`, `export-share`

**Tags:** `voice`, `productivity`, `automation`, `citations`, `collaboration` · **Complexity:** `small`

### AI Incident Commander

**Pitch:** Unify alerts, logs, runbooks, owners, and timelines into a human-controlled incident workspace that prioritizes evidence over confident guesses.

**60-second wow moment:** During a simulated outage, watch the system build a live causal timeline, propose the next safest diagnostic step, and update the status page draft.

**Minimum vertical slice:** Event ingestion, timeline, runbook retrieval, hypothesis board, approval-gated actions, and postmortem export.

**Core modules:** `tool-agents`, `cited-retrieval`, `streaming-progress`, `observability-replay`

**Tags:** `devops`, `incident`, `agents`, `realtime`, `observability` · **Complexity:** `large`

### Open-Source Maintainer Dashboard

**Pitch:** Prioritize issues, pull requests, releases, documentation gaps, and contributor health without replacing maintainer judgment.

**60-second wow moment:** Open a busy repository and get a transparent weekly map of what is blocked, what is duplicative, what is risky, and where a new contributor can help.

**Minimum vertical slice:** Issue/PR clustering, stale-risk scoring, release readiness, contributor entry points, and cited weekly brief.

**Core modules:** `tool-agents`, `cited-retrieval`, `data-storytelling`, `export-share`

**Tags:** `github`, `open-source`, `maintainers`, `automation`, `dashboard` · **Complexity:** `medium`

## Education and accessibility

### Adaptive Learning Coach

**Pitch:** Diagnose misconceptions from worked steps, generate targeted practice, and adapt explanations without hiding the reasoning behind feedback.

**60-second wow moment:** Write one wrong step and watch the coach identify the exact misconception, switch explanation style, then generate a near-transfer problem.

**Minimum vertical slice:** Step capture, misconception tags, explanation modes, mastery graph, targeted practice, and progress export.

**Core modules:** `multimodal-input`, `memory-personalization`, `evaluation-arena`, `collaborative-canvas`

**Tags:** `education`, `personalization`, `multimodal`, `visual-demo`, `learning` · **Complexity:** `medium`

### Accessibility Copilot

**Pitch:** Transform interfaces, documents, images, and live content into alternative representations tailored to a user’s access needs.

**60-second wow moment:** Point a camera at a dense interface and receive a structured, navigable description with action targets, reading order, and optional voice control.

**Minimum vertical slice:** Image/UI understanding, structured descriptions, keyboard/voice navigation, preference profiles, and privacy controls.

**Core modules:** `multimodal-input`, `voice-realtime`, `memory-personalization`, `privacy-security`

**Tags:** `accessibility`, `multimodal`, `voice`, `mobile`, `social-impact` · **Complexity:** `large`

## Data and evaluation

### AI Data Storyteller

**Pitch:** Turn messy tables into verified metrics, interactive explanations, and decision-focused visual stories with reproducible transformations.

**60-second wow moment:** Drop in a dataset and receive a narrated dashboard where every claim opens the exact calculation and source rows.

**Minimum vertical slice:** Schema profiler, cleaning plan, metric definitions, charts, cited narrative, and reproducible export.

**Core modules:** `data-storytelling`, `tool-agents`, `cited-retrieval`, `export-share`

**Tags:** `data`, `visualization`, `citations`, `analytics`, `visual-demo` · **Complexity:** `small`

### Model Eval Arena

**Pitch:** Compare models, prompts, retrieval strategies, latency, cost, and failure modes on a versioned task set instead of relying on vibes.

**60-second wow moment:** Run one dataset and watch side-by-side outputs, deterministic checks, human rubrics, regressions, and Pareto frontiers update live.

**Minimum vertical slice:** Task dataset, model adapters, run matrix, graders, trace storage, regression dashboard, and export.

**Core modules:** `evaluation-arena`, `model-routing`, `observability-replay`, `data-storytelling`

**Tags:** `evals`, `models`, `benchmarking`, `dashboard`, `developer-tools` · **Complexity:** `medium`

### Synthetic Data Workbench

**Pitch:** Generate, inspect, filter, and validate synthetic datasets with explicit coverage targets, provenance, privacy checks, and human review.

**60-second wow moment:** Move a coverage slider and watch the system generate missing edge cases while a live map shows diversity, leakage risk, and duplicates.

**Minimum vertical slice:** Schema builder, generation pipeline, deduplication, coverage dashboard, privacy checks, and versioned export.

**Core modules:** `tool-agents`, `evaluation-arena`, `data-storytelling`, `privacy-security`

**Tags:** `data`, `synthetic-data`, `evals`, `privacy`, `visual-demo` · **Complexity:** `medium`

### Agent Observability Console

**Pitch:** Inspect multi-step agent runs across prompts, tools, latency, cost, errors, approvals, and retries with searchable traces and replay.

**60-second wow moment:** Click a failed answer and expand the exact chain of prompt, retrieval, tool call, retry, and policy decision that produced it.

**Minimum vertical slice:** Trace schema, ingestion, run list, step tree, latency/cost charts, failure clustering, and replay links.

**Core modules:** `observability-replay`, `evaluation-arena`, `data-storytelling`, `privacy-security`

**Tags:** `agents`, `observability`, `evals`, `dashboard`, `developer-tools` · **Complexity:** `medium`

## Creative and multimodal

### Voice Developer Console

**Pitch:** Control a development workspace by voice while keeping commands inspectable, reversible, and confirmed before destructive actions.

**60-second wow moment:** Say “explain the failing test, patch the smallest cause, and show the diff” and watch a live transcript, plan, commands, and rollback points.

**Minimum vertical slice:** Streaming speech, intent parser, command preview, approval gates, terminal integration, diff view, and undo log.

**Core modules:** `voice-realtime`, `tool-agents`, `streaming-progress`, `observability-replay`

**Tags:** `voice`, `developer-tools`, `realtime`, `agents`, `visual-demo` · **Complexity:** `large`

### AI Creative Media Studio

**Pitch:** A provenance-aware workspace for combining text, image, audio, and video generation into editable creative workflows rather than one-shot prompts.

**60-second wow moment:** Turn a short brief into a storyboard, generate editable shots, compare variants, preserve prompt history, and export a complete asset manifest.

**Minimum vertical slice:** Brief-to-storyboard flow, provider adapters, variant comparison, prompt/version history, asset metadata, and export.

**Core modules:** `image-video`, `multimodal-input`, `collaborative-canvas`, `export-share`

**Tags:** `creative`, `multimodal`, `image`, `video`, `visual-demo` · **Complexity:** `large`

