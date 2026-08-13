# AIPC Context Accelerator

Use this reference when the goal includes faster Codex/agent completion, lower context growth, fewer redundant tool calls, or better token efficiency **without weakening correctness gates**.

## What this can and cannot change

The Skill cannot raise model tokens-per-second, change account quotas, bypass service limits, or force a client to use a particular reasoning setting. It can improve end-to-end efficiency by reducing unnecessary context, batching deterministic reconnaissance, compacting noisy tool output, and reusing exact-fingerprint non-critical evidence.

Treat all savings as workload-dependent. Measure them on representative tasks.

## 1. Budget first

Run:

```bash
python scripts/token_governor.py \
  --prompt "<task>" \
  --changed-file path/to/file \
  --format markdown
```

Modes:

- **FAST** — docs, tiny edits, narrow low-risk fixes. One agent, smallest relevant validation, 8-file initial context cap.
- **BALANCED** — normal feature/bug/review work. Targeted tests first, 20-file initial cap.
- **DEEP** — security, release, migrations, architecture, CI/supply chain, broad changes. Broader verification, 48-file initial cap, but still progressive disclosure.

`recommended_reasoning_effort` is only a suggestion for clients that expose such a control. Do not claim the Skill changed the model setting.

## 2. Compile context instead of reading broadly

Run:

```bash
python scripts/context_accelerator.py \
  --repo /path/to/repo \
  --task "<task>" \
  --git-status \
  --format markdown
```

Or pass explicit changed paths with repeated `--changed-file`.

The packet combines in one deterministic pass:

- task budget;
- changed files;
- applicable `AGENTS.md` / `AGENTS.override.md` paths;
- task-focused files;
- likely related tests;
- manifests and limited CI/governance context;
- scan warnings and a path-character context proxy.

Read the packet first. Expand into source files only when evidence requires it.

### Instruction precedence

For each file you edit, obey the repository instruction chain that governs that path. Keep root instructions short and point to deeper docs instead of copying the entire knowledge base into one prompt.

## 3. Delta context

When a change set exists, prioritize:

1. changed files;
2. governing instructions;
3. directly related tests;
4. task-matched files;
5. manifests/config needed to interpret the change;
6. broader CI/security/release context only when risk justifies it.

Do not read unrelated directories just because they exist.

## 4. Compact noisy tool output

Pipe raw logs through:

```bash
some-test-command 2>&1 | \
  python scripts/tool_output_compactor.py --max-lines 80
```

The compactor preserves failure/summary neighborhoods and appends the SHA-256 of the normalized raw log plus omitted-line count. The raw log remains the source of truth; expand it when the compact view is ambiguous.

Never compact away the only copy of evidence.

## 5. Evidence cache

The cache never executes commands. It only records or checks evidence supplied by the caller.

Record a passing targeted check:

```bash
python scripts/evidence_cache.py record \
  --repo /path/to/repo \
  --entry unit-auth \
  --command "pytest tests/test_auth.py" \
  --input src/auth.py \
  --input tests/test_auth.py \
  --status pass \
  --summary "18 passed"
```

Check later:

```bash
python scripts/evidence_cache.py check \
  --repo /path/to/repo \
  --entry unit-auth \
  --command "pytest tests/test_auth.py" \
  --input src/auth.py \
  --input tests/test_auth.py
```

A hit requires the exact command and exact content hashes of all declared inputs, and only a recorded `pass` is reusable.

Use `--critical` for security, release, deploy, migration, final CI, or other consequential gates. Critical checks always return non-reusable and must be rerun.

## 6. Multi-agent policy

Parallel agents can reduce wall-clock time on independent work, but they often increase total context/tokens. Therefore:

- FAST: one agent;
- BALANCED: one agent by default, optional independent reviewer;
- DEEP: parallelize only separable research/test/review tasks;
- serialize all writes and final evidence gates.

## 7. Efficiency metrics

Track at least:

- task success/regression rate;
- files scanned vs files initially selected;
- selected path/context characters;
- tool-output raw vs compact characters;
- tool calls/round trips when the client exposes them;
- wall-clock duration;
- actual input/cached/reasoning/output token counts only when the client/API exposes them.

The bundled deterministic metrics are **proxies**, not measured model tokens. Never relabel character reduction as token reduction.

## Safety invariant

Optimization may remove redundant work, but it must not skip a verification step merely because it is expensive. Security, release, deployment, migration, permissions, and final integration checks stay evidence-first and human-controlled.
