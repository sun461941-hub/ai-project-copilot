AI Project Copilot Multi-Interface Upgrade (2.2.0-preview.2)

Recommended:
  1) unzip this package
  2) python apply_multi_interface_patch.py /path/to/ai-project-copilot --dry-run
  3) python apply_multi_interface_patch.py /path/to/ai-project-copilot --run-tests

Alternative:
  git apply --check ai-project-copilot-multi-interface.patch
  git apply ai-project-copilot-multi-interface.patch
  python -m unittest -v tests/test_multi_interface_gateway.py

Rollback (installer path):
  python apply_multi_interface_patch.py /path/to/ai-project-copilot --rollback

Read PATCH_NOTES.zh-CN.md before remote/network deployment.
