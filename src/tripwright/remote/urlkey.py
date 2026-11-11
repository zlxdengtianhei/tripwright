"""The service generates a URL key by default. The first start writes `url-key` (mode 600); later starts read it.
Set TRIPWRIGHT_URL_KEY_GENERATE=0 only to disable generation, or TRIPWRIGHT_URL_KEY to choose a key explicitly."""
from __future__ import annotations

import logging
import os
import secrets
import stat
from pathlib import Path

log = logging.getLogger("tripwright.remote")
SWITCH = "TRIPWRIGHT_URL_KEY_GENERATE"
EXPLICIT = ("TRIPWRIGHT_URL_KEY",)
FILE_NAME = "url-key"
MINIMUM = 32  # the same floor as a key the deployer sets (remote.app.URL_KEY_MIN)
ON, OFF = {"1", "true", "yes", "on"}, {"", "0", "false", "no", "off"}


def requested(environ=os.environ) -> bool:
    given = environ.get(SWITCH)
    value = ("1" if given is None else given).strip().casefold()
    if value in ON | OFF:
        return value in ON
    raise ValueError(f"{SWITCH} must be 1 (generate a URL key) or 0; it is a switch, not the key")


def key_file(data_dir: str | Path, environ=os.environ) -> Path | None:
    """The file to read or create, or None when the switch is off or a key is given explicitly (that one wins)."""
    if not requested(environ):
        return None
    path = Path(data_dir) / FILE_NAME
    given = [name for name in EXPLICIT if environ.get(name)]
    if given:
        log.warning("%s is set and takes precedence: %s is ignored and %s is not used", " and ".join(given), SWITCH, path)
        return None
    return path


def _valid(key: str) -> bool:
    return len(key) >= MINIMUM and key.replace("-", "").replace("_", "").isalnum() and key.isascii()


def read(path: Path) -> str | None:
    """The stored key, or None when there is no file yet. A file others can read is narrowed to 600 first."""
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return None
    if mode & 0o077:
        os.chmod(path, 0o600)
        log.warning("%s was readable by other users: its permissions are now 600", path)
    key = path.read_text(encoding="ascii", errors="replace").strip()
    if not _valid(key):
        raise ValueError(f"{path} does not hold a usable URL key (at least {MINIMUM} letters, digits, '-' or '_'): "
                         "delete the file and restart to generate a new one")
    return key


def ensure(path: Path) -> str:
    """Read the key, or create it once. The new file appears complete or not at all (written aside, then linked)."""
    key = read(path)
    if key is not None:
        log.info("URL-key login uses the generated key in %s", path)
        return key
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(32)  # 43 characters of A-Z a-z 0-9 - _
    scratch = path.with_name(f".{FILE_NAME}.{os.getpid()}.tmp")
    descriptor = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as sink:
            sink.write(key + "\n")
            sink.flush()
            os.fsync(sink.fileno())
        try:
            os.link(scratch, path)
        except FileExistsError:  # another start got there first: use its key
            return read(path)
    finally:
        scratch.unlink(missing_ok=True)
    log.warning("generated a URL key in %s (not shown in logs): run `tripwright address` to print the connector; "
                "delete the file and restart to replace it", path)
    return key
