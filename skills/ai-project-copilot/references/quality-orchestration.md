# Quality Gates and Optional Agent Orchestration

## Quality loop

Use measured iteration rather than unbounded self-critique:

1. Derive behavioral requirements from user intent, docs, code, and tests.
2. Turn important requirements into executable tests or stable evaluation expectations.
3. Run the smallest relevant baseline.
4. Inspect failures and risk hotspots.
5. Make one coherent change.
6. Re-run the same checks.
7. Keep the change only if evidence improved and no required behavior regressed.

## Three quality passes

### Requirements pass

Ask:

- What must work?
- What must remain compatible?
- What failure behavior is required?
- What security/data boundary is non-negotiable?

### Implementation pass

Review architecture, complexity, duplication, types/contracts, error handling, and repository conventions.

### Verification pass

Run behavior-focused tests, regression fixtures, build/typecheck/lint, and risk-specific checks. Avoid “coverage theater”: a high line-coverage number is not a substitute for testing the important behavior.

## Evaluations

Prefer evals that are:

- substantive enough to require the skill;
- independent;
- stable over time;
- verifiable;
- tied to an expected behavior or artifact;
- diverse across happy path, near miss, failure, and adversarial input.

Use `evals/evals.json` as a portable set of skill-level scenarios. Trigger evals and output-quality evals measure different things; keep both.

## Role orchestration

When a client supports subagents, split only when work can be isolated:

- **mapper/planner** — context map, acceptance criteria, dependencies;
- **implementer** — one bounded change set;
- **reviewer** — independent diff/behavior review;
- **security** — trust boundaries and abuse/failure paths;
- **release/verifier** — final tests, changelog, artifacts, evidence.

Parallelism is useful for independent read-only analysis. Serialize writes to avoid conflicting edits and preserve a single source of truth.

## Final challenge gate

Before declaring success, ask:

- What evidence would falsify the conclusion?
- Which requirement has the weakest test?
- Did the change introduce a new permission/data/migration boundary?
- Are any claims based only on model confidence?
- Which decisions still belong to a human maintainer?
