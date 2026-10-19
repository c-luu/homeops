import json

import pytest
from test_executor import prepared
from typer.testing import CliRunner

from homeops.adb import AdbError
from homeops.cleanup import CleanupError, fingerprint
from homeops.cli import app
from homeops.config import write_config
from homeops.executor import execute_plan_file, result_path_for, unresolved_journals
from homeops.state import atomic_json


def configured(tmp_path):
    profile, plan, result, adb = prepared(tmp_path)
    config = result.parent / "config.toml"
    write_config(
        config, destination=profile.destination, roots=profile.roots, delete_mode="permanent"
    )
    plan_path = config.parent / "cleanup-plan.json"
    atomic_json(plan_path, plan)
    return profile, plan, plan_path, config, adb


def run(plan_path, config, adb, **kwargs):
    return execute_plan_file(
        plan_path,
        config=config,
        profile_name="pixel",
        adb=adb,
        confirm=True,
        permanent=True,
        permanent_acknowledged=True,
        dry_run=False,
        **kwargs,
    )


def test_advanced_cli_preserves_explicit_flag_execution(tmp_path, monkeypatch):
    profile, _, _plan_path, config, adb = configured(tmp_path)
    before = {path: path.read_bytes() for path in profile.destination.iterdir()}
    monkeypatch.setattr("homeops.cli.Adb", lambda: adb)
    runner = CliRunner()
    dry = runner.invoke(app, ["android", "cleanup", "apply", "--config", str(config)])
    assert dry.exit_code == 0 and not adb.deleted
    executed = runner.invoke(
        app,
        [
            "android",
            "cleanup",
            "apply",
            "--config",
            str(config),
            "--execute",
            "--confirm",
            "--permanent",
            "--acknowledge-permanent",
        ],
    )
    assert executed.exit_code == 0, executed.output
    assert len(adb.deleted) == 2
    assert {path: path.read_bytes() for path in profile.destination.iterdir()} == before
    assert len(list(config.parent.glob("*.result.json"))) == 1
    assert len(list(config.parent.glob("*.dry-run.json"))) == 1


@pytest.mark.parametrize("mutation", ["device", "profile", "plan", "local", "source"])
def test_changes_after_review_cannot_authorize_delete(tmp_path, mutation):
    _profile, plan, plan_path, config, adb = configured(tmp_path)
    original = adb.evidence

    def changed(serial, path):
        answer = original(serial, path)
        if mutation == "device":
            adb.serial = "different-fixture"
        elif mutation == "profile":
            config.write_text(config.read_text().replace('"permanent"', '"disabled"'))
        elif mutation == "plan":
            altered = json.loads(plan_path.read_text())
            altered["eligible_bytes"] += 1
            altered["fingerprint"] = fingerprint(altered)
            atomic_json(plan_path, altered)
        elif mutation == "local":
            target = next(item["destination"] for item in plan["files"] if item["source"] == path)
            from pathlib import Path

            Path(target).write_bytes(b"altered")
        return answer

    adb.evidence = changed
    if mutation == "source":
        adb.data[plan["files"][0]["source"]] = b"other"
    try:
        results, _ = run(plan_path, config, adb)
        assert results[-1]["status"] == "unknown"
    except CleanupError:
        pass
    assert not adb.deleted


def test_post_delete_absence_error_stops_with_unknown_outcome(tmp_path):
    _, plan, plan_path, config, adb = configured(tmp_path)

    def uncertain(*args):
        raise AdbError("disconnect during absence check")

    adb.is_absent = uncertain
    results, journal_path = run(plan_path, config, adb)
    assert len(adb.deleted) == 1
    assert results[-1]["status"] == "unknown"
    assert not json.loads(journal_path.read_text())["complete"]
    assert unresolved_journals(config.parent) == [journal_path]
    with pytest.raises(CleanupError, match="uncertain"):
        run(plan_path, config, adb)
    assert result_path_for(plan_path, plan, dry_run=False) == journal_path


def test_reviewed_fingerprint_cannot_be_replaced_before_execution(tmp_path):
    _, plan, plan_path, config, adb = configured(tmp_path)
    reviewed = plan["fingerprint"]
    plan["plan_id"] = "new-plan"
    plan["fingerprint"] = fingerprint(plan)
    atomic_json(plan_path, plan)
    with pytest.raises(CleanupError, match="changed after review"):
        run(plan_path, config, adb, expected_fingerprint=reviewed)
    assert not adb.deleted


def test_custom_plan_locations_still_share_current_configuration_lock(tmp_path):
    from homeops.executor import operation_lock

    _, plan, _plan_path, config, adb = configured(tmp_path)
    alternate = tmp_path / "alternate" / "plan.json"
    atomic_json(alternate, plan)
    with (
        operation_lock(config.parent / "lock-context", plan["device"]),
        pytest.raises(CleanupError, match="another cleanup"),
    ):
        run(alternate, config, adb)
    assert not adb.deleted
