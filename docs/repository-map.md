# Repository map and maintenance boundaries

This map keeps the repository understandable while retaining only active,
verifiable delivery assets. Read it before moving, deleting, or regenerating
files.

## Canonical product source

| Location | Purpose | Change rule |
| --- | --- | --- |
| [`skills/ai-project-copilot/`](../skills/ai-project-copilot/) | The distributable Agent Skill: metadata, references, templates, scripts, and eval fixtures. | Treat this as the canonical product source. Update behavior, documentation, tests, and version metadata together. |
| [`tests/`](../tests/) | Unit and regression coverage for the canonical Skill and tooling. | Add deterministic coverage for behavior changes and preserve cross-platform paths. |
| [`evals/`](../evals/) and [`skills/ai-project-copilot/evals/`](../skills/ai-project-copilot/evals/) | Repository and Skill evaluation fixtures. | Keep prompts, expectations, and trigger cases evidence-based; they are not model-output proof. |
| [`tools/`](../tools/) | Validation, packaging, and release-support helpers. | Preserve their fail-closed path and integrity checks. |
| [`examples/github-export/`](../examples/github-export/) | Offline fixtures for the read-only GitHub evidence workflow. | Keep them synthetic, secret-free, and aligned with the documented import contract. |

## Automation and governance

| Location | Purpose |
| --- | --- |
| [`.github/workflows/`](../.github/workflows/) | Read-only CI (including the stable `CI / gate`) and the manually dispatched release workflow. |
| [`.github/CODEOWNERS`](../.github/CODEOWNERS) | Intended reviewer boundary for workflows, packaging, budget controls, and preview gateway adapters. It is enforced only after the GitHub Ruleset requires code-owner review. |
| [`CHANGELOG.md`](../CHANGELOG.md) | Public release history. Update it before creating a release tag. |
| [`CONTRIBUTING.md`](../CONTRIBUTING.md), [`SECURITY.md`](../SECURITY.md), [`ROADMAP.md`](../ROADMAP.md) | Contribution, security, and project-governance contracts. |
| [`DEMO.md`](../DEMO.md), [`semantic-eval-protocol.md`](semantic-eval-protocol.md) | The fixed first-success journey and the separate real-model evaluation contract. |
| [`README.md`](../README.md) and [`README.zh-CN.md`](../README.zh-CN.md) | Public entry points. Keep version and release guidance aligned with the changelog and published tag. |

## Active compatibility package

| Location | Purpose | Safe handling |
| --- | --- | --- |
| [`ai-project-copilot-multi-interface-upgrade/`](../ai-project-copilot-multi-interface-upgrade/) | Independently versioned multi-interface preview upgrade, installer, payload, tests, and manifest. | Treat it as a distributable compatibility package. Keep its patch, payload, `MANIFEST.json`, and `SHA256SUMS.txt` in sync. |
| [`SHA256SUMS.txt`](../SHA256SUMS.txt) | Root integrity manifest for the active multi-interface package's checked-in release artifacts. | Recalculate only as part of a reviewed artifact change. |

One-time repair kits targeting superseded baselines are deliberately retired from
the working tree after their fixes are merged and covered by canonical tests.
Their commits remain available through Git history; do not restore them as
write-capable workflows or root-level patch clutter.

## Generated local state

`dist/`, `.aipc/`, `__pycache__/`, test databases, temporary patch state, and
similar local outputs are generated state. They should remain ignored and should
not be committed unless a specific reproducible fixture requires it. Do not
place secrets, API keys, raw private logs, or private GitHub export/ledger data
in any of these paths.

## Change routes

1. **Canonical Skill change:** update `skills/`, its unit tests and evals, then
   run the full verification suite below. Do not claim a behavior is shipped
   until the packaged archive is reproducible.
2. **Multi-interface package change:** update the installer/payload/patch
   together, run the package-specific tests, and regenerate every affected
   checksum or manifest. Test application from a clean target rather than the
   working tree.
3. **Documentation-only change:** keep the English and Chinese public entry
   points consistent where they state versions, installation, or release
   governance. Run at least the structural validation and diff check.
4. **Workflow or release change:** review permissions, pinned action refs, and
   provenance before merging. A passing CI run does not replace the required
   human confirmation for publication.

## Maintainer verification

Run these commands from the repository root before merging a release candidate:

```bash
python tools/validate_skill.py skills/ai-project-copilot
python skills/ai-project-copilot/scripts/run_skill_evals.py --format json
python -m unittest discover -s tests -v
python -m compileall -q tools tests skills/ai-project-copilot/scripts
sha256sum -c SHA256SUMS.txt
(cd ai-project-copilot-multi-interface-upgrade && sha256sum -c SHA256SUMS.txt)
python tools/package_skill.py skills/ai-project-copilot \
  --output dist/ai-project-copilot.skill.zip
```

On Windows, use an equivalent SHA-256 check if `sha256sum` is unavailable. CI
runs the same release-critical validation on supported platforms.

## Release route

1. Finish the change, changelog, version metadata, and public README updates.
2. Pass the verification suite and the required CI checks on the reviewed main
   commit.
3. Create an annotated SemVer tag on that exact commit.
4. Manually dispatch the **Release** workflow with the existing tag. Its
   validation job rebuilds the archive; the `release` environment remains the
   publication gate.
5. Confirm the GitHub Release contains the generated skill archive and its
   checksum before announcing it.

Publication, tag creation, permissions, merges, and deletion remain
consequential actions; this map documents the route but never removes the need
for explicit maintainer approval.
