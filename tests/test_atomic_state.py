import json

import pytest

from homeops.state import atomic_json, check_artifacts, paths_for_config


def test_state_stays_with_explicit_machine_config(tmp_path):
    for name in ("mini", "laptop"):
        config = tmp_path / name / "config.toml"
        assert paths_for_config(config).cleanup_plan.parent == config.parent


def test_operational_writes_cannot_target_any_part_of_media_root(tmp_path):
    media = tmp_path / "media"
    (media / "batch").mkdir(parents=True)
    with pytest.raises(ValueError, match="outside"):
        check_artifacts([media / "outside-selected-batch.json"], media)
    alias = tmp_path / "alias"
    alias.symlink_to(media, target_is_directory=True)
    with pytest.raises(ValueError):
        check_artifacts([alias / "report.json"], media)
    with pytest.raises(ValueError, match="distinct"):
        check_artifacts([tmp_path / "same", tmp_path / "same"], media)


def test_failed_atomic_replace_keeps_prior_evidence(tmp_path, monkeypatch):
    output = tmp_path / "state" / "report.json"
    atomic_json(output, {"complete": True, "run_id": "old"})

    def fail(*args):
        raise OSError("simulated write failure")

    monkeypatch.setattr("homeops.state.os.replace", fail)
    with pytest.raises(OSError):
        atomic_json(output, {"complete": False, "run_id": "new"})
    assert json.loads(output.read_text())["run_id"] == "old"
    assert list(output.parent.iterdir()) == [output]
