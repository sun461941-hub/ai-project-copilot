# Model Budget Autopilot

Use this reference when an application caps ordinary preferred-model spend at
part of a period budget and routes excess ordinary work through reviewed fallbacks.

## Contents

- [What it controls](#what-it-controls)
- [Budget semantics](#budget-semantics)
- [Configure](#configure)
- [Live-capable OpenAI gateway](#live-capable-openai-gateway)
- [Request lifecycle](#request-lifecycle)
- [Protected work](#protected-work)
- [Idempotency, leases, and recovery](#idempotency-leases-and-recovery)
- [Metrics and claims](#metrics-and-claims)
- [Deterministic proof scenario](#deterministic-proof-scenario)

## What it controls

`scripts/model_budget_autopilot.py` is a local, provider-neutral control plane.
It:

- stores an explicit period budget and reviewed model ladder;
- prices projected and settled usage with immutable configuration snapshots;
- reserves cost before a request so concurrent admissions cannot spend the same allowance;
- binds each route idempotency key to the SHA-256 of the exact provider request;
- keeps immutable allow, downgrade, protected, quality-upgrade, and block decisions;
- never selects a fallback with a higher projected cost than the requested model;
- settles late or over-estimate usage instead of discarding it;
- rejects an `incomplete` response as a successful quality result;
- authorizes at most one upward retry after a failed fallback;
- deduplicates provider response IDs and reports every attempt in the final result.

The control-plane script does **not** call a provider, authenticate users,
classify trusted task risk, alter provider quotas, or guarantee fewer Tokens.
A smaller model can use more Tokens or require a retry. Settled cost is actual
reported usage priced through the stored price card; it is not a provider
invoice reconciliation system. The separate live-capable OpenAI gateway described
below supplies the trusted execution bridge.

## Budget semantics

Money is represented exactly as integer nano-USD. Admission budgets and active
reservations stay within SQLite's signed-integer range; settled and
counterfactual evidence uses canonical decimal integer text when it exceeds
that range. Rates are supplied as USD per one million Tokens and converted
without floating-point arithmetic. CLI JSON emits every field whose name
contains `nano_usd` as a decimal string so JavaScript clients do not lose
precision.

Three controls work together:

1. **Period admission cap**: no new active reservation may exceed the configured
   period budget. A non-more-expensive fallback may be selected if it fits.
   Under-estimated usage is still recorded and the debt blocks later admissions.
2. **Preferred allocation**: `period budget × preferred share` is the fixed
   ordinary ceiling for the preferred model. It is not ring-fenced: protected
   work and an upgrade may exceed it, and other requests can consume the shared
   period cap. The remainder is not an independent quota for every fallback.
3. **Share envelope**: after a bounded startup allowance, projected preferred
   spend is compared with projected total spend. Fallback mode persists until
   the settled + reserved share reaches the lower restore line. The first
   preferred request may use its fixed allocation without being rejected merely
   because the observed cold-start share is 100%.

The cap is an admission control, not an absolute provider-spend guarantee.
Unknown external charges, an expired lease for a call that is still running, or
actual usage above projection can create debt. The gateway must supply honest
projections, renew live leases, and settle every provider response.

## Configure

Prices and the capability ladder are explicit. Verify both with the provider
and application evals before deployment.

```bash
python scripts/model_budget_autopilot.py configure \
  --db .aipc/model-budget.sqlite3 \
  --user-key opaque-app-user \
  --period-budget-usd 20 \
  --preferred-share 40 \
  --restore-share 30 \
  --startup-allowance 3 \
  --model quality-model \
  --model balanced-model \
  --model economy-model \
  --price quality-model:5:0.5:6.25:30 \
  --price balanced-model:2:0.2:2.5:12 \
  --price economy-model:0.2:0.02:0.25:1.2
```

Each price is:

```text
MODEL:ordinary-input:cached-input:cache-write:output
```

All four rates are USD per one million Tokens. A later ladder entry is selected
only when its projected request cost is no greater than the requested model's.
Treat equal-cost fallbacks as a configuration smell: they reduce preferred-model
allocation, but do not produce an estimated cost saving.

## Live-capable OpenAI gateway

`scripts/model_budget_gateway.py` integrates the policy ledger with the OpenAI
Responses input-token count and streaming generation endpoints. It counts the
same request shape for every reviewed ladder model, builds and hashes the exact
selected-model payload inside the routing transaction, renews the lease,
settles terminal usage, runs an optional deterministic quality command, and
executes at most one authorized upgrade.

Read [`openai-responses-gateway.md`](openai-responses-gateway.md) before using
that path. Its v2.1 scope accepts text input and text/JSON output only: multimodal
input, prompt templates, tools, and background responses fail closed until their
counting, execution, extra-charge, and lease lifecycles can be reconciled correctly.

## Request lifecycle

Use a new `request-id` and provider response ID for every provider attempt. Use
the same `logical-request-id` for a fallback and its one permitted upgrade.

For a manual integration, the selected-model request bytes must be built and
hashed after routing chooses a candidate, inside the same admission
transaction. The public Python `request_payload_builder` callback supports that
contract; the live-capable gateway is the reference implementation. Do not bind a
requested-model body and then send a different downgraded body. Do not count a
reduced surrogate that omits instructions, state, schemas, reasoning, or other
input-rendering fields.

```bash
python scripts/model_budget_autopilot.py route \
  --user-key opaque-app-user \
  --request-id answer-attempt-1 \
  --logical-request-id answer \
  --requested-model quality-model \
  --request-payload-sha256 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef \
  --projected-input-tokens 12000 \
  --projected-output-tokens 1200 \
  --task-class coding
```

If projected cached/cache-write counts are omitted, every input Token is priced
at the highest of the model's three configured input rates. This conservative
default prevents a cache-write rate above ordinary input from silently reducing
the reservation. When a trusted gateway has a tighter upper bound, supply both:

```bash
  --projected-cached-tokens 8000 \
  --projected-cache-write-tokens 0 \
  --projected-extra-cost-nano-usd 0
```

The caller must execute only when `execution_authorized` is `true`, and then
execute only `selected_model`. Execution authorization is consumed by the first
route response that creates the reservation. Every replay—including an active
reservation replay—of an initially executable decision clears `selected_model`,
sets `execution_authorized=false`, and makes the CLI exit with code 4. A blocked
decision and its replay remain code 3. This prevents concurrent callers from
using one reservation for two provider calls. After a lost route response,
explicitly release the unused reservation when safe and create a new request
ID; a replay is an immutable receipt, not permission to execute. Also forward
a provider idempotency key when supported as defense in depth.
Feed the complete final provider response into settlement. The provider-neutral
CLI requires a unique response `id`, the exact selected `model`, explicit final
`status`, usage, input details, and output/reasoning details where available.
The OpenAI gateway separately records a served snapshot/alias and binds it to
the selected price-card key before settlement:

```bash
python scripts/model_budget_autopilot.py settle \
  --user-key opaque-app-user \
  --request-id answer-attempt-1 \
  --model balanced-model \
  --response response.json
```

`actual_cost_nano_usd` is deliberately rejected in this ordinary response path;
otherwise an untrusted payload could write zero cost. The control plane prices
trusted usage with the route's immutable price snapshot. Any
`extra_cost_nano_usd` must be derived by the trusted gateway.

Then record a real application quality gate. Tests, schema validation, evidence
coverage, or a reviewed grader can establish this signal. Do not accept a
caller-provided boolean without authentication and task/sample alignment.

```bash
python scripts/model_budget_autopilot.py quality \
  --user-key opaque-app-user \
  --request-id answer-attempt-1 \
  --gate fail \
  --reason "required tests failed"
```

If `next_model` is returned, create attempt 2 with `--parent-request-id
answer-attempt-1`. This is a quality-policy authorization, not proof that the
upgrade will fit: the second route still applies the current admission cap.
If configuration or policy changed before a late attempt was assessed, no
automatic upgrade is authorized; submit a new logical request under the current
ladder after manual review.

## Protected work

The default protected classes are security, release, migration, deployment,
permissions, and final-gate. They bypass only soft preferred-allocation rules;
they do not bypass the period admission cap.

Derive or authorize `task-class` on a trusted server. Passing a user-controlled
`--task-class security` directly would let the caller consume protected
allocation. This local CLI cannot provide application authentication.

## Idempotency, leases, and recovery

- `(user, request-id)` has one immutable route decision, including blocks.
- Same ID + same payload hash and route parameters replays as a non-executable
  immutable receipt without another reservation.
- Same ID + different payload hash or parameters is a conflict.
- Provider response IDs are hashed and unique per user, preventing double settlement.
- Expiry releases capacity but preserves request identity; deadlines round
  outward and therefore never expire before the requested TTL.
- Late usage is still settled once; over-estimate usage is never truncated.
- Configuration and price snapshots remain attached to old decisions.
- Reconfiguration is blocked only while reservations are active or have an
  unexpected nonterminal state. A successful reconfiguration invalidates any
  unconsumed automatic-upgrade authorization from an older configuration; that
  logical request then requires manual review or a new request under the current
  ladder.
- Any unexpected reservation state is conservatively treated as nonterminal
  for accounting and reconfiguration rather than silently releasing capacity.
- SQLite admission checks and reservations use `BEGIN IMMEDIATE`, then recheck
  the schema contract under that writer lock before touching ledger facts.

SQLite remains a single-writer local control plane. Each write transaction uses
a single 60-second bounded exponential-backoff budget, while an initialized
ledger opens without reacquiring the schema writer lock. If that wait is
exhausted, retry with the same immutable request ID; an errored transaction does
not create an admission. Use a shared service ledger instead of separate SQLite
files when routing across application instances or under sustained extreme fan-in.

The unreleased preview used SQLite `INTEGER` affinity for evidence amounts.
This version fails closed when it sees that incompatible preview schema instead
of silently converting large values to floating point. The preflight runs before
DDL and verifies the complete column-type contract, critical unique keys, and
absence of triggers on ledger tables. Start with a new state database; do not
reuse a preview ledger without an explicit audited migration.

Renew the reservation while a provider call is still running:

```bash
python scripts/model_budget_autopilot.py renew \
  --user-key opaque-app-user \
  --request-id answer-attempt-1 \
  --reservation-ttl-seconds 3600
```

TTL is crash recovery, not proof that a provider call stopped. Failing to renew
can admit another request before the first call settles. An actual overrun or
late settlement remains visible as debt and blocks later work. Renewal never
shortens an existing later deadline.

Raw user, internal request, logical request, and provider response IDs are not
stored. Their unkeyed hashes are **pseudonymous identifiers**, not anonymity;
low-entropy IDs can still be guessed. Use opaque high-entropy application IDs
and protect the SQLite/WAL files as sensitive data.

## Metrics and claims

Report these separately:

- settled price-card cost and provider-invoice cost, if reconciled elsewhere;
- input, cached input, cache-write, output, reasoning, and total Tokens;
- fallback, incomplete, upgrade, retry, and blocked counts;
- TTFT and end-to-end latency;
- quality/test/evidence pass rate.

Per-attempt savings use a same-Token price counterfactual. Logical-task savings
compare the first attempt's requested-model counterfactual with every
fallback/upgrade attempt, so a failed fallback can correctly show negative
savings. Neither value proves realized savings. `token_savings` remains `null`
without a task-aligned baseline. Use `scripts/compare_efficiency_runs.py` on
paired provider-run gateway records to calculate measured aggregate Token, cost,
and latency effects without removing failed or retried attempts. Request-template,
quality-policy-configuration, and pricing-policy fingerprints help detect invalid
pairs, but they do not prove that external evaluator binaries were unchanged or
reconcile provider invoices.

## Deterministic proof scenario

```bash
python scripts/model_budget_autopilot.py simulate --format json
```

The offline scenario forces a preferred request onto a fallback, marks its
otherwise well-formed response `incomplete`, authorizes one upgrade, passes the
upgraded attempt, and prints the final model plus both usage records. It is a
synthetic control-flow test, not an API call or a sample of model answer quality.
