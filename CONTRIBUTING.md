# Contributing

Thanks for helping make AI Project Copilot more useful and more trustworthy.

## Good contributions

- a project blueprint with a distinct user problem and 60-second wow moment;
- a clearer feature gate, architecture boundary, or failure fallback;
- a deterministic helper script or regression test;
- a real-world trigger prompt, especially a near-miss negative case;
- cross-platform fixes that preserve path safety and non-overwriting behavior;
- improved privacy, model-license, accessibility, or evaluation guidance.

Avoid adding generic prompt collections, provider marketing, unverified benchmarks, model weights, or project ideas whose only differentiator is “add a chatbot.”

## Development

Requirements: Python 3.10 or newer. No third-party Python packages are required.

```bash
python tools/validate_skill.py skills/ai-project-copilot
python -m unittest discover -s tests -v
python tools/package_skill.py skills/ai-project-copilot \
  --output dist/ai-project-copilot.skill.zip
```

Optional context-efficiency benchmark:

```bash
python benchmarks/run_context_efficiency.py --repeats 7
```

Efficiency changes must report the same task/fixture before and after. Do not convert character/path proxies into claimed model-token savings unless real client/API usage telemetry was collected.

## Repository map and asset boundaries

Read [`docs/repository-map.md`](docs/repository-map.md) before moving, deleting,
or regenerating files. The active multi-interface package and its checksum
manifests are maintained compatibility assets; one-off repair kits for
superseded baselines belong in Git history rather than the current working tree.

## Adding a blueprint

Update both the structured catalog and its human-readable reference:

1. add one object to `skills/ai-project-copilot/references/blueprints.json`;
2. add the matching section to `showcase-projects.md`;
3. include a unique ID, category, pitch, 60-second wow moment, minimum vertical slice, modules, tags, and complexity;
4. add or update a ranking test if the new tags affect expected ordering;
5. keep the project focused enough for one primary demo journey.

A blueprint should not claim support, performance, privacy, or licensing that its eventual implementation would still need to prove.

## Pull requests

- Keep changes focused.
- Explain the user problem and why the change belongs in the skill.
- List commands run and results.
- Add tests for deterministic behavior.
- Do not include secrets, private traces, third-party model weights, or copyrighted demo assets without permission.
- Preserve UTF-8, LF line endings, and cross-platform paths.

## Commit style

Clear imperative messages are preferred, for example:

```text
Add local model manifest guidance
Fix deterministic ZIP metadata on Windows
Expand negative trigger evals
```
