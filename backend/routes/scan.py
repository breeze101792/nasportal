"""Scan API: enumerate the host's local networks and expand a CIDR/range +
port list into a candidate list for the browser to probe.

The actual probes (TCP/HTTP) run in the browser — the server only
validates input and produces the candidate list. This keeps the server
from making arbitrary network connections on behalf of a request and
matches the user's preference that scans reflect what their own
machine can see.
"""
import ipaddress

from flask import Blueprint, jsonify, request

from auth import login_required
from services.networks import get_local_networks

scan_bp = Blueprint("scan", __name__)

# Safety caps. The browser is doing the actual probing, but a misclick
# on a /8 plus 65k ports would still produce a 16M-row response.
# 1024 ports × up to 4 hosts (= 4096 candidates) is the realistic upper
# bound: a user scanning a single /32 against the top-1024 ports, or
# a /30 against 1024 ports. Larger combinations hit _MAX_HOSTS first.
_MAX_PORTS = 1024
_MAX_HOSTS = 4096
_MAX_CIDR_HOSTS = 4096  # hard cap on host count from a single CIDR
_MAX_RANGE_HOSTS = 1024  # stricter cap for explicit start/end
# Generous IP bounds: a /16 is the largest block we'd ever want to scan
# at once. A /8 is 16M hosts and obviously wrong.
_MAX_PREFIXLEN = 16


def _valid_app_url(u: str) -> bool:
    # Restrict the candidate URL to http:// (browsers can't probe https
    # without a valid cert; the user can add https later by hand).
    return bool(u) and u.lower().startswith("http://")


def _reject_reserved(net: ipaddress.IPv4Network) -> str | None:
    """Return a reason string if this network is one we should refuse
    to scan, else None. Reserved/private blocks are still allowed
    (that's the whole point — the user wants to scan their LAN), but
    loopback, multicast, and link-local are almost certainly mistakes
    and would also produce a lot of noise from the browser."""
    if net.is_loopback:
        return "loopback"
    if net.is_multicast:
        return "multicast"
    # 169.254.0.0/16 — link-local. The is_link_local property is on
    # IPv4Network in py3, but be explicit so it's obvious in code.
    if net.network_address in ipaddress.IPv4Network("169.254.0.0/16"):
        return "link_local"
    if net.is_unspecified:
        return "unspecified"
    return None


@scan_bp.get("/networks/local")
def list_local_networks():
    """Return the host's detected local networks as CIDR strings.

    Public (no auth) — the network list is the same info the resolver
    uses to choose the right URL for the visitor, so it's already
    implicit in the public portal behavior. No secrets are exposed.
    """
    nets = get_local_networks()
    return jsonify({"networks": [str(n) for n in nets]})


@scan_bp.post("/scan/expand")
@login_required
def scan_expand():
    """Expand a target (CIDR or start/end IPv4 range) + a port list into
    a flat list of (ip, port, url) candidates for the browser to probe.

    Body:
      {cidr: "10.0.0.0/24", ports: [80, 443, 8080]}
      or
      {start: "10.0.0.1", end: "10.0.0.254", ports: [80, ...]}

    Caps: at most ``_MAX_PORTS`` ports and ``_MAX_HOSTS`` total candidates.
    Reserved ranges (loopback, multicast, link-local, 0.0.0.0) are
    rejected — see ``_reject_reserved``. Validation errors return 400
    with a named error code; callers should branch on the code.
    """
    data = request.get_json(silent=True) or {}

    # ---- ports ----
    ports = data.get("ports")
    if not isinstance(ports, list) or not ports:
        return jsonify({"error": "ports_required"}), 400
    if len(ports) > _MAX_PORTS:
        return jsonify({"error": "too_many_ports", "max": _MAX_PORTS}), 400
    clean_ports: list[int] = []
    seen_ports: set[int] = set()
    for p in ports:
        if isinstance(p, bool) or not isinstance(p, int):
            return jsonify({"error": "invalid_port"}), 400
        if not (1 <= p <= 65535):
            return jsonify({"error": "invalid_port"}), 400
        if p not in seen_ports:
            seen_ports.add(p)
            clean_ports.append(p)
    clean_ports.sort()

    # ---- target ----
    cidr = data.get("cidr")
    start = data.get("start")
    end = data.get("end")
    if cidr is not None:
        if not isinstance(cidr, str):
            return jsonify({"error": "invalid_cidr"}), 400
        try:
            net = ipaddress.IPv4Network(cidr, strict=False)
        except ValueError:
            return jsonify({"error": "invalid_cidr"}), 400
        # Reject prefix lengths larger than the cap (i.e. networks
        # smaller than /16). A /17+ has >32k hosts; not what we want.
        if net.prefixlen < _MAX_PREFIXLEN:
            return jsonify({"error": "cidr_too_large", "max_prefixlen": _MAX_PREFIXLEN}), 400
        # Reserved range belt-and-braces.
        reason = _reject_reserved(net)
        if reason:
            return jsonify({"error": "reserved_range", "reason": reason}), 400
        hosts = list(net.hosts())
        if len(hosts) > _MAX_CIDR_HOSTS:
            return jsonify({"error": "too_many_hosts", "max": _MAX_CIDR_HOSTS}), 400
    elif start is not None or end is not None:
        if not isinstance(start, str) or not isinstance(end, str):
            return jsonify({"error": "invalid_range"}), 400
        try:
            a = ipaddress.IPv4Address(start)
            b = ipaddress.IPv4Address(end)
        except ValueError:
            return jsonify({"error": "invalid_range"}), 400
        if int(a) > int(b):
            return jsonify({"error": "invalid_range"}), 400
        count = int(b) - int(a) + 1
        if count > _MAX_RANGE_HOSTS:
            return jsonify({"error": "too_many_hosts", "max": _MAX_RANGE_HOSTS}), 400
        # Reject if the range is entirely in a reserved block.
        # Build a single network covering the range and check it.
        try:
            net = ipaddress.IPv4Network(f"{a}/{32 - (count - 1).bit_length()}", strict=False)
        except ValueError:
            return jsonify({"error": "invalid_range"}), 400
        reason = _reject_reserved(net)
        if reason:
            return jsonify({"error": "reserved_range", "reason": reason}), 400
        hosts = [ipaddress.IPv4Address(i) for i in range(int(a), int(b) + 1)]
    else:
        return jsonify({"error": "target_required"}), 400

    # ---- build candidates, cap to _MAX_HOSTS ----
    out: list[dict] = []
    for ip in hosts:
        for port in clean_ports:
            if len(out) >= _MAX_HOSTS:
                return jsonify({
                    "candidates": out,
                    "truncated": True,
                    "max": _MAX_HOSTS,
                })
            out.append({
                "ip": str(ip),
                "port": port,
                "url": f"http://{ip}:{port}/",
            })

    return jsonify({"candidates": out, "truncated": False})
