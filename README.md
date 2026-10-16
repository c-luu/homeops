# HomeOps

HomeOps is a modular personal operations monorepo for reusable CLI utilities,
automation workflows, device integrations, validation, and other personal
operations. Each capability uses shared HomeOps tooling rather than a
separate repository. The Android backup workflow below is the initial module,
not the definition of HomeOps as a whole.

## Status

The initial Python 3.12+ CLI foundation and Pixel-to-macOS backup workflow are
under development. Verification uses recursive inventory, SHA-256 matching,
and JSON/text reports. Cleanup remains fail-closed, defaults to dry-run, and
requires explicit acknowledgement before permanent deletion.

## Setup

### Responsibilities and dependencies

HomeOps declares Python package dependencies in `pyproject.toml`. Workstation
prerequisites belong in `mac-dotfiles`, because that repository owns Homebrew
Bundle manifests and machine bootstrap:

- `mac-dotfiles/Brewfile` should provide the supported Python runtime, and its
  selected Brew Bundle manifest should provide the
  `android-platform-tools` cask (`adb`) on each Mac.
- HomeOps does not install Homebrew packages, change USB debugging
  authorization, or alter device permissions.
- The HomeOps Python environment is created locally from repository metadata;
  `.venv` is machine-specific and must not be copied between Macs.

The current workflow requires Python 3.12+, the Homebrew
`android-platform-tools` cask (Android SDK Platform-Tools, which provides
`adb`), a USB-connected Pixel with USB debugging enabled, and a readable
LocalSend backup directory.

The corresponding `mac-dotfiles` manifest entries are expected to be
equivalent to:

```ruby
brew "python@3.12"
cask "android-platform-tools"
```

Use the manifest selected by the workstation profile and verify formula names
against the current Homebrew catalog before adding them. These are machine
prerequisites, not HomeOps Python dependencies.

```bash
# Run mac-dotfiles workstation bootstrap first, including platform-tools and
# the supported Python Brew dependencies.
brew bundle install --no-upgrade --file=/path/to/mac-dotfiles/Brewfile

# Then create an isolated environment in this checkout.
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

### 1. Configure the LocalSend destination

```bash
cp examples/config.toml config.toml
$EDITOR config.toml
```

Set `backups.pixel.destination` to the actual LocalSend folder on the Mac
mini. The profile searches configured Android shared-storage roots recursively;
do not assume the selected video count equals the successful backup count.

### 2. Connect and diagnose the Pixel

Unlock the phone, connect it over USB, enable USB debugging, and accept the
authorization prompt on the phone:

```bash
homeops doctor
homeops android devices
```

Continue only when exactly one device is authorized.

### 3. Run read-only verification

```bash
homeops android backup verify \
  --profile pixel \
  --config config.toml \
  --output verification-report.json
```

Review `verification-report.json` and `verification-report.txt`. Only files
with one unambiguous Mac backup and equal SHA-256 hashes are eligible for
cleanup. Missing, inaccessible, changed, duplicate, and ambiguous files
remain ineligible.

### 4. Generate and review a cleanup plan

```bash
homeops android cleanup plan \
  --report verification-report.json \
  --output cleanup-plan.json
```

Review every source and destination in `cleanup-plan.json`. Plan generation
does not delete anything.

### 5. Run the default dry-run

```bash
homeops android cleanup apply \
  --plan cleanup-plan.json \
  --config config.toml \
  --confirm
```

Before execution, HomeOps revalidates the Pixel source size and SHA-256 hash
and the Mac destination size and SHA-256 hash. A disconnect or change stops
cleanup rather than silently treating the file as safe.

### 6. Explicit permanent cleanup

The current ADB integration has no verified recoverable Trash operation.
Deletion is therefore permanent and requires separate acknowledgement:

```bash
homeops android cleanup apply \
  --plan cleanup-plan.json \
  --config config.toml \
  --confirm \
  --permanent \
  --acknowledge-permanent \
  --execute
```

Use this only after reviewing the plan and dry-run. Per-file results are
persisted beside the plan as `cleanup-plan.result.json`; interrupted or failed
operations must be reviewed and revalidated before retry.

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