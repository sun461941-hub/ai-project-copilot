# Reproducible example request

Use `$ai-project-copilot` to rank project directions for an Android application that:

- runs locally where practical;
- creates short video clips;
- has a strong visual demo;
- makes privacy and mobile constraints explicit;
- does not train, host, bundle, or redistribute third-party model weights.

Equivalent deterministic helper command:

```bash
python skills/ai-project-copilot/scripts/rank_blueprints.py \
  --priorities local-first,video,android,visual-demo \
  --constraints privacy,mobile \
  --limit 3 \
  --json
```

Expected first result: **Android Local Video Runtime**.
