import copy
import json

import pytest
from test_verification import FakeAdb

from homeops.config import Profile
from homeops.reports import save_verification, validate_verification
from homeops.verification import verify_media


def test_complete_v2_report_and_checkpoint_provenance(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "renamed.mp4").write_bytes(b"video")
    (media / "missing.mp4").write_bytes(b"different size")
    checkpoints = []
    report = verify_media(
        Profile("pixel", media),
        FakeAdb(),
        checkpoint=lambda data: checkpoints.append(copy.deepcopy(data)),
    )
    output = tmp_path / "state" / "report.json"
    save_verification(report, output)
    payload = json.loads(output.read_text())
    assert payload["schema"] == "homeops.verification.v2"
    assert payload["complete"]
    assert payload["run_id"] == checkpoints[0]["run_id"]
    assert not checkpoints[0]["complete"] and checkpoints[-1]["complete"]
    assert {entry["status"] for entry in payload["files"]} == {"verified", "unmatched"}
    text = output.with_suffix(".txt").read_text()
    assert "missing.mp4" in text and "absent-optional" in text
    assert "SHA-256=" in text


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.update(complete=False),
        lambda data: data.update(schema="homeops.verification.v1"),
        lambda data: data.pop("scope"),
        lambda data: data.pop("coverage_complete"),
    ],
)
def test_incomplete_legacy_or_missing_evidence_is_rejected(tmp_path, mutate):
    (tmp_path / "clip.mp4").write_bytes(b"video")
    report = verify_media(Profile("pixel", tmp_path), FakeAdb())
    mutate(report)
    with pytest.raises(ValueError):
        validate_verification(report)
