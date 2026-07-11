"""Scrape a URL's metadata (title, description, favicon) for the drag-and-drop
add flow. All errors are swallowed — we always return a best-effort result so
the frontend can prefill *something* even when the target is unreachable or
serves non-HTML.
"""
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

_UA = "Mozilla/5.0 (compatible; NASPortal/1.0)"
_TIMEOUT = 8


def scrape(url: str) -> dict:
    raw = (url or "").strip()
    if not raw:
        return {"title": "", "description": "", "favicon": "", "url": ""}
    if "://" not in raw:
        raw = "https://" + raw

    parsed = urlparse(raw)
    title = parsed.hostname or raw
    description = ""
    favicon = urljoin(raw, "/favicon.ico")

    try:
        resp = requests.get(raw, timeout=_TIMEOUT, headers={"User-Agent": _UA}, allow_redirects=True)
        if resp.ok and resp.text:
            soup = BeautifulSoup(resp.text, "html.parser")

            node = soup.find("title")
            if node and node.string:
                title = node.string.strip() or title

            meta = soup.find("meta", attrs={"name": "description"})
            if meta and meta.get("content"):
                description = meta["content"].strip()
            else:
                og = soup.find("meta", attrs={"property": "og:description"})
                if og and og.get("content"):
                    description = og["content"].strip()

            base = resp.url
            icon_link = None
            for rel in ("icon", "shortcut icon", "apple-touch-icon"):
                link = soup.find("link", attrs={"rel": rel})
                if link and link.get("href"):
                    icon_link = link["href"]
                    break
            # An empty data URI (e.g. href="data:,") is a site suppressing its
            # favicon request — not a usable icon. Fall back to /favicon.ico.
            if icon_link and not icon_link.strip().startswith(("data:,", "data:;", "data: ")):
                favicon = urljoin(base, icon_link)
            else:
                favicon = urljoin(base, "/favicon.ico")
    except Exception:
        pass

    return {"title": title, "description": description, "favicon": favicon, "url": raw}