## Summary

This update fixes the Python 3.14 CI failure and applies the previously generated fixes to the actual project source instead of committing repair bundles as repository content.

## Changes

- Add a string-aware JSON nesting guard before `json.loads()` to prevent unsafe deeply nested input from reaching the parser.
- Return clean CLI errors for malformed issue JSON instead of exposing tracebacks.
- Reject dangling symlink targets before generated files or integrity manifests are written.
- Replace timing-sensitive `sleep()`-based concurrency tests with event-based synchronization.
- Improve CI diagnostics with deterministic environment settings, runtime version output, and a job timeout.
- Add regression coverage for JSON depth handling, malformed input, symlink protection, SQLite locking, and lease renewal.
- Ignore local patch/export artifacts so generated delivery bundles are not recommitted as project source.
- Add v2.1.1 hardening release notes.

## Root cause

The failing workflow only affected Ubuntu with Python 3.14. The test suite passed an extremely deeply nested JSON document directly to the CPython JSON parser and relied on catching `RecursionError`. Parser behavior at that depth can vary by Python build, platform stack limits, and runtime implementation.

The repository also contained repair ZIP/patch files from earlier fix attempts, but those changes were not applied to the real source tree, so CI continued to run the original code.

## Verification

Run:

```bash
python tools/validate_skill.py skills/ai-project-copilot
python skills/ai-project-copilot/scripts/run_skill_evals.py --format json
python -m unittest discover -s tests -v
python -m compileall -q tools tests skills/ai-project-copilot/scripts
git diff --check
```

Target baseline: `main` commit `e3d6c7d`.
