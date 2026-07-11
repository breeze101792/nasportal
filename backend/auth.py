"""Password auth, sessions, and route guards.

Single shared password (stored hashed in ``config/auth.json``). Before one is
set the portal runs in **setup mode**: the settings page may set the initial
password, and every other write endpoint is refused.
"""
from functools import wraps

from flask import jsonify, session
from werkzeug.security import check_password_hash, generate_password_hash

from storage import load_json, save_json


def get_auth() -> dict:
    # strict=True: a corrupt auth.json must NOT silently revert to setup mode
    # (which would let anyone set a new password). Raise instead — the admin
    # sees the error and fixes the file.
    return load_json("auth.json", default={"password_hash": ""}, strict=True)


def setup_required() -> bool:
    """True when no password has been set yet (first-run setup mode)."""
    return not (get_auth().get("password_hash") or "")


def is_authed() -> bool:
    return bool(session.get("user"))


def set_password(password: str) -> None:
    save_json("auth.json", {"password_hash": generate_password_hash(password)})


def check_password(password: str) -> bool:
    h = get_auth().get("password_hash", "")
    if not h:
        return False
    return check_password_hash(h, password)


def login_required(view):
    """Refuse (401) any request that isn't from an authed session."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_authed():
            return jsonify({"error": "login_required"}), 401
        return view(*args, **kwargs)
    return wrapped


def setup_or_login_required(view):
    """Like ``login_required``, but also passes while in setup mode — used by
    the endpoint that sets the initial password."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if setup_required():
            return view(*args, **kwargs)
        if not is_authed():
            return jsonify({"error": "login_required"}), 401
        return view(*args, **kwargs)
    return wrapped