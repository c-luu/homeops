from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from .cleanup import CleanupError, persist_result, revalidate_entry


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
    if not dry_run and not confirm:
        raise CleanupError("explicit confirmation is required")
    if not dry_run and not permanent_acknowledged:
        raise CleanupError("permanent deletion requires separate acknowledgement")
    completed: set[tuple[str, str]] = set()
    prior_results: list[dict[str, object]] = []
    if result_path.exists():
        try:
            prior = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CleanupError(f"invalid existing cleanup result: {result_path}") from exc
        prior_results = prior.get("files", [])
        if not prior.get("complete", False) and any(
            isinstance(item, dict) and item.get("status") in {"failed", "unknown"}
            for item in prior_results
        ):
            raise CleanupError("cleanup result contains an uncertain outcome; create a fresh plan")
        completed = {
            (str(item.get("source")), str(item.get("destination")))
            for item in prior_results
            if isinstance(item, dict) and item.get("status") in {"deleted", "already-deleted"}
        }
    root = Path(str(plan.get("destination_root", ""))).resolve()
    source_roots = tuple(str(root_name).strip("/") for root_name in plan.get("source_roots", []))
    results: list[dict[str, object]] = []
    for raw in plan.get("files", []):
        if isinstance(raw, dict) and (
            str(raw.get("source")), str(raw.get("destination"))
        ) in completed:
            results.append(
                {
                    "source": str(raw.get("source")),
                    "destination": str(raw.get("destination")),
                    "status": "already-deleted",
                }
            )
            continue
        entry = revalidate_entry(
            raw,
            root=root,
            source_roots=source_roots,
            source_check=source_check,
        )
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
