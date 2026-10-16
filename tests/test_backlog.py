from pathlib import Path


def test_openspec_bundle_archival_is_documented_as_future_only() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "Future local OpenSpec Git-bundle archival" in readme
    assert "explicit user invocation" in readme
    assert "no remote" in readme
    assert "bundle integrity" in readme
    assert "safe, repeatable" in readme
    assert "CHARTER.md" in readme
    assert "not bundle `.venv`" in readme
    assert "hash-pinned dependency export" in readme


def test_routine_workflow_and_portable_workspace_are_documented() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "homeops android setup" in readme
    assert "homeops android backup verify" in readme
    assert "homeops android cleanup plan" in readme
    assert "homeops android cleanup apply" in readme
    assert "../mac-dotfiles" in readme
    assert "/Users/" not in readme
