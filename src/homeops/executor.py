from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from .cleanup import CleanupError, persist_result, revalidate


def apply_plan(
    plan: dict[str, object],
    result_path: Path,
    *,
    source_check: Callable[[str, int, str], bool],
    delete_source: Callable[[str], None],
    confirm: bool,
    permanent_acknowledged: bool,
    dry_run: bool = True,
) -> list[dict[str, object]]:
    if not confirm:
        raise CleanupError("explicit confirmation is required")
    if not dry_run and not permanent_acknowledged:
        raise CleanupError("permanent deletion requires separate acknowledgement")
    entries = revalidate(plan, source_check=source_check)
    completed: set[tuple[str, str]] = set()
    if result_path.exists():
        try:
            prior = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CleanupError(f"invalid existing cleanup result: {result_path}") from exc
        if not prior.get("complete", False):
            prior_files = prior.get("files", [])
            if any(
                isinstance(item, dict) and item.get("status") in {"failed", "unknown"}
                for item in prior_files
            ):
                raise CleanupError("cleanup result contains an uncertain outcome; create a fresh plan")
        completed = {
            (str(item.get("source")), str(item.get("destination")))
            for item in prior.get("files", [])
            if isinstance(item, dict) and item.get("status") == "deleted"
        }
    results: list[dict[str, object]] = []
    for entry in entries:
        if (entry.source, entry.destination) in completed:
            results.append(
                {"source": entry.source, "destination": entry.destination, "status": "already-deleted"}
            )
            continue
        result: dict[str, object] = {"source": entry.source, "destination": entry.destination}
        if dry_run:
            result["status"] = "dry-run"
        else:
            try:
                delete_source(entry.source)
            except (OSError, RuntimeError, CleanupError) as exc:
                result["status"] = "unknown"
                result["error"] = str(exc)
                results.append(result)
                persist_result(result_path, results, complete=False)
                return results
            result["status"] = "deleted"
        results.append(result)
        persist_result(result_path, results, complete=False)
    persist_result(result_path, results, complete=True)
    return results
