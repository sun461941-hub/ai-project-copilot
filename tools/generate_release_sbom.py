#!/usr/bin/env python3
"""Generate a small, deterministic CycloneDX SBOM for a release artifact.

The distributable Skill has no third-party Python runtime dependencies.  This
SBOM therefore records the release application, its exact archive hash, and
the reviewed source commit rather than inventing a dependency inventory.  The
GitHub Actions attestation remains the provenance proof for the build itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


SEMVER_TAG = re.compile(r"v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?\Z")
COMMIT_SHA = re.compile(r"[0-9a-fA-F]{40}\Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_artifact(path: Path) -> Path:
    candidate = path.expanduser()
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError(f"artifact must be a regular non-symlink file: {candidate}")
    return candidate.resolve()


def _validated_version(value: str) -> str:
    if not SEMVER_TAG.fullmatch(value):
        raise ValueError(f"version must be a SemVer tag such as v2.3.0: {value!r}")
    return value


def _validated_commit(value: str) -> str:
    commit = value.strip().lower()
    if not COMMIT_SHA.fullmatch(commit):
        raise ValueError("source commit must be a 40-character hexadecimal SHA")
    return commit


def _validated_repository(value: str) -> str:
    repository = value.strip().rstrip("/")
    if not re.fullmatch(r"https://[^\s/]+(?:/[^\s/]+)+", repository):
        raise ValueError("repository must be an HTTPS repository URL")
    return repository


def build_sbom(artifact: Path, *, version: str, source_commit: str, repository: str) -> dict[str, Any]:
    artifact = _validated_artifact(artifact)
    version = _validated_version(version)
    source_commit = _validated_commit(source_commit)
    repository = _validated_repository(repository)
    archive_hash = _sha256(artifact)
    component_ref = f"pkg:generic/ai-project-copilot@{version}"
    artifact_ref = f"urn:aipc:artifact:{artifact.name}:{archive_hash}"
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "component": {
                "bom-ref": component_ref,
                "type": "application",
                "name": "ai-project-copilot",
                "version": version,
                "purl": component_ref,
                "externalReferences": [{"type": "vcs", "url": repository}],
            },
            "properties": [
                {"name": "aipc:source-commit", "value": source_commit},
                {"name": "aipc:sbom-scope", "value": "release-artifact-and-runtime-dependencies"},
            ],
        },
        "components": [
            {
                "bom-ref": artifact_ref,
                "type": "file",
                "name": artifact.name,
                "version": version,
                "hashes": [{"alg": "SHA-256", "content": archive_hash}],
                "properties": [{"name": "aipc:artifact-bytes", "value": str(artifact.stat().st_size)}],
            }
        ],
        "dependencies": [{"ref": component_ref, "dependsOn": [artifact_ref]}],
    }


def _write(path: Path, payload: dict[str, Any], *, force: bool) -> None:
    target = path.expanduser()
    if target.is_symlink():
        raise ValueError(f"refusing to write through symlinked output: {target}")
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise ValueError(f"output parent must be an existing regular directory: {target.parent}")
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if target.exists():
        if not force:
            raise ValueError(f"refusing to overwrite existing SBOM: {target}")
        if not target.is_file():
            raise ValueError(f"SBOM output must be a regular file: {target}")
        target.write_bytes(encoded)
        return
    with target.open("xb") as handle:
        handle.write(encoded)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True, help="Built release archive to describe.")
    parser.add_argument("--output", type=Path, required=True, help="New CycloneDX JSON output path.")
    parser.add_argument("--version", required=True, help="Reviewed SemVer tag, for example v2.3.0.")
    parser.add_argument("--source-commit", required=True, help="40-character reviewed source commit SHA.")
    parser.add_argument("--repository", required=True, help="HTTPS URL of the source repository.")
    parser.add_argument("--force", action="store_true", help="Replace an existing regular output file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = build_sbom(
            args.artifact,
            version=args.version,
            source_commit=args.source_commit,
            repository=args.repository,
        )
        _write(args.output, payload, force=args.force)
    except (OSError, ValueError) as exc:
        print(f"Release SBOM failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"artifact": args.artifact.as_posix(), "output": args.output.as_posix()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
