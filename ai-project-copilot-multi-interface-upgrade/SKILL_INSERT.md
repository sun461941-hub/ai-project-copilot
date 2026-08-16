## Multi-interface gateway

Keep the Agent Skill as the first-class conversational interface. When another AI agent, local automation, or an application needs the same deterministic Project Copilot capabilities, read `references/multi-interface-gateway.md` and use the shared multi-interface core instead of duplicating lane logic.

- Use `scripts/project_copilot.py` for local CLI automation.
- Use `scripts/project_copilot_mcp.py` for MCP stdio clients such as coding agents and IDEs; scope caller-visible paths with `--allow-root`.
- Use `scripts/project_copilot_api.py` for programmatic REST access; keep loopback as the default and require environment-sourced bearer authentication before any non-loopback bind.
- Use `scripts/project_copilot_core.py` as the single fixed capability registry underneath adapters.
- Keep arbitrary shell execution out of the gateway. Keep merge, publish, deploy, delete, permission changes, and repository writes behind explicit human-controlled workflows.
- Prefer `copilot_run` when a caller has a natural-language goal and individual capability names when the caller already knows the exact deterministic operation.

