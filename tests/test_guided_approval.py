import json

import pytest
from test_execution_service import configured
from typer.testing import CliRunner

from homeops.cleanup import fingerprint
from homeops.cli import app
from homeops.state import atomic_json


def invoke(config, text="", extra=()):
    return CliRunner().invoke(
        app, ["android", "cleanup", "--config", str(config), *extra], input=text
    )


def test_guided_approval_executes_only_reviewed_pixel_paths(tmp_path, monkeypatch):
    profile, _, _, config, adb = configured(tmp_path)
    before = {path: path.read_bytes() for path in profile.destination.iterdir()}
    monkeypatch.setattr("homeops.cli.Adb", lambda: adb)
    monkeypatch.setattr("homeops.cli.is_interactive", lambda: True)
    result = invoke(config, "y\nDELETE 2 PIXEL VIDEOS PERMANENTLY\n")
    assert result.exit_code == 0, result.output
    assert "Execute this reviewed Pixel cleanup plan?" in result.output
    assert "Irreversible" in result.output
    assert len(adb.deleted) == 2
    assert {path: path.read_bytes() for path in profile.destination.iterdir()} == before
    authorization = json.loads(next(config.parent.glob("*.authorization.json")).read_text())
    assert authorization["execution_confirmed"] and authorization["permanent_acknowledged"]
    assert authorization["state"] == "approved"


@pytest.mark.parametrize(
    "answers,state",
    [
        ("\n", "declined"),
        ("n\n", "declined"),
        ("y\nwrong phrase\n", "declined"),
        ("", "cancelled"),
        ("y\n", "cancelled"),
    ],
)
def test_decline_eof_and_incomplete_ack_never_delete(tmp_path, monkeypatch, answers, state):
    _, _, _, config, adb = configured(tmp_path)
    monkeypatch.setattr("homeops.cli.Adb", lambda: adb)
    monkeypatch.setattr("homeops.cli.is_interactive", lambda: True)
    result = invoke(config, answers)
    assert result.exit_code == 0, result.output
    assert not adb.deleted
    record = json.loads(next(config.parent.glob("*.authorization.json")).read_text())
    assert record["state"] == state


@pytest.mark.parametrize("interactive,extra", [(False, ()), (True, ("--dry-run",))])
def test_no_prompt_for_noninteractive_or_explicit_dryrun(tmp_path, monkeypatch, interactive, extra):
    _, _, _, config, adb = configured(tmp_path)
    monkeypatch.setattr("homeops.cli.Adb", lambda: adb)
    monkeypatch.setattr("homeops.cli.is_interactive", lambda: interactive)
    monkeypatch.setattr(
        "homeops.cli.typer.confirm", lambda *args, **kw: pytest.fail("must not prompt")
    )
    result = invoke(config, extra=extra)
    assert result.exit_code == 0, result.output
    assert not adb.deleted
    assert not list(config.parent.glob("*.authorization.json"))


@pytest.mark.parametrize("mode", ["disabled", "trash"])
def test_policy_blocks_prompts_and_never_changes_itself(tmp_path, monkeypatch, mode):
    _, _, _, config, adb = configured(tmp_path)
    config.write_text(config.read_text().replace('"permanent"', f'"{mode}"'))
    before = config.read_bytes()
    monkeypatch.setattr("homeops.cli.Adb", lambda: adb)
    monkeypatch.setattr("homeops.cli.is_interactive", lambda: True)
    monkeypatch.setattr(
        "homeops.cli.typer.confirm", lambda *args, **kw: pytest.fail("policy blocks")
    )
    result = invoke(config)
    assert result.exit_code == 0, result.output
    assert "Execution unavailable" in result.output and not adb.deleted
    assert before == config.read_bytes()


@pytest.mark.parametrize("change", ["plan", "profile"])
def test_mutation_during_approval_cannot_execute(tmp_path, monkeypatch, change):
    _, _, plan_path, config, adb = configured(tmp_path)
    monkeypatch.setattr("homeops.cli.Adb", lambda: adb)
    monkeypatch.setattr("homeops.cli.is_interactive", lambda: True)
    monkeypatch.setattr("homeops.cli.typer.confirm", lambda *args, **kw: True)

    def acknowledgement(*args, **kwargs):
        if change == "plan":
            plan = json.loads(plan_path.read_text())
            plan["eligible_bytes"] += 1
            plan["fingerprint"] = fingerprint(plan)
            atomic_json(plan_path, plan)
        else:
            config.write_text(config.read_text().replace('"permanent"', '"disabled"'))
        return "DELETE 2 PIXEL VIDEOS PERMANENTLY"

    monkeypatch.setattr("homeops.cli.typer.prompt", acknowledgement)
    result = invoke(config)
    assert result.exit_code != 0 and not adb.deleted
    record = json.loads(next(config.parent.glob("*.authorization.json")).read_text())
    assert record["state"] == "blocked"


def test_keyboard_interrupt_during_approval_is_recorded_without_deletion(tmp_path, monkeypatch):
    _, _, _, config, adb = configured(tmp_path)
    monkeypatch.setattr("homeops.cli.Adb", lambda: adb)
    monkeypatch.setattr("homeops.cli.is_interactive", lambda: True)

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("homeops.cli.typer.confirm", interrupted)
    result = invoke(config)
    assert result.exit_code == 0, result.output
    assert not adb.deleted
    assert (
        json.loads(next(config.parent.glob("*.authorization.json")).read_text())["state"]
        == "cancelled"
    )
