"""The data directory must survive restarts (D-68): a marker file shows whether this start found the previous one's."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("tripwright.volume")
MARKER = ".tripwright-volume"


def check_volume(data_dir: str | Path) -> dict:
    """Read and update the marker in `data_dir`; log one line saying what this start found. Returns {state, starts,
    created_at}: state is `kept` (the marker was there), `new` (an empty new directory: fine on a first start, a lost
    volume otherwise) or `unmarked` (files but no marker: written by an older version, or the marker was removed)."""
    folder = Path(data_dir)
    marker = folder / MARKER
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
        state = "kept"
    except FileNotFoundError:
        state = "unmarked" if folder.is_dir() and any(folder.iterdir()) else "new"
        record = dict(created_at=stamp, starts=0)
    except (OSError, ValueError) as error:
        log.warning("data directory %s: cannot read %s (%s)", folder, MARKER, error)
        return dict(state="unreadable", starts=None, created_at=None)
    record.update(starts=int(record.get("starts", 0)) + 1, last_start=stamp)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        scratch = marker.with_name(MARKER + ".tmp")
        scratch.write_text(json.dumps(record), encoding="utf-8")
        os.replace(scratch, marker)
    except OSError as error:
        log.error("data directory %s is not writable (%s): writes, sign-ins and counters cannot be kept", folder,
                  error.strerror or error)
        return dict(state="read_only", starts=None, created_at=None)
    if state == "new":
        log.warning("data directory %s is new and empty: expected on the first start only; if the service ran before, "
                    "the volume is not persistent and unpushed writes, sign-ins and keys were lost", folder)
    elif state == "unmarked":
        log.warning("data directory %s has files but no %s: marked now", folder, MARKER)
    else:
        log.info("data directory %s kept since %s (start %s)", folder, record["created_at"], record["starts"])
    return dict(state=state, starts=record["starts"], created_at=record["created_at"])
