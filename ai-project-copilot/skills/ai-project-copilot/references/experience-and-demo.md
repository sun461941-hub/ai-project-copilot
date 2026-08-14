# Experience and Demo Playbook

The product should explain itself through interaction, not through marketing copy.

## Contents

- [The first screen](#the-first-screen)
- [Design every state](#design-every-state)
- [Make AI behavior legible](#make-ai-behavior-legible)
- [Visual hierarchy](#visual-hierarchy)
- [The 60-second demo](#the-60-second-demo)
- [GitHub presentation](#github-presentation)
- [What to avoid](#what-to-avoid)

## The first screen

Within a few seconds, a new user should understand:

- what input belongs here;
- what useful output will appear;
- what makes the product different;
- whether processing is local or cloud-based;
- how to start with a realistic example.

Prefer a strong empty state with one sample action over a generic chat welcome message.

## Design every state

For the primary path, create intentional states for:

- empty and first run;
- sample data loaded;
- validating input;
- queued or preparing;
- live streaming or tool progress;
- waiting for approval;
- partial result;
- success;
- unsupported input/device;
- offline/provider unavailable;
- timeout;
- cancellation;
- recoverable failure;
- unrecoverable failure.

Keep the previous useful result visible while a refresh runs. Error messages should say what happened, what was preserved, and what the user can do next.

## Make AI behavior legible

Use the trust signal that fits the project:

- citations that open the exact source;
- a trace/timeline of tools and state transitions;
- local-only and network indicators;
- model/provider identity when it materially changes behavior;
- an approval checkpoint before a write action;
- confidence or uncertainty tied to evidence;
- eval or regression status;
- generated-media settings and provenance;
- memory inspector and delete controls.

Do not expose hidden chain-of-thought. Show concise rationale, evidence, decisions, and observable actions instead.

## Visual hierarchy

A polished AI product usually needs only three visual layers:

1. **Outcome:** the artifact or answer the user came for.
2. **Evidence and control:** citations, source regions, settings, approvals, corrections, and export.
3. **Process detail:** trace, tokens, tools, logs, and diagnostics, collapsed until needed.

Avoid filling the main view with latency counters and model names unless the product itself is an observability tool.

For visual demos:

- use one strong accent rather than many competing gradients;
- align spacing and typography before adding animation;
- animate state changes, not decoration;
- keep motion cancellable and respect reduced-motion settings;
- show realistic content rather than lorem ipsum;
- design mobile overflow and keyboard navigation explicitly.

## The 60-second demo

Use this structure:

**0–8 seconds — Problem**

Show the painful input or failure. One sentence only.

**8–20 seconds — Action**

Load a realistic example and start the workflow. Make local/cloud status and permissions visible.

**20–42 seconds — Wow moment**

Reveal the product’s unique transformation: a replay, cited graph, offline generation, corrected misconception, attack path, or live comparison.

**42–52 seconds — Trust**

Open one citation, trace, approval, eval, or model manifest. Demonstrate that the result is inspectable.

**52–60 seconds — Artifact**

Export, apply, share, or continue from the result. End with the product outcome, not a settings screen.

Prepare a fallback recording or deterministic demo fixture for unreliable external APIs, but label it clearly as a demo mode.

## GitHub presentation

A strong README should contain, in this order:

1. name and one-sentence promise;
2. a real screenshot, short GIF, or diagram;
3. three to six concrete capabilities;
4. a 60-second quick start;
5. a sample workflow;
6. architecture and data boundaries;
7. supported/unsupported platforms and models;
8. evaluation or test evidence;
9. roadmap with a narrow next milestone;
10. contribution and license information.

State “what this is not” when it prevents misunderstanding. For a generic local model runtime, say clearly that the repository does not train, host, bundle, or redistribute model weights and that users import legally obtained models.

Use `assets/templates/readme-ai-section.md` for a compact structure.

## What to avoid

- a chatbot pasted onto an unrelated product;
- ten feature cards with no complete workflow;
- fake terminal output, fake metrics, or screenshots of unimplemented UI;
- animated gradients that obscure hierarchy;
- hidden cloud calls in a “local AI” project;
- one giant autonomous agent with no permissions or stop condition;
- a demo that depends on manual database edits;
- a README that begins with installation before explaining the product;
- claims such as “production-ready,” “secure,” or “works on all devices” without evidence.
