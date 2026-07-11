"""NAS Portal backend package.

Import paths are rooted at this directory: ``import config``, ``from routes
import ...``, ``from services import ...``. This shim puts ``backend/`` on
``sys.path`` so the same absolute imports work whether the app is run as
``python backend/app.py`` or loaded as a module (e.g. ``backend.app:create_app()``
via a WSGI server).
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)