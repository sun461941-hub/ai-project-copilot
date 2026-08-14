# Apply notes

Baseline: `sun461941-hub/ai-project-copilot` main commit `e3d6c7d`.

```bash
git status --short
git apply --check ai-project-copilot-v2.1.1-hardening.patch
git apply ai-project-copilot-v2.1.1-hardening.patch

# Remove repair/export artifacts that were mistakenly committed as source.
git rm -r --ignore-unmatch ai-project-copilot-patch
git rm --ignore-unmatch ai-project-copilot-fixed-source.zip
git rm --ignore-unmatch ai-project-copilot-repair-from-245cefc.patch
git rm --ignore-unmatch SHA256SUMS.txt

python tools/validate_skill.py skills/ai-project-copilot
python skills/ai-project-copilot/scripts/run_skill_evals.py --format json
python -m unittest discover -s tests -v
python -m compileall -q tools tests skills/ai-project-copilot/scripts
```

SHA-256: `82419bea193329a3314567e9b4799541c175754de29c7b68636961a486ef8170`
