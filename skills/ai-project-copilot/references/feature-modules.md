# AI Feature Modules

Use this file after the product wedge is clear. Select the fewest modules that complete one user journey.

## Contents

- [Cited retrieval](#cited-retrieval)
- [Tool-using agents](#tool-using-agents)
- [Multimodal input](#multimodal-input)
- [Streaming progress](#streaming-progress)
- [Voice and realtime](#voice-and-realtime)
- [Image and video generation](#image-and-video-generation)
- [Local and on-device AI](#local-and-on-device-ai)
- [Memory and personalization](#memory-and-personalization)
- [Collaborative canvas](#collaborative-canvas)
- [Evaluation arena](#evaluation-arena)
- [Observability and replay](#observability-and-replay)
- [Privacy and security](#privacy-and-security)
- [Model routing and fallback](#model-routing-and-fallback)
- [Data storytelling](#data-storytelling)
- [Export and sharing](#export-and-sharing)

## Cited retrieval

**Use when:** answers must be grounded in private or changing documents, repository files, logs, papers, policies, or web content.

**Minimum proof:** every important claim can open its exact source; retrieval failures are visible; unsupported claims are labeled rather than smoothed over.

**Implementation notes:**

- Preserve source identity, page/line/region, access scope, and ingestion version.
- Separate retrieval from answer generation so each layer can be evaluated.
- Include a no-answer path when evidence is insufficient.
- Test duplicate, conflicting, stale, malformed, and adversarial documents.

**Avoid when:** the answer is purely generative, the source set is tiny enough for direct context, or citations would be decorative rather than verifiable.

## Tool-using agents

**Use when:** the product must inspect, calculate, search, modify, run, schedule, or coordinate across systems.

**Minimum proof:** the user can see the plan, tool arguments, status, result, and approval point; every write action is attributable and reversible where possible.

**Implementation notes:**

- Prefer a short state machine over an open-ended autonomous loop.
- Give each tool a narrow schema, explicit timeout, and idempotency strategy.
- Distinguish read, draft, write, publish, and destructive permissions.
- Cap steps, retries, output size, and total cost.
- Treat tool output as untrusted input.

**Avoid when:** one deterministic function or normal API call solves the task more safely.

## Multimodal input

**Use when:** the user’s real input is visual, spatial, audio, handwritten, diagrammatic, or mixed-media.

**Minimum proof:** the interface highlights the exact visual/audio region behind a result and lets the user correct misinterpretation.

**Implementation notes:**

- Keep original media and derived text/regions linked.
- Preserve orientation, timestamps, page numbers, speaker turns, and bounding boxes when available.
- Provide low-resolution, unsupported-format, and partial-processing states.
- Do not silently replace visual reasoning with unreliable OCR.

**Avoid when:** text entry is faster, more accurate, and more accessible for the target task.

## Streaming progress

**Use when:** users benefit from early output, live tool status, cancellation, or intervention during a long task.

**Minimum proof:** progress events correspond to real workflow states; cancellation actually stops or detaches the job safely.

**Implementation notes:**

- Define typed events such as `queued`, `retrieving`, `tool_started`, `token_delta`, `validating`, `completed`, `cancelled`, and `failed`.
- Support reconnect or resume for long jobs.
- Never show fake percentages unless total work is measurable.
- Keep the final persisted result independent from transient stream chunks.

**Avoid when:** the task normally finishes instantly or streaming makes the result harder to verify.

## Voice and realtime

**Use when:** hands-free interaction, conversation latency, accessibility, field work, or live coaching is central.

**Minimum proof:** live transcript, interruption handling, clear recording state, and a review step before consequential actions.

**Implementation notes:**

- Separate audio capture, transcription, turn detection, intent, action, and playback.
- Make mute, stop, retry, transcript editing, and device selection obvious.
- Store audio only when necessary and state retention clearly.
- Test noise, accents, crosstalk, silence, and interrupted turns.

**Avoid when:** voice is merely a novelty around a text-first workflow.

## Image and video generation

**Use when:** generated media is the artifact, a visual prototype accelerates work, or variant comparison is valuable.

**Minimum proof:** editable prompt/settings history, provenance metadata, reproducible variants, and honest limits around consistency and rights.

**Implementation notes:**

- Treat generation as a job with queue, cancellation, seed/settings, and output metadata.
- Preserve source assets and transformation instructions.
- Add content and licensing boundaries appropriate to the deployment.
- For video, expose duration, frames, resolution, memory, and encoding stages.
- Never bundle model weights unless redistribution is allowed and explicitly intended.

**Avoid when:** a stock asset, deterministic renderer, or normal design tool is more reliable.

## Local and on-device AI

**Use when:** offline operation, privacy, latency, user-owned models, or device capability is a core promise.

**Minimum proof:** network-disconnected demo, explicit local/cloud indicator, device capability check, and graceful unsupported-device behavior.

**Implementation notes:**

- Detect RAM, storage, accelerator, thermal, and supported operators before loading.
- Use a model manifest with architecture, tokenizer, precision, memory estimate, license, source, checksums, and backend compatibility.
- Keep runtime, model adapters, UI, and model files separate.
- Prefer user-imported legally obtained models for generic runtimes.
- Provide cancellation and cleanup for partially loaded models and failed jobs.

**Avoid when:** the target hardware cannot meet the task or the local claim hides mandatory cloud steps.

## Memory and personalization

**Use when:** past decisions, preferences, progress, or stable context materially improves future outcomes.

**Minimum proof:** users can inspect, correct, scope, export, expire, and delete remembered information.

**Implementation notes:**

- Store claims with provenance, confidence, scope, timestamps, and expiry.
- Separate raw history from promoted memory.
- Detect contradictions instead of silently overwriting.
- Do not infer sensitive attributes without a clear product need and consent.

**Avoid when:** session context is sufficient or persistence creates more risk than value.

## Collaborative canvas

**Use when:** relationships, alternatives, spatial grouping, shared editing, or human-AI co-creation matter more than a linear chat.

**Minimum proof:** users can directly manipulate AI-created objects, trace why they exist, and continue without the model.

**Implementation notes:**

- Use typed nodes and edges rather than arbitrary blobs.
- Keep source references and generation history attached to objects.
- Support undo, versioning, comments, and export.
- Avoid creating a beautiful graph that does not improve a decision.

**Avoid when:** a table, form, or normal document is clearer.

## Evaluation arena

**Use when:** prompts, models, retrieval, tools, or policies will change and regressions matter.

**Minimum proof:** a versioned task set, repeatable run configuration, explainable checks, and a comparison view.

**Implementation notes:**

- Mix deterministic assertions, structured rubrics, and human review.
- Keep test data separate from tuning examples.
- Store model/version, prompt/version, seed, tool versions, latency, cost, and traces.
- Include negative controls and malformed/adversarial cases.

**Avoid when:** the prototype has no stable behavior to evaluate yet; first create a small regression fixture.

## Observability and replay

**Use when:** workflows have multiple steps, tools, retries, approvals, failures, or high debugging cost.

**Minimum proof:** one failed run can be reconstructed without exposing hidden secrets or private raw data.

**Implementation notes:**

- Use a versioned event schema with bounded fields.
- Separate private traces from public-safe projections.
- Redact before persistence or export, not only in the UI.
- Record state transitions, timing, model/tool identifiers, and result summaries.
- Support corrupted, partial, duplicate, and out-of-order events.

**Avoid when:** a single request/response log already explains the behavior.

## Privacy and security

**Use when:** any private data, external content, authentication, tool execution, local model import, or public deployment is involved. In practice this module is usually required.

**Minimum proof:** a data-flow statement, least-privilege permissions, secret handling, retention controls, and tests for hostile input.

**Implementation notes:**

- Treat retrieved pages, model output, file names, archives, and tool results as untrusted.
- Separate instructions from content to reduce prompt-injection risk.
- Validate paths, URLs, MIME types, archive entries, and output destinations.
- Never echo tokens, approval text, raw private traces, or sensitive arguments into public logs.
- Require explicit confirmation for destructive or external side effects.

## Model routing and fallback

**Use when:** cost, latency, local capability, quality, or availability requires more than one model/provider.

**Minimum proof:** routing policy is inspectable, fallbacks preserve semantics, and the UI identifies material capability differences.

**Implementation notes:**

- Route on task class and constraints, not vague “smartness.”
- Keep a stable internal request/response contract.
- Make retry and fallback budgets finite.
- Record why a route was selected without exposing hidden chain-of-thought.
- Test provider outage, quota failure, incompatible model, and partial stream.

**Avoid when:** one model meets the task and a router would only add complexity.

## Data storytelling

**Use when:** the product must turn structured data into decisions, explanations, or interactive visual evidence.

**Minimum proof:** every metric is reproducible from source rows and every narrative claim links to its calculation.

**Implementation notes:**

- Define metrics before chart selection.
- Keep transformations as code or query, not hidden model prose.
- Show units, filters, missing data, and uncertainty.
- Let users inspect the source behind outliers.

**Avoid when:** a normal report or chart already answers the question.

## Export and sharing

**Use when:** the result must leave the product as a report, archive, link, media file, patch, or reproducible bundle.

**Minimum proof:** exported artifacts retain provenance, version, limitations, and privacy-safe defaults.

**Implementation notes:**

- Make public and private export modes distinct.
- Use deterministic filenames and never overwrite existing output silently.
- Include a manifest for multi-file exports.
- Sanitize rich content and archive paths.
- Verify exports can be reopened independently.
