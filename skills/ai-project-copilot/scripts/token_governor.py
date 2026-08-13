#!/usr/bin/env python3
"""Choose a conservative context/verification budget for a coding-agent task.

This helper does not change a model setting. It emits a reviewable recommendation
for how much repository context, verification, and multi-agent work is justified.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath

FAST_TERMS = (
    "typo", "spelling", "readme", "documentation", "docs", "comment", "formatting",
    "format only", "rename", "copy edit", "wording", "small doc", "changelog text",
    "错别字", "文档", "说明文档", "注释", "格式化", "重命名", "措辞",
)
BALANCED_TERMS = (
    "bug", "fix", "feature", "refactor", "test", "review", "pull request", "pr review",
    "endpoint", "component", "cli", "validation", "regression", "performance",
    "修复", "错误", "功能", "重构", "测试", "审查", "评审", "拉取请求", "性能",
)
DEEP_TERMS = (
    "architecture", "migration", "schema", "database", "security", "authentication",
    "authorization", "permission", "release", "deploy", "deployment", "supply chain",
    "github actions", "workflow", "mcp", "concurrency", "distributed", "breaking change",
    "rewrite", "full audit", "comprehensive audit", "cross platform", "cross-platform",
    "架构", "迁移", "数据库", "安全", "认证", "授权", "权限", "发布", "部署",
    "供应链", "工作流", "并发", "分布式", "破坏性变更", "全面审计", "跨平台",
)
DEEP_PATH_PARTS = {
    ".github", "migrations", "migration", "schema", "auth", "security", "infra",
    "infrastructure", "deploy", "deployment", "terraform", "k8s", "kubernetes",
}
DOC_SUFFIXES = {".md", ".rst", ".txt", ".adoc"}
DOC_NAMES = {"readme", "changelog", "roadmap", "contributing", "security", "license"}


def _contains_phrase(text: str, phrase: str) -> bool:
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(phrase.casefold()) + r"(?![A-Za-z0-9_])"
    return re.search(pattern, text.casefold()) is not None


def _hits(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if _contains_phrase(text, term)]


def _normalize_path(value: str) -> str:
    text = value.replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip("/")


def _is_docs_only(paths: list[str]) -> bool:
    if not paths:
        return False
    for raw in paths:
        path = PurePosixPath(_normalize_path(raw))
        name = path.name.casefold()
        stem = path.stem.casefold()
        if path.suffix.casefold() in DOC_SUFFIXES:
            continue
        if stem in DOC_NAMES or name in DOC_NAMES:
            continue
        return False
    return True


def _has_deep_path(paths: list[str]) -> list[str]:
    hits: list[str] = []
    for raw in paths:
        path = PurePosixPath(_normalize_path(raw))
        low_parts = {part.casefold() for part in path.parts}
        low = path.as_posix().casefold()
        if low_parts & DEEP_PATH_PARTS or low.startswith(".github/workflows/"):
            hits.append(path.as_posix())
            continue
        if path.name.casefold() in {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "cargo.lock", "go.sum"}:
            hits.append(path.as_posix())
    return hits


@dataclass(frozen=True)
class BudgetPlan:
    mode: str
    score: int
    reasons: list[str]
    max_focus_files: int
    max_reference_files: int
    max_log_lines: int
    verification: str
    multi_agent: str
    recommended_reasoning_effort: str
    cache_policy: str


def plan_task(prompt: str, changed_files: list[str] | None = None) -> BudgetPlan:
    changed = [_normalize_path(x) for x in (changed_files or []) if _normalize_path(x)]
    fast_hits = _hits(prompt, FAST_TERMS)
    balanced_hits = _hits(prompt, BALANCED_TERMS)
    deep_hits = _hits(prompt, DEEP_TERMS)
    deep_paths = _has_deep_path(changed)

    score = 0
    reasons: list[str] = []
    if fast_hits:
        score -= min(2, len(fast_hits))
        reasons.append("low-risk task signals: " + ", ".join(fast_hits[:4]))
    if balanced_hits:
        score += min(2, len(balanced_hits))
        reasons.append("implementation/review signals: " + ", ".join(balanced_hits[:4]))
    if deep_hits:
        score += 4 + min(3, len(deep_hits) - 1)
        reasons.append("high-risk/broad task signals: " + ", ".join(deep_hits[:4]))
    if deep_paths:
        score += 5
        reasons.append("high-risk changed paths: " + ", ".join(deep_paths[:4]))
    if changed:
        if len(changed) >= 9:
            score += 4
            reasons.append(f"broad change surface: {len(changed)} files")
        elif len(changed) >= 3:
            score += 2
            reasons.append(f"multi-file change surface: {len(changed)} files")
        elif _is_docs_only(changed):
            score -= 3
            reasons.append("changed files are documentation-only")
        else:
            reasons.append(f"narrow change surface: {len(changed)} file(s)")

    if deep_hits or deep_paths or score >= 5:
        return BudgetPlan(
            mode="DEEP",
            score=score,
            reasons=reasons or ["broad or consequential task"],
            max_focus_files=48,
            max_reference_files=8,
            max_log_lines=160,
            verification="full relevant tests; security/release gates never rely on cache alone",
            multi_agent="allowed only for genuinely parallel subproblems; serialize writes and final verification",
            recommended_reasoning_effort="high",
            cache_policy="reuse non-critical evidence only when command and input fingerprints match exactly",
        )
    if score <= -2 or (_is_docs_only(changed) and not balanced_hits):
        return BudgetPlan(
            mode="FAST",
            score=score,
            reasons=reasons or ["narrow low-risk task"],
            max_focus_files=8,
            max_reference_files=2,
            max_log_lines=50,
            verification="smallest relevant validation; do not run full-suite checks unless evidence requires it",
            multi_agent="single agent",
            recommended_reasoning_effort="low",
            cache_policy="reuse exact-fingerprint non-critical evidence; invalidate on relevant input change",
        )
    return BudgetPlan(
        mode="BALANCED",
        score=score,
        reasons=reasons or ["normal repository task"],
        max_focus_files=20,
        max_reference_files=4,
        max_log_lines=90,
        verification="targeted tests first, broaden only when failures/risk justify it",
        multi_agent="single agent by default; add one reviewer only when independent review has clear value",
        recommended_reasoning_effort="medium",
        cache_policy="reuse exact-fingerprint non-critical evidence; refresh tests affected by changed files",
    )


def markdown(plan: BudgetPlan) -> str:
    lines = [
        "# Token/context budget",
        "",
        f"- Mode: **{plan.mode}**",
        f"- Score: **{plan.score}**",
        f"- Focus-file cap: **{plan.max_focus_files}**",
        f"- Reference-file cap: **{plan.max_reference_files}**",
        f"- Log-line cap: **{plan.max_log_lines}**",
        f"- Suggested reasoning effort (only if the client exposes it): **{plan.recommended_reasoning_effort}**",
        f"- Verification: {plan.verification}",
        f"- Multi-agent: {plan.multi_agent}",
        f"- Cache: {plan.cache_policy}",
        "",
        "## Why",
    ]
    lines.extend(f"- {reason}" for reason in plan.reasons)
    lines.extend([
        "",
        "> This is a workload recommendation. The Skill cannot change Codex backend token rate, quota, or model settings by itself.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    plan = plan_task(args.prompt, args.changed_file)
    if args.format == "json":
        print(json.dumps(asdict(plan), ensure_ascii=False, indent=2))
    else:
        print(markdown(plan), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
