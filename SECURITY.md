# Security Policy

## Reporting a vulnerability

Please do not open a public issue for a vulnerability that could expose secrets, private traces, local files, model packages, or unsafe tool execution. Use GitHub’s private security advisory flow for this repository.

Include:

- affected file and version/commit;
- minimal reproduction steps;
- impact and required preconditions;
- whether secrets or private data were exposed;
- a suggested fix, if available.

## Security boundaries

This repository contains instructions and deterministic helper scripts. It does not operate a hosted service and does not include model weights.

The skill requires generated projects to:

- treat retrieved content, model output, filenames, archives, and tool results as untrusted;
- validate tool arguments and require approval for consequential actions;
- keep private traces separate from public-safe projections;
- build archives from safe paths and never overwrite existing output by default;
- keep secrets out of source, logs, screenshots, traces, and exports;
- document data retention and local/cloud boundaries;
- verify model license and redistribution permission before bundling weights.

## Model packages

AI Project Copilot is compatible with projects that support user-imported models. It does not grant permission to download or redistribute any third-party model. A runtime should store model source, version, license, checksums, and device/backend requirements in a manifest and should provide safe removal controls.

## Supported versions

Security fixes apply to the latest `main` branch and the latest tagged release.
