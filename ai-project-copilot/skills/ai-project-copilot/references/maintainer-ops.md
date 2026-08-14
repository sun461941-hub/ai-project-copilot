# Maintainer operations

Use this playbook when the project already exists and the goal is to make open-source maintenance faster, safer, and easier to review.

## Principles

1. Human maintainers keep final authority over labels, merges, releases, and security disclosures.
2. Prefer deterministic evidence before model judgment: changed files, failing tests, issue templates, release diffs, and reproducible commands.
3. Never auto-close a security report or a bug report solely from a classifier score.
4. Separate suggestions from writes. Show the proposed action before changing GitHub state.
5. Keep an audit trail: what was inspected, what was suggested, what was accepted, and why.

## Issue triage

Start with `scripts/maintainer_triage.py` for a deterministic pre-triage pass. It can suggest labels, priority, difficulty, missing evidence, and whether an issue looks suitable for a first-time contributor.

```bash
python scripts/maintainer_triage.py \
  --issue-json /path/to/issue.json \
  --format markdown
```

Treat the result as a queueing aid, not an autonomous decision. For bugs, ask for reproduction evidence before assigning implementation work. For security signals, route to the repository security policy instead of discussing sensitive exploit details in public.

## Pull request review

Review in this order:

1. scope: does the PR solve the linked issue without unrelated churn?
2. evidence: tests, reproduction, screenshots or fixtures where relevant;
3. compatibility: supported platforms and versions;
4. security/data boundary: secrets, permissions, network calls, model/data handling;
5. maintainability: naming, docs, failure paths, and rollback strategy;
6. release impact: breaking change, migration note, changelog entry, or no user-visible change.

Never approve a PR because an AI summary sounds plausible. Read the actual diff and test results.

## Good first issue criteria

A `good first issue` should have:

- a bounded surface area;
- no security-sensitive code path;
- a clear expected result;
- a reproduction or acceptance test;
- pointers to likely files or modules;
- no hidden product decision that requires maintainer context.

## Release readiness

Before cutting a release:

- CI is green on all supported platforms;
- the release diff is understood;
- breaking changes are called out explicitly;
- `CHANGELOG.md` is updated;
- security-sensitive fixes are coordinated appropriately;
- artifacts are reproducible where practical;
- release notes distinguish new features, fixes, maintenance, and known limitations.

## Suggested maintainer outputs

For a maintenance task, leave a concise report with:

- issues triaged and the evidence behind each suggestion;
- PRs reviewed and unresolved risks;
- release blockers;
- proposed labels/milestones;
- commands or checks run;
- actions that still require human confirmation.
