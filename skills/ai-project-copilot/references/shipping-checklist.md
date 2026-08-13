# Shipping Checklist

Use this at the end of a launch, retrofit, rescue, or showcase pass.

## Product

- [ ] The target user and painful moment are explicit.
- [ ] The one-sentence promise matches the implemented workflow.
- [ ] One primary vertical slice works end to end.
- [ ] AI features pass the need, proof, grounding, fallback, boundary, and evaluation gate.
- [ ] Unsupported capabilities are stated honestly.

## Experience

- [ ] The first screen explains the input, output, and next action.
- [ ] A realistic sample path is available.
- [ ] Empty, loading, streaming, success, partial, cancellation, offline, and error states are intentional.
- [ ] Long tasks can be cancelled safely.
- [ ] Consequential actions have preview and approval.
- [ ] Keyboard, contrast, focus, labels, and reduced-motion behavior are checked.

## Architecture

- [ ] Domain logic is independent from provider SDK objects.
- [ ] Model and tool adapters expose capabilities and stable errors.
- [ ] Timeouts, retries, rate limits, and cancellation are bounded.
- [ ] Local/cloud/hybrid boundaries are documented.
- [ ] Temporary files and partial jobs are cleaned safely.
- [ ] Existing repository conventions and supported platforms are preserved.

## Trust and safety

- [ ] Claims are backed by tests, evals, citations, traces, or reproducible steps.
- [ ] Retrieved content and model output are treated as untrusted.
- [ ] Tool arguments, file paths, URLs, and archive entries are validated.
- [ ] Secrets are not committed, logged, displayed, or exported.
- [ ] Private traces and public-safe projections are separated.
- [ ] Retention, deletion, telemetry, and network behavior are clear.
- [ ] Model source, license, redistribution status, and checksum strategy are documented.
- [ ] No third-party model weights are bundled without verified permission.

## Tests and evals

- [ ] Baseline repository tests pass.
- [ ] Happy path, failure/timeout, malformed input, and adversarial cases exist.
- [ ] The main AI behavior has a versioned regression fixture.
- [ ] Deterministic CI does not require paid credentials.
- [ ] Live provider smoke tests are optional and clearly separated.
- [ ] Cross-platform behavior is tested when the project claims it.

## GitHub and release

- [ ] README begins with the product value, not installation details.
- [ ] Quick start is reproducible from a clean environment.
- [ ] Screenshot, GIF, video, or architecture diagram reflects real behavior.
- [ ] Demo script reaches the wow moment within roughly one minute.
- [ ] Architecture and data boundaries are documented.
- [ ] License, contribution, security, and issue templates exist when appropriate.
- [ ] CI is green and uses least-privilege permissions.
- [ ] Generated archives are deterministic, path-safe, and non-overwriting.
- [ ] The release notes distinguish implemented, experimental, and planned work.

## Final report

- [ ] List changed files.
- [ ] List commands run and their results.
- [ ] State assumptions and limitations.
- [ ] State remaining risks and the narrowest valuable next milestone.
