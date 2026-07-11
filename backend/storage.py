"""Atomic JSON read/write helpers over the config directory.

Files are written via a temp file + ``os.replace`` so a crash mid-write can
never leave a half-written config (important for a single source of truth).
Read-modify-write sequences that span multiple handlers are serialized with a
cross-process ``fcntl.flock`` (the threaded dev server, or multiple workers if
run behind a WSGI server) so concurrent edits can't silently drop each other.
"""
import contextlib
import copy
import fcntl
import json
import os
import tempfile

from config import CONFIG_DIR, DEFAULT_SETTINGS, DEFAULT_APPS, DEFAULT_AUTH

_FILE_DEFAULTS = {
    "settings.json": DEFAULT_SETTINGS,
    "apps.json": DEFAULT_APPS,
    "auth.json": DEFAULT_AUTH,
}


def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _path(name: str):
    return CONFIG_DIR / name


@contextlib.contextmanager
def file_lock(name: str):
    """Cross-process exclusive lock on ``config/<name>.lock``. Hold this across
    a read-modify-write sequence (load_json -> mutate -> save_json) so two
    concurrent requests can't both read the same snapshot and clobber each other."""
    ensure_config_dir()
    lock_path = _path(name + ".lock")
    # Open fresh each call (not at import time) so this works across separate
    # worker processes and with or without app preloading.
    with lock_path.open("a", encoding="utf-8") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def load_json(name: str, default=None, strict=False):
    """Load ``config/<name>``. If absent, seed it with ``default`` (or the known
    default for that file) and return that. A corrupt file normally falls back
    to the default; with ``strict=True`` (used for security-critical files like
    auth.json) a corrupt file raises instead of silently downgrading."""
    ensure_config_dir()
    p = _path(name)
    fallback = default if default is not None else _FILE_DEFAULTS.get(name, {})
    if not p.exists():
        if fallback is not None:
            save_json(name, fallback)
        # Hand out a COPY, not the shared module-level default dict — callers
        # mutate the returned store in place (e.g. apps.append(...)) and a
        # shared object would leak those edits into every later fresh session
        # (the classic mutable-default bug). json.load already returns fresh
        # objects for files on disk; this keeps the absent-file path consistent.
        return copy.deepcopy(fallback)
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        if strict:
            raise
        return copy.deepcopy(fallback)


def save_json(name: str, data) -> None:
    """Atomically write ``data`` to ``config/<name>`` (pretty-printed, UTF-8)."""
    ensure_config_dir()
    p = _path(name)
    fd, tmp = tempfile.mkstemp(dir=str(CONFIG_DIR), prefix=name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, str(p))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise