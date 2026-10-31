"""Network awareness helpers.

Used to:

1. Enumerate the server's own IPv4 interfaces — this is the "local network"
   table the resolver matches against. Per request the table is widened
   by ``visitor_networks(host)``: a /24 around the private IPv4 the
   visitor actually used to open the portal. That covers a portal behind
   a reverse proxy, where the request's source address is the proxy and
   the interface table says nothing about the visitor — yet they are on
   the LAN, because they typed the NAS's LAN address.
2. Parse a single pasted URL into one of: domain / public_ip / network_ip,
   using (1) to decide which bucket an IP falls into.
3. Pick the best URL for a service given the user's source IP. The
   priority is fixed and explicit (no settings toggle):

     1. An IP in the same local network as the visitor. If a
        translation entry maps one of the app's IPs onto the visitor's
        network, the *translated* URL is the tier-1 winner — a
        translated IP is by definition on the visitor's network.
     2. A hostname (domain) URL.
     3. A public IP (routable IPv4 on no detected local network).
     4. An IP that is on a *different* local network than the
        visitor's — useful for tunneled / admin-only addresses that
        the admin wants to keep visible but only as a last resort.

   Returns (url, kind) so the frontend can show *which* tier won
   (handy for the "Detected: ..." preview).

4. Offer the *other* URL of the LAN-versus-domain pair via
   ``resolve_alternate``. The priority above still decides which URL a
   card clicks through to; the alternate is only ever a secondary link
   (the LAN address is often faster, the domain is the only one that
   works off-LAN).

The server's interfaces are read once per process via a cached call.
If the cache is empty (cold start, container with no network, etc.) the
resolver still works — it just never finds a "same network" match and
falls through to the domain / public_ip tiers. Call
``reset_local_networks_cache()`` in tests after monkey-patching the
detector.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Optional
from urllib.parse import urlparse


# ---- local network detection ------------------------------------------------

_local_networks_cache: Optional[list[ipaddress.IPv4Network]] = None

# Ranges that mean "the visitor reached the box on a LAN address".
# ``IPv4Address.is_private`` is not usable here: it also covers the
# IANA special-purpose ranges (TEST-NET, benchmarking, documentation)
# that say nothing about the visitor's LAN.
_LAN_RANGES = tuple(ipaddress.IPv4Network(net) for net in (
    "10.0.0.0/8",        # RFC 1918
    "172.16.0.0/12",     # RFC 1918
    "192.168.0.0/16",    # RFC 1918
    "169.254.0.0/16",    # link-local
    "100.64.0.0/10",     # carrier-grade NAT / tunnel overlays
    "127.0.0.0/8",       # loopback (local development)
))


def get_local_networks() -> list[ipaddress.IPv4Network]:
    """Return the CIDRs of all active IPv4 interfaces on this host.

    Detection strategy (Linux): open a UDP socket to a public IP — the
    kernel picks the source interface. ``getsockname()`` gives the local
    IP. Netmask is read from ``/proc/net/route`` (every Linux interface
    has an entry there). On other OSes we fall back to a /24 guess.

    Result is sorted so tests are stable and order is deterministic.
    """
    global _local_networks_cache
    if _local_networks_cache is not None:
        return _local_networks_cache

    nets: list[ipaddress.IPv4Network] = []

    # 1) Find the set of local IPv4 addresses by inspecting the routing
    #    table. /proc/net/route lists one line per interface with its
    #    destination + mask. The default-route line (destination
    #    0.0.0.0) is NOT a local network — it's where to send traffic
    #    that doesn't match anything more specific. If we kept it, the
    #    synthesized 0.0.0.0/0 entry would match every IP, which would
    #    collapse "same-network" detection to "always yes" and break
    #    the resolver. We treat the per-interface /N lines as the real
    #    local networks.
    route_lines = _read_proc_route()
    if route_lines is not None:
        for parts in route_lines:
            iface, dest_hex, _gw, _flags, _ref, _use, _metric, mask_hex = parts[:8]
            dest = _hex_to_ipv4(dest_hex)
            mask = _hex_to_ipv4(mask_hex)
            if not dest or not mask:
                continue
            # Skip the default-route line (dest=0.0.0.0).
            if dest == "0.0.0.0":
                continue
            try:
                net = ipaddress.IPv4Network((dest, mask), strict=False)
            except ValueError:
                continue
            # Skip the loopback (127.0.0.0/8).
            if net.network_address == ipaddress.IPv4Address("127.0.0.0") and net.prefixlen == 8:
                continue
            nets.append(net)
    else:
        # /proc/net/route unavailable (macOS, locked-down container):
        # fall back to a getsockname probe and assume /24.
        local_ip = _probe_local_ip()
        if local_ip:
            nets.append(ipaddress.IPv4Network(f"{local_ip}/24", strict=False))

    nets = sorted(set(nets), key=str)
    _local_networks_cache = nets
    return nets


def _read_proc_route() -> Optional[list[list[str]]]:
    try:
        with open("/proc/net/route", "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    if len(lines) < 2:
        return []
    return [line.split() for line in lines[1:] if line.strip()]


def _hex_to_ipv4(hexstr: str) -> Optional[str]:
    """Convert little-endian hex from /proc/net/route to a dotted quad."""
    if not hexstr or len(hexstr) != 8:
        return None
    try:
        b = bytes.fromhex(hexstr)
    except ValueError:
        return None
    return ".".join(str(b[i]) for i in range(3, -1, -1))


def _probe_local_ip() -> Optional[str]:
    """Open a UDP socket and let the kernel pick a source IP. UDP ``connect``
    doesn't send anything, so this is safe and free. Returns the dotted
    quad or None if it fails (e.g. no network)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def reset_local_networks_cache() -> None:
    """Clear the cached interface list (for tests)."""
    global _local_networks_cache
    _local_networks_cache = None


