#!/usr/bin/env python3
"""Deterministically route repository/product requests to AI Project Copilot lanes."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class Route:
    mode: str
    score: int
    reasons: list[str]
    resources: list[str]


LANES: dict[str, dict[str, object]] = {
    "discover": {
        "terms": ("map", "architecture", "onboard", "understand repo", "codebase", "context", "where is", "ai-ready", "agents.md", "copilot instructions", "skill stack", "installed skill", "installed skills", "影响范围", "架构", "代码库", "上下文", "梳理", "技能栈", "智能体指令"),
        "resources": ("scripts/repo_context.py", "scripts/ai_ready_bootstrap.py", "scripts/skill_stack_audit.py", "references/codebase-context.md", "references/ai-ready-and-skill-stack.md"),
    },
    "launch": {
        "terms": ("new ai", "idea", "mvp", "greenfield", "project direction", "build ai", "新项目", "创意", "mvp", "从零"),
        "resources": ("references/showcase-projects.md", "scripts/rank_blueprints.py"),
    },
    "retrofit": {
        "terms": ("add ai", "retrofit", "existing app", "rag", "agent", "multimodal", "local model", "接入ai", "加入ai", "现有项目"),
        "resources": ("references/feature-modules.md", "references/architecture-playbook.md"),
    },
    "maintain": {
        "terms": ("issue", "triage", "good first issue", "contributor", "maintain", "backlog", "label", "github export", "github json", "evidence bundle", "evidence ledger", "run-state", "maintainer dashboard", "维护", "议题", "贡献者", "分类", "证据台账", "维护台账", "维护看板"),
        "resources": ("scripts/maintainer_triage.py", "scripts/github_evidence_sync.py", "scripts/run_state_ledger.py", "scripts/render_maintainer_dashboard.py", "references/maintainer-ops.md", "references/github-evidence-ledger.md"),
    },
    "review": {
        "terms": ("pr", "pull request", "diff", "review", "breaking change", "risk", "review thread", "代码审查", "合并请求", "变更风险"),
        "resources": ("scripts/change_risk.py", "references/pr-review-loop.md"),
    },
    "release": {
        "terms": ("release", "semver", "version", "tag", "changelog", "release notes", "publish", "版本", "发布", "更新日志", "标签"),
        "resources": ("scripts/release_intel.py", "references/release-intelligence.md"),
    },
    "secure": {
        "terms": ("security", "secret", "permission", "supply chain", "github actions", "workflow", "vulnerability", "安全", "密钥", "权限", "供应链"),
        "resources": ("scripts/supply_chain_guard.py", "references/security-governance.md"),
    },
    "quality": {
        "terms": ("test", "eval", "quality", "regression", "coverage", "benchmark", "verify", "测试", "评测", "质量", "回归", "验证"),
        "resources": ("references/quality-orchestration.md", "evals/evals.json"),
    },
    "showcase": {
        "terms": ("readme", "demo", "launch", "portfolio", "showcase", "screenshot", "star", "presentation", "演示", "展示", "readme", "开源发布"),
        "resources": ("references/experience-and-demo.md", "assets/templates/demo-script.md"),
    },
}


def _term_matches(text: str, term: str) -> bool:
    folded = text.casefold()
    needle = term.casefold()
    # Chinese trigger phrases do not use ASCII word boundaries.
    if re.search(r"[\u4e00-\u9fff]", needle):
        return needle in folded
    # Avoid substring accidents such as `pr` in `prepare`, `map` in
    # `roadmap`, or `tag` in `stage`. Hyphenated/dotted phrases still work.
    pattern = rf"(?<![a-z0-9_]){re.escape(needle)}(?![a-z0-9_])"
    return re.search(pattern, folded) is not None


def _hits(text: str, terms: Iterable[str]) -> list[str]:
    hits: list[str] = []
    seen: set[str] = set()
    for term in terms:
        folded = term.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        if _term_matches(text, term):
            hits.append(term)
    return hits


def route(prompt: str) -> list[Route]:
    prompt = re.sub(r"\s+", " ", prompt.strip())
    routes: list[Route] = []
    for mode, config in LANES.items():
        terms = tuple(str(x) for x in config["terms"])
        hits = _hits(prompt, terms)
        score = len(hits) * 10
        reasons: list[str] = []
        if hits:
            reasons.append(f"matched: {', '.join(hits[:5])}")
        if mode == "discover" and _hits(prompt, ("repo", "repository", "仓库")):
            score += 3
            reasons.append("repository-level request benefits from context discovery")
        if mode == "quality" and _hits(prompt, ("review", "release", "发布", "审查")):
            score += 2
            reasons.append("review/release work benefits from a verification gate")
        if score:
            routes.append(Route(mode, score, reasons, list(config["resources"])))

    if not routes:
        routes.append(Route("discover", 1, ["no strong lane keyword; inspect repository context first"], list(LANES["discover"]["resources"])))

    routes.sort(key=lambda item: (-item.score, item.mode))
    return routes[:4]


def markdown(routes: list[Route]) -> str:
    lines = ["# AI Project Copilot route", ""]
    for index, item in enumerate(routes, start=1):
        lines.append(f"## {index}. {item.mode} — score {item.score}")
        lines.extend(f"- {reason}" for reason in item.reasons)
        lines.append("- Load: " + ", ".join(f"`{r}`" for r in item.resources))
        lines.append("")
    lines.append("> Routing is deterministic guidance; repository evidence may justify overriding it.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    routes = route(args.prompt)
    if args.format == "json":
        print(json.dumps([asdict(item) for item in routes], indent=2, ensure_ascii=False))
    else:
        print(markdown(routes), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
