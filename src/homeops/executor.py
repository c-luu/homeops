from __future__ import annotations

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
    results: list[dict[str, object]] = []
    for entry in entries:
        result: dict[str, object] = {"source": entry.source, "destination": entry.destination}
        if dry_run:
            result["status"] = "dry-run"
        else:
            try:
                delete_source(entry.source)
            except (OSError, RuntimeError, CleanupError) as exc:
                result["status"] = "failed"
                result["error"] = str(exc)
                results.append(result)
                persist_result(result_path, results, complete=False)
                continue
            result["status"] = "deleted"
        results.append(result)
        persist_result(result_path, results, complete=False)
    persist_result(result_path, results, complete=True)
    return results
