# Repository map and maintenance boundaries

This map keeps the repository understandable without deleting the patch and
recovery assets that users may still rely on. Read it before moving, deleting,
or regenerating files.

## Canonical product source

| Location | Purpose | Change rule |
| --- | --- | --- |
| [`skills/ai-project-copilot/`](../skills/ai-project-copilot/) | The distributable Agent Skill: metadata, references, templates, scripts, and eval fixtures. | Treat this as the canonical product source. Update behavior, documentation, tests, and version metadata together. |
| [`tests/`](../tests/) | Unit and regression coverage for the canonical Skill and tooling. | Add deterministic coverage for behavior changes and preserve cross-platform paths. |
| [`evals/`](../evals/) and [`skills/ai-project-copilot/evals/`](../skills/ai-project-copilot/evals/) | Repository and Skill evaluation fixtures. | Keep prompts, expectations, and trigger cases evidence-based; they are not model-output proof. |
| [`tools/`](../tools/) | Validation, packaging, and controlled patch-application helpers. | Preserve their fail-closed path and integrity checks. |

## Automation and governance

| Location | Purpose |
| --- | --- |
| [`.github/workflows/`](../.github/workflows/) | CI, mobile-patch workflows, and the manually dispatched release workflow. |
| [`CHANGELOG.md`](../CHANGELOG.md) | Public release history. Update it before creating a release tag. |
| [`CONTRIBUTING.md`](../CONTRIBUTING.md), [`SECURITY.md`](../SECURITY.md), [`ROADMAP.md`](../ROADMAP.md) | Contribution, security, and project-governance contracts. |
| [`README.md`](../README.md) and [`README.zh-CN.md`](../README.zh-CN.md) | Public entry points. Keep version and release guidance aligned with the changelog and published tag. |

## Historical patch and recovery assets

The following files are intentionally retained at the repository root. They are
not ordinary build output and must not be deleted just to make the tree look
smaller.

| Location | Purpose | Safe handling |
| --- | --- | --- |
| [`ai-project-copilot-patch/`](../ai-project-copilot-patch/) and [`FIX5_MANIFEST.json`](../FIX5_MANIFEST.json) | Versioned fix-5 compatibility patch and its manifest. | Change only when deliberately regenerating that patch; then rebuild every affected hash/manifest and validate a clean application. |
| [`ai-project-copilot-multi-interface-upgrade/`](../ai-project-copilot-multi-interface-upgrade/) | Independently versioned multi-interface preview upgrade, installer, payload, tests, and manifest. | Treat it as a distributable compatibility package. Keep its patch, payload, `MANIFEST.json`, and `SHA256SUMS.txt` in sync. |
| [`FIX5_MOBILE_PATCH_NOTES.zh-CN.md`](../FIX5_MOBILE_PATCH_NOTES.zh-CN.md), [`FIX6_MOBILE_GUIDE.zh-CN.md`](../FIX6_MOBILE_GUIDE.zh-CN.md), and [`ai-project-copilot-audit-fixes.patch`](../ai-project-copilot-audit-fixes.patch) | Reviewable mobile/recovery instructions and audit evidence. | Preserve history and provenance. Supersede through a documented new asset rather than silent replacement. |
| [`SHA256SUMS.txt`](../SHA256SUMS.txt) | Root integrity manifest for the checked-in patch artifacts. | Recalculate only as part of a reviewed artifact change. |

## Generated local state

`dist/`, `.aipc/`, `__pycache__/`, test databases, temporary patch state, and
similar local outputs are generated state. They should remain ignored and should
not be committed unless a specific reproducible fixture requires it. Do not
place secrets, API keys, or raw private logs in any of these paths.

## Change routes

1. **Canonical Skill change:** update `skills/`, its unit tests and evals, then
   run the full verification suite below. Do not claim a behavior is shipped
   until the packaged archive is reproducible.
2. **Patch-package change:** update the installer/payload/patch together, run
   the package-specific tests, and regenerate every affected checksum or
   manifest. Test application from a clean target rather than the working tree.
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
python tools/apply_fix6_mobile_release.py --repo . --check
python skills/ai-project-copilot/scripts/run_skill_evals.py --format json
python -m unittest discover -s tests -v
python -m compileall -q tools tests skills/ai-project-copilot/scripts
sha256sum -c SHA256SUMS.txt
(cd ai-project-copilot-patch && sha256sum -c SHA256SUMS.txt)
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
