"""On-demand HTTP health check for an app URL.

Tries a cheap HEAD first; falls back to a streaming GET (response headers only,
body never downloaded) when the server rejects HEAD (405) or the request fails.
``online`` is True for any response with a status below 500 — 401/403/404 mean
the service is *up* but requires auth or the path is wrong, which is the useful
signal for a NAS dashboard.
"""
import time

import requests

_UA = "Mozilla/5.0 (compatible; NASPortal/1.0)"
_TIMEOUT = 5


def ping(url: str) -> dict:
    start = time.monotonic()
    resp = None
    try:
        resp = requests.head(url, timeout=_TIMEOUT, headers={"User-Agent": _UA}, allow_redirects=True)
        if resp.status_code == 405:
            resp.close()
            resp = requests.get(url, timeout=_TIMEOUT, headers={"User-Agent": _UA},
                                allow_redirects=True, stream=True)
            resp.close()
    except requests.RequestException:
        try:
            resp = requests.get(url, timeout=_TIMEOUT, headers={"User-Agent": _UA},
                                allow_redirects=True, stream=True)
            resp.close()
        except requests.RequestException:
            resp = None

    status = resp.status_code if resp is not None else None
    latency = int((time.monotonic() - start) * 1000)
    online = status is not None and status < 500
    return {"online": online, "status": status, "latency_ms": latency}