def portal_ip(host: str) -> Optional[ipaddress.IPv4Address]:
    """Return the private IPv4 the visitor used to reach the portal, or
    None.

    Only a private literal counts: a public address or a hostname says
    nothing about the visitor's LAN.
    """
    s = (host or "").strip()
    if not s:
        return None
    if "://" not in s:
        s = "//" + s
    try:
        hostname = urlparse(s).hostname
    except ValueError:
        return None
    if not hostname:
        return None
    try:
        ip = ipaddress.IPv4Address(hostname)
    except ipaddress.AddressValueError:
        return None
    return ip if any(ip in net for net in _LAN_RANGES) else None


def visitor_address(host: str, remote_addr: str) -> str:
    """The address to treat as the visitor's for network matching.

    Behind a reverse proxy the request's source address is the proxy, so
    every same-network test fails and the resolver falls through to the
    public domain. When the portal was reached on a private IPv4 the
    visitor is demonstrably on that LAN — they typed the NAS's own LAN
    address — so that address is the honest answer. Otherwise the real
    source address stands.
    """
    ip = portal_ip(host)
    return str(ip) if ip is not None else remote_addr


def visitor_networks(host: str) -> list[ipaddress.IPv4Network]:
    """The local networks to classify app URLs against for one request:
    the interfaces detected on this host, plus the network the visitor
    used to reach the portal.

    When a detected interface already covers the portal's address its
    real mask is kept — a ZeroTier /16 must not be shrunk to a guessed
    /24, or an app URL on another /24 of the same overlay would fall out
    of tier 1. The synthetic /24 is only a fallback for a portal whose
    subnet this host has no route line for, and it matches the
    assumption ``get_local_networks`` makes without ``/proc/net/route``.
    """
    nets = list(get_local_networks())
    ip = portal_ip(host)
    if ip is None:
        return nets
    if any(ip in net for net in nets):
        return nets
    nets.append(ipaddress.IPv4Network(f"{ip}/24", strict=False))
    return nets


# ---- URL parsing ------------------------------------------------------------


