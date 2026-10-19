# HomeOps

HomeOps is a modular personal operations monorepo for reusable CLI utilities,
automation workflows, device integrations, validation, and other personal
operations. Each capability uses shared HomeOps tooling rather than a
separate repository. The Android media cleanup workflow below is the initial module,
not the definition of HomeOps as a whole.

## Status

The Python 3.12+ CLI verifies videos already transferred manually with LocalSend.
Verification uses scoped inventory, SHA-256 matching,
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

## Pixel LocalSend verification and cleanup

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

### Verify manually transferred videos

Select videos on the Pixel yourself and transfer them with LocalSend. Wait for
the files to arrive in its configured Mac destination. HomeOps does not initiate
transfers, manage backups, synchronize devices, or infer successful arrivals
from LocalSend's selected-file count. It never deletes Mac media.

Unlock the phone, connect it over USB, enable USB debugging, and accept the
authorization prompt. Then run:

```bash
homeops android backup verify
```

The default scope is the entire configured LocalSend destination, including
older arrivals, recursively. An explicit `recursive = false` profile scans
direct files only; the effective scope and depth are shown in review. To narrow
the scan to an existing subfolder, use `--within` relative to that destination:

```bash
homeops android backup verify --within received-videos
```

This selects a subtree; it does not track transfers or identify the latest
batch. Invalid, missing, escaping, or symlink scopes are rejected without
falling back to a broader scan. Pixel matching still covers every configured
Android root to detect duplicate originals outside the Mac subtree.

Supported video suffixes are `.mp4`, `.m4v`, `.mov`, `.mkv`, `.webm`, `.3gp`,
and `.3g2`, case-insensitively. Other suffixes (including partial-transfer
suffixes), empty files, symlinks, and non-regular entries are skipped with
reasons. Size narrows the search; only full SHA-256 and size equality prove
identity. Different filenames, including renamed originals, can match.

Review the managed JSON and text reports under
`~/Library/Application Support/HomeOps`. Exactly one local candidate and one
Pixel path per content group are required for eligibility. Duplicate content
on either side is ambiguous; no filename or inventory order selects a copy to
delete. Unmatched, unreadable, partial, or changed files remain ineligible.

Configured Android media directories are optional as to existence: a positively
proven absent directory is an informational skip. Permission errors,
unavailable storage, failed probes, and incomplete enumeration cannot be
treated as absence. Cleanup fails closed when missing evidence might conceal
an original or duplicate. Only configured shared-storage roots are inspected;
unrelated Pixel inventory never becomes eligible independently of a local file.

### Guided review and detailed reports

After one-time setup, the routine entry point is:

```bash
homeops android cleanup
homeops android cleanup --within received-videos --dry-run
```

The whole destination is the default; `--within` selects an existing relative
subfolder. `--dry-run` always means review only. The command verifies fresh
files, persists a separate plan, validates it read-only, and presents review.
If permanent policy is enabled, an interactive run asks a default-No execution
question and then requires a separate typed acknowledgement such as
`DELETE 3 PIXEL VIDEOS PERMANENTLY`, using the reviewed eligible count.
Approval is bound to that exact plan and current profile. Declining either step,
EOF, cancellation, or a changed plan/profile prevents execution. Policy changes
are never made by a confirmation prompt.

Noninteractive guided runs verify and plan without prompting or deleting,
including when stdin contains `yes`. Use the existing advanced apply flags
for deliberate scripted execution. `--dry-run` never offers execution even in
an interactive terminal. A plan with no eligible sources produces no deletion
prompts. Detailed local authorization records are diagnostic history, not
reusable approval tokens.

Review shows scanned regular Mac files, supported videos, verified matches,
unmatched and ambiguous Mac videos, errors, skipped entries, deletion-eligible
Pixel originals, and estimated eligible bytes. File errors and inventory/root
errors are separate; proven absent optional directories are informational.
Estimated savings sum eligible Pixel file sizes once, not guaranteed Android
filesystem free-space recovery. A verified identity may still be ineligible
because coverage or another planning check failed.

The v2 JSON and text reports retain per-file paths, size/SHA-256 evidence,
reasons, source ambiguities, root coverage, run identity, completeness, and scope.
Plans bind to that run, the exact device, current profile and policy, scope/depth,
and a content fingerprint. Legacy v1 and incomplete reports/plans cannot
authorize deletion: rerun verification and planning. Operational artifacts stay
outside the LocalSend folder. Explicit `--config` keeps reports/plans with that
configuration's local state; advanced output overrides remain available.

### Enable permanent deletion

New setup and unspecified deletion policy default to `delete_mode = "disabled"`.
Guided cleanup explains a blocking policy and prints the effective local config
location and selected profile. It never enables policy itself.

To opt in intentionally on each Mac:

1. Run verification and a dry-run, and inspect the scope and detailed plan.
2. Understand that this ADB workflow has no supported recoverable trash and
   permanent deletion cannot be undone by HomeOps.
3. Open the effective local TOML config printed by cleanup. In the **existing**
   selected profile table, normally `[backups.pixel]`, add or change
   `delete_mode = "permanent"`. Preserve destination, roots, recursion, and
   other settings; keep `[defaults]` `dry_run = true`. Do not paste another
   profile table or rerun setup to overwrite the current config.
4. Run cleanup again for a fresh plan. Enabling policy never approves a run:
   review, explicit execution confirmation, separate permanent acknowledgement,
   and both-copy revalidation remain required when execution is available.
5. To revoke deletion capability, set that profile back to
   `delete_mode = "disabled"`. Another Mac needs its own intentional setup.

The existing `setup --delete-mode permanent` option remains available for
intentional initial setup. Editing the selected local profile is the way to
enable an already configured installation without replacing its other settings.

### Advanced planning and application

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

Use this only after reviewing the plan and dry-run. Apply records a durable
per-file intent before each request, revalidates the reviewed plan and current
configuration, and confirms the exact source is absent before recording success.
Both copies are rehashed after approval; device identity and source metadata
are checked again at the action boundary. A changed file or policy stops further
actions. Mac media remains untouched.

Per-plan result files include the plan ID in their names; dry-run results are
separate from deletion journals. A newly generated plan never overwrites an
unresolved prior deletion journal. The command identifies retained uncertain
journals for troubleshooting. Unknown outcomes or interrupted intents require
reconciliation or a fresh completed verification and plan with new approval;
they are never retried automatically. A confirmed prior deletion is skipped
only after proving absence on the same device. A reappearing path requires fresh
verification and approval. There is no automatic rollback or promise of trash
recovery. Concurrent apply attempts for the same device and state directory
are refused.

Disconnects, changed files,
uncertain deletion outcomes, and incomplete results fail closed; advanced
`--config`, `--output`, `--report`, and `--plan` options remain available for
scripting and troubleshooting.

Reports and plans are local operational data and must not be committed.

The Android compatibility target is `adb devices` and a non-PTY `adb shell -T`
running fixed `sh` programs with `realpath`, `find -print0`, `stat -c`, and
`sha256sum`. Paths travel over stdin as data, never as interpolated shell source.
Inventory uses NUL framing; whitespace and shell metacharacters in names stay
literal. Control-character names, traversal, unsafe aliases/symlinks, malformed
responses, and unavailable commands fail closed with diagnostics. Successful
parent enumeration proves optional-directory absence; a failed existence test
does not. Remote stat identity, size, mode, and fractional modification/change
timestamps (`%y`/`%z` as well as epoch times) bracket SHA-256 reads; unsupported
stat formats fail closed. Local reads check opened file identity and metadata
as well. Tests use synthetic files and mocked ADB;
no physical device is required.

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
