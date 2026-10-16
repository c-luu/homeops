from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path


def write_report(
    results: Iterable[dict[str, object]],
    output: Path,
    *,
    device: str | None = None,
    destination_root: Path | None = None,
) -> None:
    entries = list(results)
    payload = {
        "schema": "homeops.verification.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "device": device or "[redacted]",
        "destination_root": str(destination_root.resolve()) if destination_root else None,
        "summary": {
            "total": len(entries),
            "verified": sum(entry["status"] == "verified" for entry in entries),
            "ambiguous": sum(entry["status"] == "ambiguous" for entry in entries),
            "unmatched": sum(entry["status"] == "unmatched" for entry in entries),
        },
        "files": entries,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_human_report(results: Iterable[dict[str, object]], output: Path) -> None:
    lines = ["HomeOps verification report", "==========================="]
    for entry in results:
        source = entry["source"]
        lines.append(f"{entry['status']:>10}  {source['path']}")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_checkpoint(results: Iterable[dict[str, object]], output: Path, *, complete: bool = False) -> None:
    """Persist resumable per-file results without treating incomplete work as success."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "schema": "homeops.verification.checkpoint.v1",
                "complete": complete,
                "files": list(results),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
