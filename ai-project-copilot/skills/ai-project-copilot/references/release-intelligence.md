# Release Intelligence

## Release evidence, not release vibes

A release recommendation should derive from commit/diff evidence, tests, CI, migration impact, and artifact provenance.

## SemVer decision

Use the strongest applicable change:

- **major** — backwards-incompatible public behavior, schema/API contract break, removed feature, required migration;
- **minor** — backwards-compatible user-visible capability;
- **patch** — backwards-compatible bug/security/performance fix or maintenance release;
- **none** — no releasable change.

`release_intel.py` uses conventional-commit hints when available but the maintainer should override it when the public contract says otherwise.

## Release-note groups

Prefer user-facing groups:

1. Breaking
2. Features
3. Fixes
4. Performance
5. Security
6. Documentation
7. Maintenance

Avoid copying raw commit noise. Merge implementation-only commits into the user-facing change they support.

## Blocking gates

Before recommending publish, verify as applicable:

- current branch/commit is the intended release source;
- repository is clean or the release process explicitly handles generated files;
- tests/typechecks/builds pass on supported platforms;
- migration/upgrade note exists for breaking changes;
- changelog/version metadata agree;
- dependency and workflow changes have supply-chain review;
- release artifact can be reproduced or at minimum hashed;
- no secrets/private fixtures/model weights entered the package;
- permissions required to publish are understood;
- rollback or yanked-release plan is known for consequential packages/services.

## Artifacts and provenance

For downloadable artifacts:

- prefer deterministic packaging;
- produce SHA-256 checksums;
- record source commit/tag;
- record tool/runtime versions when reproducibility depends on them;
- do not claim signing/attestation unless it actually exists.

Use `supply_chain_guard.py --manifest <path>` only when the user explicitly wants a manifest written.

## Publish gate

Drafting a version, tag name, changelog, or release notes is read-only planning. Creating the tag/release/upload is a consequential write and must be previewed and confirmed.
