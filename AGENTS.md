# HomeOps contributor guidance

- Treat HomeOps as a modular personal operations monorepo; keep utilities independently testable.
- Keep workstation bootstrap and dependency declarations in `mac-dotfiles`.
- Verification and planning must be read-only with respect to user media.
- Never interpolate device serials or filesystem paths into shell commands.
- Never commit media metadata, local paths, credentials, reports, cleanup plans, or device identifiers.
- OpenSpec documents are private local artifacts. Only approved `.gitkeep` placeholders may be tracked under `openspec/`.
- `CHARTER.md` is also a local-only migration artifact; do not stage, commit, or publish it.
