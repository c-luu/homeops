import copy

import pytest
from test_verification import FakeAdb

from homeops.cleanup import CleanupError, create_plan, fingerprint, validate_plan
from homeops.config import Profile
from homeops.verification import verify_media


def evidence(tmp_path, within=None):
    media = tmp_path / "media"
    media.mkdir()
    (media / "batch").mkdir()
    (media / "batch" / "clip.mp4").write_bytes(b"video")
    profile = Profile("pixel", media, roots=("DCIM", "Movies"))
    report = verify_media(profile, FakeAdb(), within=within)
    return profile, report


def test_unique_plan_binds_scope_run_and_optional_absence(tmp_path):
    profile, report = evidence(tmp_path, "batch")
    plan = create_plan(report, destination_root=profile.destination, profile=profile)
    assert len(plan["files"]) == 1 and plan["eligible_bytes"] == 5
    assert plan["verification_run_id"] == report["run_id"]
    assert plan["scope"]["within"] == "batch"
    assert validate_plan(plan, profile)


@pytest.mark.parametrize(
    "change",
    [
        lambda data: data.update(complete=False),
        lambda data: data.update(schema="homeops.verification.v1"),
        lambda data: data["files"][0].update(remote_count=2),
        lambda data: data["files"].append(copy.deepcopy(data["files"][0])),
    ],
)
def test_invalid_report_cannot_authorize_plan(tmp_path, change):
    profile, report = evidence(tmp_path)
    change(report)
    with pytest.raises(CleanupError):
        create_plan(report, destination_root=profile.destination, profile=profile)


def test_incomplete_coverage_produces_no_actions(tmp_path):
    profile, report = evidence(tmp_path)
    report["coverage_complete"] = False
    plan = create_plan(report, destination_root=profile.destination, profile=profile)
    assert not plan["files"] and plan["eligible_bytes"] == 0
    assert plan["blocked_reason"]


def test_changed_local_copy_is_excluded(tmp_path):
    profile, report = evidence(tmp_path)
    (profile.destination / "batch" / "clip.mp4").write_bytes(b"other")
    plan = create_plan(report, destination_root=profile.destination, profile=profile)
    assert not plan["files"] and plan["exclusions"]


@pytest.mark.parametrize(
    "change",
    [
        lambda data: data["files"].append(copy.deepcopy(data["files"][0])),
        lambda data: data.update(source_roots=["Download"]),
        lambda data: data.update(destination_root="/tmp"),
        lambda data: data.update(delete_mode="permanent"),
        lambda data: data["files"][0].update(source="/sdcard/Download/clip"),
        lambda data: data.update(verification_run_id=""),
        lambda data: data.update(eligible_bytes=999),
    ],
)
def test_invalid_current_context_or_actions_fail_before_execution(tmp_path, change):
    profile, report = evidence(tmp_path, "batch")
    plan = create_plan(report, destination_root=profile.destination, profile=profile)
    change(plan)
    plan["fingerprint"] = fingerprint(plan)
    with pytest.raises(CleanupError):
        validate_plan(plan, profile)


def test_contained_file_outside_reviewed_subtree_is_rejected(tmp_path):
    profile, report = evidence(tmp_path, "batch")
    plan = create_plan(report, destination_root=profile.destination, profile=profile)
    outside = profile.destination / "outside.mp4"
    outside.write_bytes(b"video")
    plan["files"][0]["destination"] = str(outside)
    plan["fingerprint"] = fingerprint(plan)
    with pytest.raises(CleanupError, match="scope"):
        validate_plan(plan, profile)


def test_symlink_substitution_invalidates_plan(tmp_path):
    profile, report = evidence(tmp_path, "batch")
    plan = create_plan(report, destination_root=profile.destination, profile=profile)
    clip = profile.destination / "batch" / "clip.mp4"
    clip.unlink()
    clip.symlink_to(profile.destination / "absent")
    with pytest.raises(CleanupError):
        validate_plan(plan, profile)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda report: report["files"][0].update(local=None),
        lambda report: report["files"][0].update(remote_count=True),
        lambda report: report.update(coverage=["not an object"]),
        lambda report: report["coverage"].append(dict(report["coverage"][0])),
        lambda report: report["coverage"][0].update(status="absent-optional"),
        lambda report: report["scope"].update(recursive=1),
    ],
)
def test_malformed_or_contradictory_evidence_is_actionably_rejected(tmp_path, mutate):
    profile, report = evidence(tmp_path)
    mutate(report)
    with pytest.raises(CleanupError):
        create_plan(report, destination_root=profile.destination, profile=profile)
