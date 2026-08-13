# Evidence-First PR Review Loop

## Review order

### Pass 1 — Change surface and risk

Use the actual diff. Run `scripts/change_risk.py` to prioritize review effort. Verify high-risk categories manually.

Check:

- auth/security/permissions;
- schema/data migrations and rollback;
- public APIs/contracts and compatibility;
- dependencies and GitHub Actions;
- deploy/config changes;
- large or unusually wide diffs.

### Pass 2 — Behavioral correctness

For each intended behavior:

- identify the requirement or user story;
- identify implementation path;
- identify test/fixture evidence;
- verify error/fallback behavior;
- verify old behavior that must remain compatible.

### Pass 3 — Adversarial/failure paths

Probe the paths developers frequently miss:

- malformed/empty/extreme input;
- permission denied / expired credentials;
- retry, timeout, cancellation, duplicate request;
- concurrent operations/races;
- partial failure and rollback;
- cross-platform/path/encoding assumptions;
- backwards compatibility and data migration.

## Thread triage rubric

Classify every review thread as one of:

- **fix** — evidence shows a real defect, missing requirement, regression, or maintainability risk worth changing now;
- **decline** — suggestion is incorrect, out of scope, stylistic without repository basis, or harms the stated goal; reply with evidence;
- **escalate** — design, security, product, migration, or compatibility tradeoff requires maintainer judgment.

Do not equate “zero open threads” with “high quality.” A clear escalation is better than an unjustified automatic fix.

## Verification loop

1. Apply one coherent change set.
2. Run the repository's own format/lint/typecheck/test/build commands.
3. Re-run targeted tests for the risk category.
4. Re-read the diff after automated formatters/generators.
5. Re-run `change_risk.py` if the surface changed materially.
6. Stop when requirements are met and remaining decisions are explicitly human-owned.

## Review output

Lead with blockers. Include:

- risk level + evidence;
- exact file/line references where available;
- impact statement;
- recommended fix or escalation;
- tests run and results;
- compatibility/migration notes;
- unresolved human decisions.
