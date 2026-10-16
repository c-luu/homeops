# HomeOps

HomeOps is a modular personal operations monorepo for reusable CLI utilities,
automation workflows, device integrations, validation, and other personal
operations. Each capability uses shared HomeOps tooling rather than a
separate repository. The Android backup workflow below is the initial module,
not the definition of HomeOps as a whole.

## Status

The initial Python 3.12+ CLI foundation and Pixel-to-macOS backup workflow are
under development. Verification uses recursive inventory, SHA-256 matching,
and managed JSON/text reports. Cleanup remains fail-closed, defaults to
dry-run, and requires explicit acknowledgement before permanent deletion.

## Setup

### Responsibilities and dependencies

HomeOps declares Python package dependencies in `pyproject.toml`. The current
end-user installation is separate from the unpublished local
`mac-dotfiles` integration:

- HomeOps does not install Homebrew packages, change USB debugging
  authorization, or alter device permissions.
- The end-user CLI is installed with `uv tool` into an isolated environment;
  no source checkout or routine virtual-environment activation is required.

The current workflow requires Python 3.12+, the Homebrew
`android-platform-tools` cask (Android SDK Platform-Tools, which provides
`adb`), a USB-connected Pixel with USB debugging enabled, and a readable
LocalSend backup directory.

For the current public checkout, install HomeOps at the reviewed immutable
commit:

```bash
uv tool install --force \
  git+https://github.com/c-luu/homeops.git@1eeee602f4a904dafd9d2c36f636732c25463f8c
```

Install `android-platform-tools` using the workstation's normal package
manager. The optional `mac-dotfiles/Brewfile.homeops` and its installer are
not published as part of this HomeOps installation contract; review that
integration separately before using it.

### Developer setup

Only contributors working from a HomeOps source checkout need an editable
virtual environment:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
homeops --help
```

If the Mac uses another supported Python executable, substitute it for
`python3.12`; do not reuse a `.venv` created on another machine.

## Pixel backup verification and cleanup

This is the setup and operating procedure for the initial Android module. It
is intentionally separate from the general HomeOps description so future
operations tools can add their own workflows without making Android a
top-level assumption.

### One-time Pixel setup

Point setup at the existing directory that LocalSend already uses. Setup does
not create or initialize a backup destination:

```bash
homeops android setup \
  --destination "/existing/path/to/LocalSend/backup"
```

Setup discovers exactly one authorized device and writes a local TOML profile.
The destination is the directory populated by LocalSend. Device identifiers,
paths, reports, and plans remain local runtime data.

### Verify a backup

Unlock the phone, connect it over USB, enable USB debugging, and accept the
authorization prompt. Then run:

```bash
homeops android backup verify
```

Review the managed JSON and text reports under
`~/Library/Application Support/HomeOps`. Only files with one unambiguous Mac
backup and equal SHA-256 hashes are eligible for cleanup. Missing,
inaccessible, changed, duplicate, and ambiguous files remain ineligible.

### Plan and apply cleanup

```bash
homeops android cleanup plan
homeops android cleanup apply
```

Plan review is separate from deletion. Apply defaults to a dry-run and
revalidates the exact device, authorized Android source path, and source and
destination size/digest immediately before every action. Permanent cleanup is
disabled unless the profile explicitly uses `delete_mode = "permanent"` and
the user supplies the advanced approvals:

```bash
homeops android cleanup apply \
  --permanent \
  --acknowledge-permanent \
  --confirm \
  --execute
```

Use this only after reviewing the plan and dry-run. Per-file results and
checkpoints are kept in the local state directory. Disconnects, changed files,
uncertain deletion outcomes, and incomplete results fail closed; advanced
`--config`, `--output`, `--report`, and `--plan` options remain available for
scripting and troubleshooting.

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

## Existing VS Code workspace

The current multi-root workspace is an editing convenience, not a deployment
system. It contains the independent roots `homeops` and `../mac-dotfiles`
relative to the workspace file. On another Mac, clone the repositories as
sibling directories, or manually recreate an equivalent workspace with those
relative paths. Do not put credentials, device identifiers, runtime state, or
OpenSpec planning files in the workspace. Additional independently maintained
repositories can be added later without merging these two repositories.

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