def parse_url(raw: str, networks: Optional[list] = None) -> dict:
    """Categorize a pasted URL into the storage buckets.

    ``networks`` is the local-network list to classify against. The
    resolver passes the per-request list from ``visitor_networks`` so an
    app URL on the visitor's own LAN is classified as a local address
    even when the server's interface table doesn't list that subnet.
    Defaults to the detected interfaces.

    Returns a dict with keys:
      scheme:    "http" | "https" | None
      host:      bare hostname or IPv4 literal (no scheme, no port, no path)
      port:      int or None
      domain:    host if it's a hostname, else None
      public_ip: host if it's a literal IP on NO local network, else None
      network_ip:host if it's a literal IP on a local network, else None
      path:      any path/query string (kept verbatim; the resolver
                 concatenates this onto the chosen host when building
                 the final URL)
    """
    s = (raw or "").strip()
    out = {"scheme": None, "host": None, "port": None,
           "domain": None, "public_ip": None, "network_ip": None, "path": ""}
    if not s:
        return out

    # Add a scheme so urlparse doesn't choke on bare hostnames.
    if "://" not in s:
        s = "http://" + s
    parsed = urlparse(s)
    if parsed.scheme in ("http", "https"):
        out["scheme"] = parsed.scheme
    host = parsed.hostname
    if not host:
        return out
    # IPv6 literals are wrapped in [..] by urlparse; strip the brackets.
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    out["host"] = host
    try:
        out["port"] = parsed.port
    except ValueError:
        # Malformed port: ignore rather than crash the form.
        out["port"] = None
    out["path"] = parsed.path or ""
    if parsed.query:
        out["path"] += "?" + parsed.query
    # Normalize the bare-root path (`/`) to an empty string so that
    # `https://example.com/` and `https://example.com` parse to the
    # same fields. The browser treats them identically, and storing
    # them identically avoids the "did I type a slash or not" noise
    # when the user re-edits the app.
    if out["path"] == "/":
        out["path"] = ""

    # Is it a literal IPv4? Try parsing; if it fails it's a hostname
    # (or an IPv6, which we treat as a domain — same-network detection
    # only works for v4 in this version).
    try:
        ip = ipaddress.IPv4Address(host)
    except ipaddress.AddressValueError:
        out["domain"] = host
        return out

    local = get_local_networks() if networks is None else networks
    for net in local:
        if ip in net:
            out["network_ip"] = host
            return out
    out["public_ip"] = host
    return out


# ---- resolver ---------------------------------------------------------------

# The user-settable ip_translation table is a {from_ip: to_ip} dict. We do
# a SINGLE-LEVEL lookup (no recursion) — a chain like {A:B, B:C} will
# translate A->B but never A->C. That matches the "I accept the bug"
# trade-off: the admin takes responsibility for the entries they add.


def _same_network(ip_str: str, user_ip: str,
                  networks: Optional[list] = None) -> bool:
    """True if both ``ip_str`` and ``user_ip`` fall inside the same
    local network. False if either isn't an IPv4 literal or if there's
    no shared local network. ``networks`` overrides the detected
    interface list (see ``visitor_networks``).
    """
    try:
        ip = ipaddress.IPv4Address(ip_str)
        user = ipaddress.IPv4Address(user_ip)
    except ipaddress.AddressValueError:
        return False
    local = get_local_networks() if networks is None else networks
    for net in local:
        if user in net and ip in net:
            return True
    return False


def _translated_to_user_network(ip_str: str, user_ip: str,
                                translation: dict,
                                networks: Optional[list] = None) -> Optional[str]:
    """If ``ip_str`` has a translation entry, return the translated IP
    iff the translated IP is on the user's network. Otherwise None.
    """
    target = translation.get(ip_str)
    if not target:
        return None
    return target if _same_network(target, user_ip, networks) else None


def synthesize_urls(app: dict) -> list[str]:
    """Return the canonical URL list for an app.

    Apps now store a ``urls`` list of full URLs (one per line in the
    form). The order of the list is the resolver's priority order —
    the first URL is the preferred one for visitors the resolver
    can't bucket more specifically.

    Apps that still have the legacy structured shape
    (``network_ips`` / ``domain`` / ``public_ip`` / ``scheme`` /
    ``port`` / ``path`` / single ``url``) are converted on the fly so
    the read path keeps working. The synthesized list uses the same
    per-URL scheme + port + path for every entry — the canonical
    form's whole point is that you can give different URLs different
    schemes/ports/paths.
    """
    urls = app.get("urls")
    if isinstance(urls, list) and urls:
        return [str(u).strip() for u in urls if str(u).strip()]
    if isinstance(urls, str) and urls.strip():
        return [u.strip() for u in urls.replace(",", "\n").splitlines() if u.strip()]

    # Legacy structured shape. Build one URL per field. They all share
    # the same scheme/port/path (that's the data-loss the canonical
    # shape fixes), so the result is the best the old shape can do.
    out: list[str] = []
    scheme = app.get("scheme") or "http"
    port = app.get("port")
    path = app.get("path") or ""
    for ip in app.get("network_ips") or []:
        out.append(_compose(scheme, ip, port, path))
    if app.get("domain"):
        out.append(_compose(scheme, app["domain"], port, path))
    if app.get("public_ip"):
        out.append(_compose(scheme, app["public_ip"], port, path))
    if not out:
        legacy = (app.get("url") or "").strip()
        if legacy:
            out.append(legacy)
    # Dedupe, preserve order.
    seen = set()
    deduped = []
    for u in out:
        if u and u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped


