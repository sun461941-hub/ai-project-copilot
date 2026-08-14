# Architecture Playbook

Use this reference after choosing the project wedge and feature modules.

## Contents

- [Start from the core loop](#start-from-the-core-loop)
- [Choose local, cloud, or hybrid](#choose-local-cloud-or-hybrid)
- [Repository inspection](#repository-inspection)
- [Layer boundaries](#layer-boundaries)
- [Model and tool adapters](#model-and-tool-adapters)
- [Streaming and jobs](#streaming-and-jobs)
- [Reliability](#reliability)
- [Data and model boundaries](#data-and-model-boundaries)
- [Architecture decision record](#architecture-decision-record)

## Start from the core loop

Describe the product as one testable loop:

```text
user input
  -> validated domain request
  -> context / retrieval / media preparation
  -> model and/or tool execution
  -> verification and policy checks
  -> progressive user feedback
  -> useful artifact
  -> trace + evaluation signal
```

Each arrow needs an explicit contract and failure state. Do not let UI components call model SDKs directly.

## Choose local, cloud, or hybrid

| Mode | Choose it when | Main risks | Required proof |
|---|---|---|---|
| Local-first | privacy, offline use, predictable marginal cost, or user-owned models is central | hardware fragmentation, model size, thermal limits, unsupported operators | offline demo, capability probe, memory/latency telemetry, unsupported-device path |
| Cloud | model quality, large context, centralized updates, or cross-device access dominates | privacy, cost, quota, provider lock-in, network failure | data-flow statement, cancellation, quotas, provider outage fallback |
| Hybrid | local preprocessing or private storage combines with cloud inference | boundary confusion and duplicated complexity | clear local/cloud indicator, minimal data sent, independent fallback behavior |

Do not call a product “local” when a required embedding, moderation, login, or generation step still depends on a server.

## Repository inspection

Before proposing architecture:

1. Read repository instructions and contributor docs.
2. Identify entry points, package managers, build systems, test commands, deployment targets, and supported platforms.
3. Map existing domain types and state management.
4. Search for current AI/provider code, environment variables, telemetry, queues, storage, and auth.
5. Note code ownership boundaries and files that should not be rewritten.
6. Run the smallest existing test/build command that verifies the baseline.

Record only material findings. Do not produce a giant repository summary before starting useful work.

## Layer boundaries

Prefer these logical layers even when they live in fewer physical packages:

```text
presentation
  UI state, accessibility, streaming display, user corrections
application
  workflows, permissions, cancellation, retries, orchestration
core/domain
  provider-neutral inputs, outputs, citations, jobs, errors, policies
adapters
  model SDKs, vector stores, device runtimes, tools, file parsers
infrastructure
  persistence, queues, telemetry, auth, deployment
```

Rules:

- Domain types must not expose vendor SDK objects.
- Prompts are versioned configuration, not scattered strings.
- Tool schemas are narrow and validated at the boundary.
- Persistence stores domain events/results, not accidental UI state.
- Public-safe traces are derived from private internal traces through an allowlist.

## Model and tool adapters

A useful internal contract is small and capability-aware:

```text
ModelRequest
  task
  messages / input parts
  response schema
  tools
  constraints: latency, privacy, cost, local-only
  cancellation token

ModelResult
  content / structured value
  citations or provenance
  tool requests/results
  usage and timing
  model identity
  warnings
```

Adapters should:

- validate configuration at startup;
- expose capabilities rather than letting callers guess;
- normalize errors into stable categories;
- support finite timeouts and cancellation;
- avoid logging raw secrets or private payloads;
- surface model/version and material fallback changes.

For model-agnostic local runtimes, use a manifest containing at least:

- package format version;
- model architecture and task;
- tokenizer/text encoder requirements;
- tensor format, precision, and checksums;
- expected memory/storage;
- compatible backends and minimum device capability;
- model source and license metadata;
- optional safety or content notes.

A runtime should not imply permission to redistribute the model package.

## Streaming and jobs

Use a job abstraction for long or cancellable work:

```text
Job: queued -> preparing -> running -> validating -> completed
                                     -> partial
                                     -> cancelled
                                     -> failed
```

Requirements:

- Generate a stable job ID before work starts.
- Emit typed, bounded events with monotonic sequence numbers.
- Make repeated status requests idempotent.
- Persist final output separately from transient deltas.
- Clean temporary files on failure and cancellation.
- Resume only when the underlying operation supports it honestly.
- Never infer completion from a closed network connection alone.

## Reliability

Design the failure taxonomy before polishing success:

- invalid input;
- unsupported capability or device;
- authentication/configuration failure;
- quota or rate limit;
- network/provider unavailable;
- timeout/cancellation;
- tool permission denied;
- malformed model/tool output;
- verification failure;
- storage/export failure;
- internal invariant violation.

For each category define user message, retry policy, telemetry, cleanup, and fallback. Retry only transient operations and cap retries. Never retry destructive actions blindly.

Use deterministic mocks for CI, but keep a clearly marked real-provider smoke test path. A mock must not be presented as evidence that a live integration works.

## Data and model boundaries

Create a small table in the project docs:

| Data/model item | Source | Stored where | Sent where | Retention | User control |
|---|---|---|---|---|---|
| User input | user | memory/local/server | selected provider/tool | explicit | edit/delete/export |
| Retrieved content | repository/docs/web | index/cache | model context | explicit | re-index/remove |
| Trace | workflow | private trace store | none by default | bounded | inspect/delete |
| Public report | derived allowlist | project/export | user-selected | user controlled | preview |
| Model package | user import or approved source | device/storage | nowhere unless stated | user controlled | verify/remove |

Do not use a denylist to create public traces or exports. Start from an allowlist of safe fields.

## Architecture decision record

Create an ADR when choosing a provider, local runtime, vector store, queue, persistence layer, model format, or cross-platform strategy. Keep it short:

- context and user constraint;
- decision;
- alternatives considered;
- consequences and failure modes;
- validation plan;
- reversal/migration path.

Use `assets/templates/architecture-decision.md`.
