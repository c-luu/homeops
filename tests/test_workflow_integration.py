import json
from hashlib import sha256

import pytest
from test_execution_service import configured, run
from test_verification import MemoryAdb
from typer.testing import CliRunner

from homeops.adb import AdbError
from homeops.cli import app
from homeops.config import write_config


def hashes(root):
    return {
        str(path.relative_to(root)): sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("machine", ["mini", "laptop"])
@pytest.mark.parametrize("advanced", [False, True])
@pytest.mark.parametrize(
    "within,recursive,eligible", [(None, True, 3), ("batch", True, 2), ("batch", False, 1)]
)
def test_manual_arrivals_to_reviewed_pixel_only_cleanup(
    tmp_path,
    monkeypatch,
    machine,
    advanced,
    within,
    recursive,
    eligible,
):
    media = tmp_path / machine / "received"
    (media / "batch" / "nested").mkdir(parents=True)
    local = {
        "old.mp4": b"old-video",
        "batch/unique.mp4": b"unique",
        "batch/duplicate.mp4": b"duplicate",
        "batch/partial.mp4": b"par",
        "batch/missing.mp4": b"not-transferred",
        "batch/nested/deep.mp4": b"deep-data",
        "batch/note.txt": b"skip",
    }
    for name, content in local.items():
        (media / name).write_bytes(content)
    contents = {
        "/sdcard/DCIM/old": b"old-video",
        "/sdcard/DCIM/original": b"unique",
        "/sdcard/DCIM/duplicate": b"duplicate",
        "/sdcard/Movies/other-duplicate": b"duplicate",
        "/sdcard/DCIM/full": b"partial-original",
        "/sdcard/DCIM/deep": b"deep-data",
        "/sdcard/Movies/unrelated": b"unrelated-video",
    }

    class FixtureAdb(MemoryAdb):
        def inventory_with_coverage(self, serial, roots):
            inventory = super().inventory_with_coverage(serial, roots)
            inventory.coverage[-1]["status"] = "absent-optional"
            return inventory

    adb = FixtureAdb(dict(contents))
    config = tmp_path / machine / "state" / "config.toml"
    write_config(
        config,
        destination=media,
        roots=("DCIM", "Movies", "Pictures"),
        delete_mode="permanent",
        recursive=recursive,
    )
    before = hashes(media)
    monkeypatch.setattr("homeops.cli.Adb", lambda: adb)
    monkeypatch.setattr("homeops.cli.is_interactive", lambda: True)
    runner = CliRunner()
    scope_args = ["--within", within] if within else []
    if advanced:
        commands = [
            ["android", "backup", "verify", *scope_args],
            ["android", "cleanup", "plan"],
            ["android", "cleanup", "apply"],
            [
                "android",
                "cleanup",
                "apply",
                "--execute",
                "--confirm",
                "--permanent",
                "--acknowledge-permanent",
            ],
        ]
        for command in commands:
            result = runner.invoke(app, command + ["--config", str(config)])
            assert result.exit_code == 0, result.output
    else:
        result = runner.invoke(
            app,
            ["android", "cleanup", "--config", str(config), *scope_args],
            input=f"y\nDELETE {eligible} PIXEL VIDEOS PERMANENTLY\n",
        )
        assert result.exit_code == 0, result.output
        assert f"Deletion-eligible Pixel originals: {eligible}" in result.output
        assert "ambiguous Mac videos: 1" in result.output
        assert "absent optional Android roots: 1" in result.output
        assert ("subdirectory" if within else "entire configured") in result.output
    plan = json.loads((config.parent / "cleanup-plan.json").read_text())
    assert len(plan["files"]) == eligible
    assert plan["eligible_bytes"] == sum(len(contents[path]) for path in adb.deleted)
    assert set(adb.deleted) == {entry["source"] for entry in plan["files"]}
    assert {
        "/sdcard/DCIM/duplicate",
        "/sdcard/Movies/other-duplicate",
        "/sdcard/DCIM/full",
        "/sdcard/Movies/unrelated",
    } <= set(adb.data)
    assert hashes(media) == before


def test_new_verified_plan_retains_and_surfaces_unknown_prior_journal(tmp_path, monkeypatch):
    profile, _, plan_path, config, adb = configured(tmp_path)
    before = hashes(profile.destination)
    delete = adb.delete_file

    def lost_response(serial, path, **kwargs):
        delete(serial, path, **kwargs)
        raise AdbError("lost response after request")

    adb.delete_file = lost_response
    results, prior_path = run(plan_path, config, adb)
    assert results[-1]["status"] == "unknown"
    prior_bytes = prior_path.read_bytes()
    adb.delete_file = delete
    monkeypatch.setattr("homeops.cli.Adb", lambda: adb)
    monkeypatch.setattr("homeops.cli.is_interactive", lambda: False)
    result = CliRunner().invoke(app, ["android", "cleanup", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert "Prior unresolved deletion journal retained" in result.output
    assert "Deletion-eligible Pixel originals: 1" in result.output
    assert len(adb.deleted) == 1
    _, next_path = run(plan_path, config, adb)
    assert next_path != prior_path and prior_path.read_bytes() == prior_bytes
    assert hashes(profile.destination) == before


def test_interrupted_verification_never_applies_prior_plan(tmp_path, monkeypatch):
    _, _, _, config, adb = configured(tmp_path)
    old_plan = (config.parent / "cleanup-plan.json").read_bytes()
    monkeypatch.setattr("homeops.cli.Adb", lambda: adb)

    def interrupt(*args):
        raise KeyboardInterrupt

    adb.hash_file = interrupt
    result = CliRunner().invoke(app, ["android", "cleanup", "--config", str(config), "--dry-run"])
    assert result.exit_code != 0
    checkpoint = json.loads((config.parent / "verification-checkpoint.json").read_text())
    assert not checkpoint["complete"]
    assert (config.parent / "cleanup-plan.json").read_bytes() == old_plan
    assert not adb.deleted