def _compose(scheme: str, host: str, port, path: str) -> str:
    """Build a URL string from individual parts. Used by synthesize_urls
    to assemble a URL for the legacy structured shape. Empty parts
    are dropped (``http://host`` rather than ``http://host:/``)."""
    if port:
        hp = f"{host}:{port}"
    else:
        hp = host
    return f"{scheme}://{hp}{path or ''}"


def _parsed_urls(app: dict, networks: Optional[list] = None) -> list[dict]:
    """Return the app's URL list paired with their parsed form.

    The list preserves the user's input order and verbatim strings.
    Each entry is a dict ``{"raw", "parsed"}`` where ``raw`` is the
    URL as stored and ``parsed`` is the result of ``parse_url`` for
    host-type classification (network_ip / public_ip / domain). We
    keep both because the resolver needs the parsed host for tier
    decisions but should return the raw URL — the user wrote it that
    way for a reason, and re-encoding can drop query strings, fragment
    handling, and trailing-slash nuances.
    """
    out = []
    for raw in synthesize_urls(app):
        p = parse_url(raw, networks)
        if p.get("host"):
            out.append({"raw": raw, "parsed": p})
    return out


def _lan_candidate(entries: list[dict], user_ip: str, translation: dict,
                   networks: Optional[list] = None) -> Optional[dict]:
    """Return the URL a same-network visitor would use: a direct
    same-subnet IP first, then a translation entry that lands on the
    visitor's network. None if neither applies."""
    for e in entries:
        p = e["parsed"]
        if p.get("network_ip") and _same_network(p["host"], user_ip, networks):
            return _from_entry(e, "network")
    for e in entries:
        p = e["parsed"]
        if p.get("public_ip") or p.get("network_ip"):
            translated = _translated_to_user_network(
                p["host"], user_ip, translation, networks)
            if translated:
                new_url = _swap_host(e["raw"], translated)
                return _result(new_url, translated, p, "translated")
    return None


def _domain_candidate(entries: list[dict]) -> Optional[dict]:
    """Return the first URL whose host is a hostname, else None."""
    for e in entries:
        if e["parsed"].get("domain"):
            return _from_entry(e, "domain")
    return None


def resolve_url(app: dict, user_ip: str,
                translation: Optional[dict] = None,
                networks: Optional[list] = None) -> Optional[dict]:
    """Pick the best URL for ``app`` given the user's source IP.

    ``networks`` is the local-network list to classify against; the
    request layer passes ``visitor_networks(host)`` so a visitor who
    reached the portal by its LAN address is treated as being on that
    LAN even when the source address can't prove it (reverse proxy).
    Defaults to the detected interfaces.

    The app carries a list of full URLs (synthesized from the legacy
    structured shape if needed). Each URL is paired with its parsed
    host-type for tier decisions, but the URL is returned verbatim —
    the user wrote the URL with a specific scheme, port, path, query,
    and trailing slash, and re-encoding can drop nuance.

    Priority chain (fixed, no settings toggle):

      1. Same-network IP. A literal IP host on the same local
         network as the visitor wins (kind=network). If a
         translation entry maps one of the app's IP hosts onto
         the visitor's network, the *translated* URL is also
         tier 1 (kind=translated) — a translated IP is, by
         construction, on the visitor's network.
      2. Domain. The first URL with a hostname host (kind=domain).
      3. Public IP. A routable IPv4 on no detected local network
         (kind=public_ip).
      4. Other-network IP. An IP on a local network the visitor
         is NOT on (kind=other_network) — a tunneled / admin-only
         address kept visible for completeness.
      5. First URL in the list, as a last-resort fallback
         (kind=fallback).
      6. Legacy single-``url`` field (kind=legacy) — the host
         fields are empty here (the legacy URL is opaque).

    Ties are broken by URL-list order: the first entry that
    matches a tier wins.

    The return shape is ``{"url", "kind", "host", "port", "scheme",
    "path"}`` for all kinds except ``legacy``.
    """
    translation = translation or {}
    entries = _parsed_urls(app, networks)

    # 1. Same-network IP — direct match on the visitor's subnet, then
    #    the same via an ip_translation entry. Both are tier 1 because
    #    a translated IP is, by construction, on the visitor's network.
    lan = _lan_candidate(entries, user_ip, translation, networks)
    if lan:
        return lan

    # 2. Domain — first URL with a hostname host.
    domain = _domain_candidate(entries)
    if domain:
        return domain

    # 3. Public IP — first URL whose host is a public literal IP
    #    (an IPv4 on no detected local network).
    for e in entries:
        p = e["parsed"]
        if p.get("public_ip"):
            return _from_entry(e, "public_ip")

    # 4. Other-network IP — an IP on a local network the visitor
    #    is not on. Useful for tunneled / admin-only addresses the
    #    admin kept for completeness.
    for e in entries:
        p = e["parsed"]
        if p.get("network_ip"):
            return _from_entry(e, "other_network")

    # 5. First URL in the list, as a last-resort fallback.
    if entries:
        return _from_entry(entries[0], "fallback")

    # 6. Legacy single-`url` field. Treat as opaque public URL.
    legacy = (app.get("url") or "").strip()
    if legacy:
        return {"url": legacy, "kind": "legacy", "host": "", "port": None,
                "scheme": "", "path": ""}

    return None


