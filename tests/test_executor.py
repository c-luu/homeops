import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_verification import MemoryAdb

from homeops.cleanup import CleanupError, create_plan, fingerprint
from homeops.config import Profile
from homeops.executor import apply_plan, operation_lock
from homeops.verification import verify_media


def prepared(tmp_path):
    media = tmp_path / "received"
    media.mkdir()
    adb = MemoryAdb()
    for path, data in adb.data.items():
        (media / Path(path).name).write_bytes(data)
    profile = Profile("pixel", media, roots=("DCIM", "Movies"), delete_mode="permanent")
    plan = create_plan(verify_media(profile, adb), destination_root=media, profile=profile)
    return profile, plan, tmp_path / "state" / "result.json", adb


def execute(profile, plan, result, adb, **overrides):
    options = {
        "profile": profile,
        "source_check": lambda path, size, digest: (
            adb.evidence(plan["device"], path)[:2] == (size, digest)
        ),
        "delete_source": lambda path: adb.delete_file(
            plan["device"], path, expected_metadata=adb.evidence(plan["device"], path)[2]
        ),
        "source_absent": lambda path: adb.is_absent(plan["device"], path),
        "confirm": True,
        "permanent": True,
        "permanent_acknowledged": True,
        "dry_run": False,
    }
    options.update(overrides)
    return apply_plan(plan, result, **options)


def test_dry_run_needs_no_approvals_and_never_deletes(tmp_path):
    profile, plan, result, adb = prepared(tmp_path)
    results = execute(
        profile,
        plan,
        result,
        adb,
        dry_run=True,
        confirm=False,
        permanent=False,
        permanent_acknowledged=False,
    )
    assert [item["status"] for item in results] == ["dry-run", "dry-run"]
    assert not adb.deleted


@pytest.mark.parametrize(
    "override",
    [
        {"confirm": False},
        {"permanent": False},
        {"permanent_acknowledged": False},
        {"source_absent": None},
    ],
)
def test_each_execution_gate_is_required(tmp_path, override):
    profile, plan, result, adb = prepared(tmp_path)
    with pytest.raises(CleanupError):
        execute(profile, plan, result, adb, **override)
    assert not adb.deleted and not result.exists()


@pytest.mark.parametrize("mode", ["disabled", "trash"])
def test_policy_cannot_be_bypassed(tmp_path, mode):
    profile, plan, result, adb = prepared(tmp_path)
    profile = replace(profile, delete_mode=mode)
    plan["delete_mode"] = mode
    plan["fingerprint"] = fingerprint(plan)
    with pytest.raises(CleanupError, match="policy"):
        execute(profile, plan, result, adb)
    assert not adb.deleted


def test_intent_is_durable_before_every_deletion(tmp_path):
    profile, plan, result, adb = prepared(tmp_path)
    before = {p: p.read_bytes() for p in profile.destination.iterdir()}

    def delete(path):
        journal = json.loads(result.read_text())
        assert not journal["complete"]
        assert journal["files"][-1]["source"] == path
        assert journal["files"][-1]["status"] == "intent"
        adb.delete_file(
            plan["device"], path, expected_metadata=adb.evidence(plan["device"], path)[2]
        )

    results = execute(profile, plan, result, adb, delete_source=delete)
    assert all(item["status"] == "deleted" for item in results)
    assert json.loads(result.read_text())["complete"]
    assert {p: p.read_bytes() for p in profile.destination.iterdir()} == before


def test_later_revalidation_failure_keeps_prior_progress(tmp_path):
    profile, plan, result, adb = prepared(tmp_path)
    first, second = [item["source"] for item in plan["files"]]

    def check(path, *_):
        return path == first

    with pytest.raises(CleanupError, match="revalidation"):
        execute(profile, plan, result, adb, source_check=check)
    journal = json.loads(result.read_text())
    assert not journal["complete"]
    assert [item["status"] for item in journal["files"]] == ["deleted", "failed"]
    assert adb.deleted == [first] and second in adb.data


@pytest.mark.parametrize("interrupt", [RuntimeError("disconnect"), KeyboardInterrupt()])
def test_unknown_outcome_never_retries_silently(tmp_path, interrupt):
    profile, plan, result, adb = prepared(tmp_path)

    def stop(path):
        raise interrupt

    if isinstance(interrupt, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            execute(profile, plan, result, adb, delete_source=stop)
    else:
        assert execute(profile, plan, result, adb, delete_source=stop)[-1]["status"] == "unknown"
    journal = json.loads(result.read_text())
    assert not journal["complete"] and journal["files"][-1]["status"] == "unknown"
    with pytest.raises(CleanupError, match="uncertain"):
        execute(profile, plan, result, adb)
    assert not adb.deleted


def test_failed_intent_write_prevents_delete(tmp_path, monkeypatch):
    profile, plan, result, adb = prepared(tmp_path)
    monkeypatch.setattr(
        "homeops.executor.atomic_json",
        lambda *_: (_ for _ in ()).throw(OSError("simulated full disk")),
    )
    with pytest.raises(CleanupError, match="persistence"):
        execute(profile, plan, result, adb)
    assert not adb.deleted


def test_lost_outcome_write_leaves_unknown_intent_and_stops(tmp_path, monkeypatch):
    from homeops.state import atomic_json

    profile, plan, result, adb = prepared(tmp_path)
    writes = []

    def write(path, payload):
        writes.append(1)
        if len(writes) > 1:
            raise OSError("lost outcome persistence")
        atomic_json(path, payload)

    monkeypatch.setattr("homeops.executor.atomic_json", write)
    with pytest.raises(CleanupError, match="persistence"):
        execute(profile, plan, result, adb)
    journal = json.loads(result.read_text())
    assert journal["files"][-1]["status"] == "intent"
    assert len(adb.deleted) == 1 and not journal["complete"]


def test_resume_proves_absence_and_reappearing_source_requires_fresh_plan(tmp_path):
    profile, plan, result, adb = prepared(tmp_path)
    execute(profile, plan, result, adb)
    assert all(item["status"] == "already-deleted" for item in execute(profile, plan, result, adb))
    first = plan["files"][0]["source"]
    adb.data[first] = b"video"
    with pytest.raises(CleanupError, match="reappeared"):
        execute(profile, plan, result, adb)
    assert len(adb.deleted) == 2


def test_foreign_result_cannot_skip_or_repeat_actions(tmp_path):
    profile, plan, result, adb = prepared(tmp_path)
    execute(profile, plan, result, adb)
    plan["plan_id"] = "different"
    plan["fingerprint"] = fingerprint(plan)
    with pytest.raises(CleanupError, match="another plan"):
        execute(profile, plan, result, adb)
    assert len(adb.deleted) == 2


def test_lock_prevents_concurrent_execution_even_with_different_result_names(tmp_path):
    profile, plan, result, adb = prepared(tmp_path)
    with (
        operation_lock(result, plan["device"]),
        pytest.raises(CleanupError, match="another cleanup"),
    ):
        execute(profile, plan, result.with_name("another.json"), adb)
    assert not adb.deleted
