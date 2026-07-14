"""Network awareness helpers.

Used to:

1. Enumerate the server's own IPv4 interfaces — this is the "local network"
   table the resolver matches against.
2. Parse a single pasted URL into one of: domain / public_ip / network_ip,
   using (1) to decide which bucket an IP falls into.
3. Pick the best URL for a service given the user's source IP. Priority:
     a) network_ips whose CIDR contains the source IP (direct match).
     b) network_ips that have a translation entry landing on the user's
        network — the "I know A and B are the same machine" table.
     c) domain (public hostname).
     d) public_ip.
     e) first network_ip — last-resort fallback (tunneled, may be slow).
   Returns (url, kind) so the frontend can show *which* tier won (handy
   for the "Detected: ..." preview).

The server's interfaces are read once per process via a cached call.
If the cache is empty (cold start, container with no network, etc.) the
resolver still works — it just never finds a "same network" match and
falls through to domain / public_ip. Call ``reset_local_networks_cache()``
in tests after monkey-patching the detector.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Optional
from urllib.parse import urlparse


# ---- local network detection ------------------------------------------------

_local_networks_cache: Optional[list[ipaddress.IPv4Network]] = None


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
    #    destination + mask. We treat every non-loopback line as a
    #    local network. (A 0.0.0.0 destination is the default route —
    #    the corresponding network is "whatever /N the mask says, with
    #    the *real* local IP" — so we still pick up the local IP by
    #    using getsockname on a probe socket.)
    route_lines = _read_proc_route()
    if route_lines is not None:
        for parts in route_lines:
            iface, dest_hex, _gw, _flags, _ref, _use, _metric, mask_hex = parts[:8]
            dest = _hex_to_ipv4(dest_hex)
            mask = _hex_to_ipv4(mask_hex)
            if not dest or not mask:
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


# ---- URL parsing ------------------------------------------------------------


def parse_url(raw: str) -> dict:
    """Categorize a pasted URL into the storage buckets.

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

    for net in get_local_networks():
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


def _same_network(ip_str: str, user_ip: str) -> bool:
    """True if both ``ip_str`` and ``user_ip`` fall inside the same
    detected local network. False if either isn't an IPv4 literal or
    if there's no shared local network.
    """
    try:
        ip = ipaddress.IPv4Address(ip_str)
        user = ipaddress.IPv4Address(user_ip)
    except ipaddress.AddressValueError:
        return False
    for net in get_local_networks():
        if user in net and ip in net:
            return True
    return False


def _translated_to_user_network(ip_str: str, user_ip: str,
                                translation: dict) -> Optional[str]:
    """If ``ip_str`` has a translation entry, return the translated IP
    iff the translated IP is on the user's network. Otherwise None.
    """
    target = translation.get(ip_str)
    if not target:
        return None
    return target if _same_network(target, user_ip) else None


def resolve_url(app: dict, user_ip: str,
                translation: Optional[dict] = None) -> Optional[dict]:
    """Pick the best URL for ``app`` given the user's source IP.

    Returns ``None`` if the app has nothing reachable. Otherwise returns
    ``{"url", "kind", "host", "port", "scheme", "path"}`` where ``kind``
    is one of "network", "translated", "domain", "public_ip", "fallback",
    "legacy".

    The legacy single-``url`` field is still honoured: if the new
    structured fields are all absent, we hand back the legacy URL
    verbatim. This keeps every existing app working unchanged.
    """
    translation = translation or {}
    network_ips = list(app.get("network_ips") or [])
    domain = app.get("domain") or ""
    public_ip = app.get("public_ip") or ""
    scheme = app.get("scheme") or "http"
    port = app.get("port")

    # 1. Same-network match (direct).
    for ip in network_ips:
        if _same_network(ip, user_ip):
            return _build(scheme, ip, port, "network", app)

    # 2. Translation: an "other network" IP that maps to something on
    #    the user's network. This catches the case where the service is
    #    only listed under its remote-network IP, but the admin has
    #    told us "that IP is actually the same machine as this one on
    #    your side".
    for ip in network_ips:
        translated = _translated_to_user_network(ip, user_ip, translation)
        if translated:
            return _build(scheme, translated, port, "translated", app)

    # 3. Public domain.
    if domain:
        return _build(scheme, domain, port, "domain", app)

    # 4. Public IP.
    if public_ip:
        return _build(scheme, public_ip, port, "public_ip", app)

    # 5. Last-resort: any network IP (tunneled). We just return the first.
    if network_ips:
        return _build(scheme, network_ips[0], port, "fallback", app)

    # 6. Legacy single-`url` field. Treat as opaque public URL.
    legacy = (app.get("url") or "").strip()
    if legacy:
        return {"url": legacy, "kind": "legacy", "host": "", "port": None,
                "scheme": "", "path": ""}

    return None


def _build(scheme: str, host: str, port, kind: str, app: dict) -> dict:
    """Construct a final URL string from the chosen host + scheme + port.
    If the app carries a path we keep it; otherwise root.
    """
    path = app.get("path") or ""
    if port:
        host_port = f"{host}:{port}"
    else:
        host_port = host
    url = f"{scheme}://{host_port}{path}"
    return {"url": url, "kind": kind, "host": host, "port": port,
            "scheme": scheme, "path": path}


def is_translatable(app: dict, user_ip: str,
                    translation: Optional[dict] = None) -> bool:
    """True iff this app has at least one URL we can serve the user.

    "Translatable" means: the user can actually click the link and end
    up on the service. This includes:

      * any same-network IP (direct or via translation) — tier 1/2
      * a public domain — the user can reach it from any network
      * a public IP — the user can reach it from any network

    Apps with only "other network" IPs (the fallback tier) are
    NOT considered translatable — those go through a tunnel and may
    not be reachable at all for some visitors.
    """
    translation = translation or {}
    for ip in app.get("network_ips") or []:
        if _same_network(ip, user_ip):
            return True
        if _translated_to_user_network(ip, user_ip, translation):
            return True
    if app.get("domain") or app.get("public_ip"):
        return True
    return False
