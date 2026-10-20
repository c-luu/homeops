# Beginner guide: Free up Pixel space after transferring videos with LocalSend

This guide assumes:

- You have already cloned `c-luu/mac-dotfiles` and `c-luu/homeops` on your Apple Silicon Mac mini.
- You already used **LocalSend on your Pixel to send selected videos to your Mac mini**.
- The received videos are still in an **existing folder on the Mac**, and the originals are still on the Pixel.
- You want to **delete only Pixel originals that HomeOps proves have an identical Mac copy**.

**HomeOps does not transfer videos or run LocalSend. It does not delete the Mac copies.** It checks the received files against your connected Pixel, then offers to delete only unambiguous, SHA-256-verified originals.

> **Important:** The Android deletion is permanent; HomeOps does not have a recoverable trash or undo. Before removing irreplaceable videos from your phone, consider keeping a second independent copy of the Mac folder (for example, on an external drive or in a separate backup). Matching a single Mac copy verifies transfer integrity but does not protect against later Mac drive failure.

## 1. Install the HomeOps command (one time per Mac)

Open **Terminal**. Move into your *existing* `mac-dotfiles` checkout. Replace the example path with its actual location:

```bash
cd "/path/to/mac-dotfiles"
```

Install only the optional tools HomeOps needs and then HomeOps itself:

```bash
brew bundle install --no-upgrade --file=Brewfile.homeops
./scripts/install-homeops.sh
```

- `Brewfile.homeops` installs `uv` and Android Platform Tools (`adb`).
- The install script installs the specific, reviewed HomeOps Git revision pinned in `mac-dotfiles/homeops-version`. You do **not** need to activate a Python virtual environment or run anything from the HomeOps source checkout.
- If the script detects a different existing installation, it stops and explains the mismatch. Only after reviewing the change, use `./scripts/install-homeops.sh --upgrade` if you intend to replace it.

### Make the `homeops` command available in zsh

**Do this after the installer, especially on a Mac where you have not installed a `uv` CLI tool before.** `uv` may have installed HomeOps successfully even though Terminal initially says `zsh: command not found: homeops`. That means your shell has not yet been configured to find `uv`'s tool executables; it does **not** necessarily mean the installation failed.

Run:

```bash
uv tool update-shell
```

This updates your shell's configuration so `uv`-installed commands can be found. **Restart Terminal**, or start a fresh login zsh session:

```bash
exec zsh -l
```

Then confirm HomeOps is available:

```bash
homeops --help
```

You normally only need `uv tool update-shell` once per Mac, not every time you run HomeOps. If it is still not found, run `uv tool list` to confirm HomeOps was installed and `uv tool dir --bin` to find the executable directory. Don't reinstall Homebrew just to fix a shell `PATH` issue.

## 2. Connect and authorize the Pixel

Use a USB cable that supports data, connect the Pixel to your Mac mini, and unlock it.

If USB debugging is not enabled, on the Pixel enable **Developer options** (normally under **Settings → About phone → Build number**, tap seven times), then enable **USB debugging** under **Settings → System → Developer options**. Accept the **Allow USB debugging?** prompt for your Mac. Menu labels can vary by Android version.

In Terminal, run:

```bash
homeops doctor
```

**Continue only when you see** an Android shell protocol message saying **`compatible`**. `doctor` checks ADB and the supported Android command behavior without reading or deleting your videos. Do not rely on the command's exit code alone; an unsupported protocol may be printed as a diagnostic.

If it says *unauthorized*, unlock the Pixel and accept the prompt. If there is no device, check the cable, connection, and USB debugging; only one authorized Android device should be attached.

## 3. Tell HomeOps where LocalSend put the videos (one time)

First, open Finder and locate the **real Mac folder containing the videos LocalSend already delivered**. Don't create a new empty folder and don't point HomeOps at your `homeops` Git repository.

For a **first setup only**, run:

```bash
homeops android setup \
  --destination "/actual/path/to/your/LocalSend/folder"
```

Replace the quoted example path with the real folder pathname. In Finder, you can select the folder and use **Option–Command–C** to copy its pathname.

Setup stores a local configuration at:

```text
~/Library/Application Support/HomeOps/config.toml
```

The deletion policy is **disabled by default**, which is what you want for your first test. If HomeOps was configured previously, **do not rerun setup blindly**: inspect the existing configuration instead, because setup rewrites it.

## 4. Preview which Pixel videos can be safely removed — no deletion

Make sure the LocalSend transfers have fully finished and that the Pixel remains connected and unlocked. Run:

```bash
homeops android cleanup --dry-run
```

This may take a while. HomeOps scans the configured Mac folder, inventories the configured Pixel media directories, narrows matches by file size, and compares SHA-256 hashes. It prints a summary and exact matching paths.

Look for:

