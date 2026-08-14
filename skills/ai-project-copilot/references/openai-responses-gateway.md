# OpenAI Responses Budget Gateway

Use this reference when the application must turn Model Budget Autopilot from a
local policy ledger into a live-capable OpenAI Responses execution path.

## What is implemented

`scripts/model_budget_gateway.py` closes this loop:

1. validate one text-input, text/JSON-output Responses request;
2. call `POST /v1/responses/input_tokens` for every reviewed model in parallel;
3. reserve the conservative maximum counted input plus `max_output_tokens`;
4. atomically select the model and bind the exact canonical request bytes;
5. stream `POST /v1/responses` without automatic generation retries;
6. settle terminal provider usage, including cached input, output, and reasoning
   details, plus cache-write Tokens if a provider response explicitly reports them;
7. run an optional deterministic quality command;
8. permit at most one budget-checked upward retry after a failed fallback; and
9. report attempts, selected/served model, Tokens, price-card cost, TTFT, provider
   latency, and whole-workflow latency.

The selected model is inserted before the request hash is stored. The same
canonical body is then sent to OpenAI, so a downgrade cannot leave the ledger
bound to the originally requested model payload.

Repository tests use deterministic injected transports and do not spend API
quota. They verify request construction, streaming protocol handling, accounting,
and failure semantics; they are not evidence that a live provider call succeeded.

## Prerequisites

Configure `model_budget_autopilot.py` first. Every ladder entry must be a real
OpenAI model ID accepted by the same request shape. Price cards are explicit,
reviewed configuration; this repository deliberately does not hard-code a live
pricing catalog.

Set the key outside JSON and shell history:

```bash
read -rsp "OpenAI API key: " OPENAI_API_KEY && export OPENAI_API_KEY
printf '\n'
```

`OPENAI_PROJECT` and `OPENAI_ORGANIZATION` are optional. The key is read only
from the process environment, is not forwarded to the quality subprocess, and
is not written to the ledger or result report.

## Request file

Start from `assets/templates/openai-response-request.json` and replace the model
with the preferred model ID from the configured ladder. The request must contain
`input` and a positive `max_output_tokens` value. `stream` is controlled by the
gateway and `store` defaults to `false`.

The v2.1 gateway intentionally rejects:

- image, audio, and file input, because this executor's supported request and
  quality contract is deliberately text-only;
- `conversation`, because mutable server-side state is outside the immutable
  request body used for routing and paired-run evidence;
- prompt-template requests, because the current input-token endpoint cannot
  count that request form exactly;
- non-empty `tools`, because function loops and variable built-in-tool charges
  are not reconciled by this text-only executor;
- background responses, because their asynchronous lease and settlement
  lifecycle is not implemented; and
- top-level fields outside the reviewed v2.1 text request contract, so a newly
  introduced modality or billing mode cannot silently bypass accounting.

Nested message objects accept only `type`, `role`, and `content`. Text parts
accept only `type` plus `text`, while refusal parts accept only `type` plus
`refusal`. Extra nested keys fail before token counting or generation.

Non-default `service_tier` values are also rejected so the price card cannot
silently omit a tier premium.

These are fail-closed scope boundaries, not claims that the Responses API lacks
those capabilities.

## Execute

```bash
python scripts/model_budget_gateway.py \
  --db .aipc/model-budget.sqlite3 \
  --user opaque-trusted-user \
  --request-id answer-attempt-1 \
  --logical-request-id answer \
  --request request.json \
  --task-class routine \
  --format json
```

Visible text deltas stream to stderr while the final machine-readable report is
written to stdout. Use `--live-output none` for a silent stream or `--format
jsonl` when appending aligned task records for A/B analysis. Deltas from a
fallback are provisional until its quality gate passes; the final report is the
authoritative output. Top-level `ttft_ms` is the first visible delta across the
workflow and can therefore be provisional; per-attempt TTFT remains in the trace,
while top-level `e2e_ms` ends only when the final workflow result is known.

The command above intentionally omits `--quality-policy`, so only provider status
is used. Add `--quality-policy assets/templates/quality-policy.json` only after
reviewing the structural smoke gate described below and replacing it when the
task needs a stronger acceptance criterion.

