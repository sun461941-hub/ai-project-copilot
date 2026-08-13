# Codebase Context and Reconnaissance

## Objective

Build enough repository understanding to make a correct change without loading the entire codebase into context.

## Progressive context sequence

1. **Instructions** — repository `AGENTS.md`, Copilot/Claude/Gemini instructions, contributor/security docs.
2. **Shape** — top-level folders, manifests, entry points, tests, CI, docs, ownership files.
3. **Task focus** — files whose paths/names match the user request and their direct callers/dependencies.
4. **Behavior evidence** — tests, fixtures, API schemas, migration files, docs describing the relevant behavior.
5. **Only then read implementation details.**

Run:

```bash
python scripts/repo_context.py --repo /path/to/repo --task "task text" --format markdown
```

## High-signal files

Treat these as context anchors when present:

- package/build manifests and lockfiles;
- service/application entry points;
- API/OpenAPI/GraphQL/proto/schema files;
- migrations and database schema;
- CI workflows;
- test configuration and representative tests;
- README, architecture, changelog, roadmap;
- SECURITY, CONTRIBUTING, CODEOWNERS;
- agent instruction files.

## Task-context map

Before editing, write a compact map:

- user goal;
- entry point for the relevant behavior;
- data flow / call chain;
- validation and failure boundary;
- tests currently covering it;
- configuration/deployment surface;
- likely collateral files if the contract changes.

## Context budget rules

- Do not dump large generated/vendor/lock files into model context unless a specific line is relevant.
- Prefer grep/search + targeted reads to reading whole directories.
- Use bundled scripts as black boxes; inspect source only when behavior needs modification.
- When a reference exceeds a few hundred lines, load only the section required for the active lane.
- Preserve a short context summary between iterations instead of repeatedly rereading everything.

## Architecture claims

Do not infer a framework, service boundary, ownership model, or data flow solely from folder names. Confirm with manifests, imports/calls, configuration, and tests.
