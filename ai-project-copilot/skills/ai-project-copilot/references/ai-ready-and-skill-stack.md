# AI-ready repository and Skill Stack operations

Use this reference when the task is about making a repository easier for coding agents to understand, auditing installed skills, or resolving trigger overlap.

## AI-ready bootstrap

`../scripts/ai_ready_bootstrap.py` creates a conservative draft `AGENTS.md` and, optionally, `.github/copilot-instructions.md` from repository evidence.

Rules:

- never overwrite an existing instruction file unless the user explicitly opts into `--force`;
- treat generated instructions as a reviewable starting point, not discovered project truth;
- keep commands evidence-linked to manifests, CI, and docs rather than inventing them;
- preserve vendor-neutral instructions in `AGENTS.md`; use client-specific files only when the user wants them.

Example:

```bash
python scripts/ai_ready_bootstrap.py --repo /path/to/repo --target agents --target copilot --json
```

## Local Skill Stack audit

`../scripts/skill_stack_audit.py` inventories local Agent Skills without executing or installing them. It reports:

- skill name/path;
- description and portability warnings;
- script/reference/asset counts;
- duplicate names;
- likely description/trigger overlap.

Use overlap as a prompt to tighten trigger descriptions or reduce the stack. Do not treat token overlap as proof that two skills are functionally redundant.

Example:

```bash
python scripts/skill_stack_audit.py --project /path/to/repo --format markdown
```

## Network and installation boundary

Discovery on the public internet, installation, updates, or trust decisions are separate actions. Do not install, update, execute, or recommend a third-party Skill solely because it is popular. Verify repository identity, compatibility, permissions, bundled scripts, and security signals first.
