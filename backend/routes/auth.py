"""Auth API: check status, login (handles first-run setup), logout, change
password while authed."""
from flask import Blueprint, jsonify, request, session

from auth import (check_password, is_authed, login_required, set_password,
                  setup_required)

auth_bp = Blueprint("auth", __name__)


@auth_bp.get("/auth/check")
def check():
    return jsonify({"authed": is_authed(), "setup_required": setup_required()})


@auth_bp.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    password = data.get("password", "")

    if setup_required():
        # First run: this is how the admin password gets set.
        if not password:
            return jsonify({"error": "password_required"}), 400
        set_password(password)
        session["user"] = "admin"
        return jsonify({"ok": True, "setup_completed": True})

    if not password:
        return jsonify({"error": "password_required"}), 400
    if check_password(password):
        session["user"] = "admin"
        return jsonify({"ok": True})
    return jsonify({"error": "invalid_password"}), 401


@auth_bp.post("/auth/logout")
def logout():
    session.pop("user", None)
    return jsonify({"ok": True})


@auth_bp.put("/auth/password")
@login_required
def change_password():
    data = request.get_json(silent=True) or {}
    current = data.get("current_password", "")
    new = data.get("new_password", "")
    if not new:
        return jsonify({"error": "new_password_required"}), 400
    if not check_password(current):
        return jsonify({"error": "invalid_current_password"}), 401
    set_password(new)
    return jsonify({"ok": True})