- **Verified matches** — a Mac file and Pixel original were proven byte-identical.
- **Deletion-eligible Pixel originals** — the subset that passes all safety and uniqueness rules.
- **Unmatched** — a Mac file has no verified Pixel original; nothing will be deleted for it.
- **Ambiguous** — duplicate content on one or both sides; HomeOps excludes these conservatively.
- **Errors/skipped files** — inspect before proceeding; an incomplete Android inventory may block cleanup.
- **Estimated eligible savings** — approximately how much Pixel storage the eligible files occupy.

**Do not expect every video on the Pixel to appear.** HomeOps is intentionally driven by the videos in your Mac's LocalSend folder; unrelated phone videos must remain untouched. And if a Mac video is already absent from the Pixel, it cannot be deleted again.

By default, HomeOps scans **the entire configured LocalSend folder recursively**, including older transfers. If you organized this transfer into an existing child folder such as `received-videos`, you can narrow the Mac-side candidates:

```bash
homeops android cleanup --within received-videos --dry-run
```

The `--within` value must name an **existing subfolder relative to your configured LocalSend folder**; it is not an absolute path and does not automatically detect your most recent transfer batch.

## 5. Read the detailed verification report

The routine command displays a summary, but HomeOps also writes a human-readable report to its local state directory. Open it using:

```bash
open -e "$HOME/Library/Application Support/HomeOps/verification-report.txt"
```

The JSON report and cleanup plan are kept nearby under `~/Library/Application Support/HomeOps/`. These files may contain private video paths and a Pixel device identifier; **do not commit or upload them to your public Git repositories**.

Before you delete anything, make sure the report identifies the correct LocalSend destination and Pixel, verify the listed matching paths, and confirm that there are no unexpected errors or ambiguous candidates. If the eligible count is zero, stop here; HomeOps has nothing it can safely remove.

## 6. Optional: Enable permanent Pixel-only deletion

**Do this only after you are satisfied with the dry-run and have secured the Mac videos.** HomeOps cannot recover deleted Pixel originals.

Open your existing HomeOps configuration:

```bash
open -e "$HOME/Library/Application Support/HomeOps/config.toml"
```

In the **existing** `[backups.pixel]` section, change or add this setting:

```toml
[backups.pixel]
# Keep your existing destination, roots, and recursive settings here.
delete_mode = "permanent"
```

Do not replace the whole config with this fragment, create a second `[backups.pixel]` section, or change your backup destination. Keep `[defaults]` `dry_run = true` unchanged. Save and close the file.

Now run the guided command **without** `--dry-run`:

```bash
homeops android cleanup
```

HomeOps will run **fresh verification**, build a new plan, and display the exact eligible Pixel files and Mac copies. Read the results carefully. If the configured deletion policy is `permanent`, it will:

1. Ask whether to execute the reviewed plan; the default is **No**.
2. Require you to type an exact acknowledgement, for example `DELETE 3 PIXEL VIDEOS PERMANENTLY` (the count will reflect your actual plan).
3. Recheck the files and device before each deletion and record the outcome.

You can answer **No**, press **Ctrl+C**, or close the prompt to avoid deletion. Only explicitly approved, verified Pixel originals are deletion targets. **Mac videos are not deleted.**

For your first run, it's reasonable to leave the deletion policy disabled and bring the dry-run summary here for review before enabling anything.

## 7. Check the result

Read HomeOps' final summary and any result/journal path it prints. If it reports a disconnect, unknown outcome, or an incomplete operation, **do not blindly rerun deletion**; inspect the retained journal and resolve the uncertainty first.

Check the Pixel's storage and the Mac's received videos separately. Do not use the absence of an original as proof that your Mac has a second backup.

Once finished, you may disable **USB debugging** on your Pixel if you do not need it until the next cleanup session. You can also return `delete_mode = "disabled"` in the local HomeOps config to prevent execution until you deliberately opt in again.

## Quick reference for future LocalSend sessions

After manually sending more videos with LocalSend and confirming they arrived, reconnect and unlock the Pixel:

```bash
homeops doctor                         # Check the Android connection
homeops android cleanup --dry-run     # Preview; cannot delete videos
homeops android cleanup               # Review; offers deletion only if enabled
```

**Troubleshooting:** If `homeops doctor` does not say *compatible*, don't proceed. If zero videos are eligible, check the configured LocalSend destination and the full report; don't weaken hash or duplicate checks to force matches. If zsh says `command not found: homeops`, run `uv tool update-shell`, restart Terminal (or `exec zsh -l`), then retry `homeops --help`. If it remains missing, check `uv tool list` and `uv tool dir --bin`. If a different HomeOps revision is reported, review the pinned version in `mac-dotfiles/homeops-version` and deliberately run the installer with `--upgrade` only if appropriate.

**Project sources:** [mac-dotfiles](https://github.com/c-luu/mac-dotfiles) · [HomeOps](https://github.com/c-luu/homeops)