def resolve_alternate(app: dict, primary_kind: Optional[str],
                      networks: Optional[list] = None) -> Optional[dict]:
    """Return the public domain to offer as a second link, or None.

    Only the domain, and only when the primary URL is a LAN address. A
    LAN address is never offered the other way round: if the visitor is
    not on that network the link cannot work, and a link that cannot
    work is not shown at all.

    The return shape matches ``resolve_url``.
    """
    if primary_kind not in ("network", "translated"):
        return None
    return _domain_candidate(_parsed_urls(app, networks))


def _from_entry(entry: dict, kind: str) -> dict:
    """Build a result dict from a ``_parsed_urls`` entry and a chosen kind."""
    p = entry["parsed"]
    return _result(entry["raw"], p["host"], p, kind)


def _result(url: str, host: str, p: dict, kind: str) -> dict:
    return {"url": url, "kind": kind, "host": host, "port": p.get("port"),
            "scheme": p.get("scheme") or "http", "path": p.get("path") or ""}


def _swap_host(url: str, new_host: str) -> str:
    """Replace the host in a URL with ``new_host`` while keeping the
    scheme, port, path, query, and fragment intact. Used by the
    translation tier."""
    # Re-parse to be safe; ``urlparse`` is cheap and lets us build a
    # well-formed URL even if the original was missing a path or had
    # an unusual netloc.
    raw = url
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlparse(raw)
    # IPv6 literal wrap.
    if ":" in new_host and not new_host.startswith("["):
        host_part = f"[{new_host}]"
    else:
        host_part = new_host
    netloc = host_part
    if parsed.port and not _has_port(new_host):
        netloc = f"{host_part}:{parsed.port}"
    path = parsed.path or ""
    if parsed.query:
        path += "?" + parsed.query
    if parsed.fragment:
        path += "#" + parsed.fragment
    return f"{parsed.scheme}://{netloc}{path}"


def _has_port(host: str) -> bool:
    """Cheap port-presence check: a literal IPv4 or hostname with
    a colon is an IPv6 address (which carries its own brackets)."""
    if host.startswith("[") and "]" in host:
        return False
    return host.count(":") > 0


def is_translatable(app: dict, user_ip: str,
                    translation: Optional[dict] = None,
                    networks: Optional[list] = None) -> bool:
    """True iff this app has at least one URL we can serve the user.

    "Translatable" means: at least one URL has a host the user can
    plausibly reach. This includes:

      * any same-network IP (direct or via translation) — tier 1/2
      * any URL with a hostname host — reachable as a public domain
      * any URL with a literal IP host — reachable directly

    Apps with no usable URLs at all are NOT translatable. The
    resolver's tier chain decides *which* URL to show; this filter
    decides whether to show the app at all.
    """
    translation = translation or {}
    for e in _parsed_urls(app, networks):
        p = e["parsed"]
        host = p.get("host") or ""
        if p.get("network_ip") and _same_network(host, user_ip, networks):
            return True
        # A literal IP (public or network) is translatable if either
        # it's directly reachable (no translation needed) or the
        # translation table maps it onto the user's network. We don't
        # gate this on public_ip alone — a server-local network IP that
        # isn't on the user's network still needs translation to be
        # reachable from the user.
        if p.get("public_ip") or p.get("network_ip"):
            if _translated_to_user_network(host, user_ip, translation, networks):
                return True
            return True
        if p.get("domain"):
            return True
    return False
