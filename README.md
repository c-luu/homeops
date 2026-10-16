# HomeOps

HomeOps is a modular personal operations monorepo for reusable CLI utilities,
automation workflows, and device integrations. The initial module focuses on
read-only-first verification of Google Pixel video backups on macOS, followed
by explicitly authorized cleanup.

## Status

The initial Python 3.12+ CLI foundation is under development. Verification
uses recursive local inventory, SHA-256 matching, and JSON/text reports.
Cleanup remains fail-closed and does not perform permanent deletion.

## Install

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
homeops --help
```

ADB is an external prerequisite. HomeOps does not install packages, change
device permissions, or modify media during diagnostics and verification.

## Configuration and verification

Copy `examples/config.toml` and set a machine-specific backup destination:

```bash
homeops doctor
homeops android devices
homeops android backup verify --profile pixel --config config.toml
homeops android cleanup plan --report verification-report.json
```

Reports and plans are local operational data and must not be committed.

The current Android compatibility target is standard `adb devices`, `find`,
`stat`, and `sha256sum` behavior. Unsupported output or unavailable commands
are reported as errors rather than guessed around; tests use mocked ADB
responses and do not require a physical device.

## Repository boundaries

`mac-dotfiles` owns workstation bootstrap, Homebrew Bundle, chezmoi, and
machine configuration. HomeOps owns reusable application logic, CLI tools,
device operations, validation, automation, and tests. Future iOS, Linux,
NAS/storage, networking, and homelab capabilities are backlog only and are
not implemented by the initial module.

### Future local OpenSpec Git-bundle archival

This capability is backlog-only and is intentionally not implemented in the
initial module. When eventually implemented, it must:

- require explicit user invocation for create, verify, and restore operations;
- snapshot ignored OpenSpec working-tree files into versioned history before
  creating a bundle, because Git bundles contain objects and refs rather than
  arbitrary ignored files;
- include the local `CHARTER.md` alongside the ignored OpenSpec working-tree
  files in that migration snapshot;
- not bundle `.venv`, compiled binaries, caches, or other machine-specific
  environment files; instead preserve the rebuild recipe (`pyproject.toml`,
  the supported Python version, installer/tool versions, and a future
  platform-appropriate lock or hash-pinned dependency export);
- record enough platform metadata to explain how the environment was rebuilt
  on the source Mac, while allowing dependencies to be resolved afresh on a
  compatible destination Mac;
- use an isolated local Git repository with no remote and never create a
  hosted repository, private mirror, or automatically pushed archival branch;
- preserve OpenSpec directory structure and useful change history;
- verify bundle integrity and support safe, repeatable archive and restore
  operations;
- restore onto another Mac only when explicitly requested.

Illustrative future commands are `homeops archive openspec create`,
`homeops archive openspec verify`, and
`homeops archive openspec restore --bundle <path>`. No archive command is
currently exposed by the CLI.

## OpenSpec privacy

OpenSpec planning documents are local-only. Public source may retain only
approved `.gitkeep` placeholders under `openspec/`; CI checks tracked paths to
prevent accidental publication. `CHARTER.md` is likewise local-only and is
reserved for the future explicit bundle workflow.