The user key, task class, price card, extra-cost projection, and quality policy
are trusted-server inputs. Do not pass a user-controlled `security` task class
through unchanged. `--projected-extra-cost-nano-usd` is both reserved and
settled as a trusted fixed amount; it is not a provider invoice lookup.
If configuration changes after counting but before admission, the version
precondition aborts before provider execution instead of using stale counts.

## Deterministic quality command

Start from `assets/templates/quality-policy.json`. The command runs with
`shell=False`, a narrow environment without OpenAI credentials, bounded output,
and a timeout. Exact argument tokens may use:

- `{python}` — the gateway's current Python interpreter;
- `{response_json}` — temporary terminal response JSON;
- `{output_text}` — temporary UTF-8 output text; and
- `{attempt}` — `1` or `2`.

The checked-in template is only a runnable structural smoke gate: it reads both
temporary files and passes when the terminal response is `completed` and the
visible output is non-empty. It does **not** judge factuality, task completion,
test results, or answer quality. Replace its inline command with a trusted,
task-specific evaluator before using a failed gate to authorize more spend.

Exit semantics are strict:

| Result | Meaning | Upgrade behavior |
|---|---|---|
| `0` | completed response passed the configured quality check | stop |
| `1` | configured response-quality check failed | one upgrade may be authorized |
| timeout/start failure/other exit | evaluator failure | stop; do not spend on an upgrade |
| provider `incomplete` or `failed` | provider-status failure | one upgrade may be authorized |

Without `--quality-policy`, `completed` is the only quality signal. That proves
provider completion, not semantic correctness. The command contract limits
shell interpolation, credentials, output, and time; it is not an operating-
system sandbox, so configure only trusted repository-local evaluators.

## Failure and recovery semantics

- Generation is never retried automatically after an uncertain network error.
- A definite validation, authentication, payload-size, media-type, semantic-
  validation, or rate-limit rejection releases its reservation. HTTP 408/409,
  server failures, and uncertain transport errors keep it for reconciliation or
  TTL recovery.
- A stream without exactly one terminal response, stable response ID, supported
  status, model, and usage fails closed and is not settled as zero.
- The terminal response must confirm `service_tier=default`; a missing or
  different tier remains unsettled for manual reconciliation because the
  configured price card does not include a tier premium.
- The lease is renewed while the stream is active. TTL is still crash recovery,
  not proof that an abandoned provider request stopped.
- Only the route call that creates the reservation receives execution authority.
  A replay never calls the provider again.
- Exact or date-suffixed served model IDs are accepted. Other aliases require an
  explicit `--served-model-map`; price settlement still uses the selected
  model's immutable price snapshot and records the served ID separately.
- OpenAI `x-request-id` and `openai-processing-ms`, when returned, are preserved
  in the attempt trace for support and latency analysis.

## Measure real effects

A single gateway result deliberately keeps `token_savings` as `null`: there is
no counterfactual Token count in one run. Run the same frozen tasks once with a
preferred-only baseline policy and once with the candidate portfolio, preserving
the same logical task IDs, frozen request template, quality-policy configuration,
and reviewed pricing inputs. Append each compact result to its
own JSONL file, then compare:

```bash
python scripts/compare_efficiency_runs.py \
  --baseline baseline.jsonl \
  --candidate candidate.jsonl \
  --require-improvement \
  --format markdown
```

The comparator aligns task IDs, includes failed and retried attempts, blocks
adoption on a success regression or request-template, quality-policy-configuration,
or pricing-policy fingerprint mismatch, and separately reports aggregate Token
saving, price-card cost saving, TTFT, end-to-end latency reduction, and speedup.
Negative percentages remain negative. The quality-policy fingerprint covers
recorded configuration, not the content of an external evaluator executable.
The request fingerprint also binds the originally requested model and task class.
The pricing fingerprint covers the reviewed model ladder and price cards,
protected-task policy, served-model map, projected fixed extra cost, and enforced
default service tier. Routing thresholds remain the candidate treatment rather
than an invariant. Price-card cost is not provider-invoice cost.

Official protocol references:

- <https://developers.openai.com/api/reference/resources/responses/subresources/input_tokens/methods/count/>
- <https://developers.openai.com/api/docs/guides/streaming-responses>
- <https://developers.openai.com/api/docs/guides/prompt-caching>
