# Capability Router

Use this reference when a request spans multiple repository/product domains. The goal is to activate the smallest set of lanes that can finish the task with evidence.

## Lanes

| Lane | Trigger shape | Do first | Exit condition |
|---|---|---|---|
| Discover | unfamiliar repo, architecture, impact map | run `repo_context.py` | relevant files + architecture evidence identified |
| Launch | vague/new AI idea | write opportunity map, rank blueprints | one primary vertical slice chosen |
| Retrofit | existing product needs AI | inspect current flow and constraints | one AI capability passes feature gate |
| Maintain | issue/backlog/contributor workflow | triage evidence and scope | human-reviewable issue/action plan |
| Review | PR/diff/code-change review | inspect actual diff + run `change_risk.py` | blockers, review lanes, test evidence known |
| Release | tag/version/changelog | classify commits + release diff | version, blockers, notes, publish gate known |
| Secure | workflow/tool/plugin/supply chain | scan trust boundaries | findings + least-privilege remediation plan |
| Quality | tests/evals/regressions | derive behavioral requirements | repeatable gate catches relevant failures |
| Showcase | README/demo/public launch | collect evidence | new user can understand, run, trust the project |

## Composition patterns

### Make this repo better

1. Discover
2. Quality + Secure in parallel if independent
3. Domain lane (Launch/Retrofit/Maintain/Review/Release)
4. Showcase

### Review and merge a PR

1. Review
2. Secure if auth/workflow/dependency paths changed
3. Quality
4. Human confirmation before merge

### Prepare a release

1. Release
2. Quality
3. Secure/supply-chain
4. Showcase for release notes/demo evidence
5. Human confirmation before tag/publish

### Onboard a contributor

1. Discover
2. Maintain
3. Quality if task needs tests
4. Showcase only if contributor-facing docs are poor

## Routing constraints

- Do not activate every lane because keywords overlap.
- Prefer deterministic scripts to establish evidence before model synthesis.
- Run independent read-only lanes in parallel only when the client supports it safely.
- Consequential writes remain serialized behind a human confirmation gate.
- If two lanes disagree, repository evidence wins over keyword routing.
