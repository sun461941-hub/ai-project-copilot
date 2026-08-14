# Maintainer Mode

AI Project Copilot v1.1 adds an open-source maintainer workflow alongside its product-building workflow.

## What is new

- deterministic issue pre-triage;
- suggested `good first issue` detection with conservative rules;
- security/high-impact routing signals;
- explicit missing-evidence checks for bug reports;
- a maintainer operations playbook for issue triage, PR review, and releases;
- a public roadmap and changelog so project evolution is visible.

## Quick demo

```bash
python skills/ai-project-copilot/scripts/maintainer_triage.py \
  --issue-json examples/issue-triage-input.json \
  --format markdown
```

The helper does **not** mutate GitHub. It emits a reviewable suggestion so a maintainer keeps final control.

## Why this matters

The goal is to make AI useful for real repository maintenance rather than only project generation. Future iterations will add evidence-first PR review and release intelligence while preserving the same human-in-the-loop boundary.
