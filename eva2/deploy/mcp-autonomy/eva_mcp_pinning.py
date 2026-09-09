"""Opt-in IP-pinned MCP HTTP clients for the sealed ContextForge image.

Uses ContextForge's maintained URL/DNS validator and Host/SNI rewrite. Policy
is deployment-owned JSON, never supplied by a tool or remote MCP response.
"""
from __future__ import annotations

import ipaddress
import json
import os
from urllib.parse import urlsplit

import httpx


def _policy():
    raw = os.environ.get("EVA_MCP_PINNING_POLICY")
    if raw is None:
        return None
    value = json.loads(raw)
    fields = {"owner_networks", "blocked_addresses", "filesystem_origin", "filesystem_address"}
    if not isinstance(value, dict) or set(value) not in (fields, fields | {"managed_endpoints"}):
        raise ValueError("MCP pinning policy is invalid")
    if (not isinstance(value["owner_networks"], list)
            or not 1 <= len(value["owner_networks"]) <= 32
            or not isinstance(value["blocked_addresses"], list)
            or not value["blocked_addresses"]):
        raise ValueError("MCP pinning network policy is invalid")
    value["owner_networks"] = tuple(ipaddress.ip_network(x, strict=True) for x in value["owner_networks"])
    if any(not network.is_private or network.is_loopback or network.is_link_local
           or network.is_multicast or network.is_unspecified for network in value["owner_networks"]):
        raise ValueError("MCP pinning owner network scope is invalid")
    value["blocked_addresses"] = frozenset(ipaddress.ip_address(x) for x in value["blocked_addresses"])
    value["filesystem_address"] = ipaddress.ip_address(value["filesystem_address"])
    origin = urlsplit(value["filesystem_origin"])
    if (origin.scheme != "http" or not origin.hostname or origin.username or origin.password
            or origin.query or origin.fragment or origin.path not in {"", "/"}
            or origin.port is None or not value["filesystem_address"].is_private
            or value["filesystem_address"].is_loopback):
        raise ValueError("MCP pinning filesystem origin is invalid")
    value["filesystem_origin"] = (origin.scheme, origin.hostname, origin.port)
    managed = value.get("managed_endpoints", [])
    if not isinstance(managed, list) or len(managed) > 16:
        raise ValueError("MCP managed endpoints are invalid")
    admitted = []
    for item in managed:
        if (not isinstance(item, dict) or set(item) != {"endpoint", "resolved_ip"}
                or not all(isinstance(item[key], str) and item[key] for key in item)):
            raise ValueError("MCP managed endpoint is invalid")
        endpoint = urlsplit(item["endpoint"])
        address = ipaddress.ip_address(item["resolved_ip"])
        endpoint_port = endpoint.port if endpoint.port is not None else (443 if endpoint.scheme == "https" else 80)
        if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
                or endpoint.username is not None or endpoint.password is not None
                or endpoint.query or endpoint.fragment or not address.is_private
                or address.is_loopback or address.is_link_local or address.is_multicast
                or address.is_unspecified or address in value["blocked_addresses"]
                or not 1 <= endpoint_port <= 65535):
            raise ValueError("MCP managed endpoint is invalid")
        admitted.append((endpoint.scheme, endpoint.hostname, endpoint_port,
                         endpoint.path or "/", address))
    value["managed_endpoints"] = tuple(admitted)
    return value


def _admit(url: str, metadata: dict, policy: dict) -> None:
    target = urlsplit(url)
    address = ipaddress.ip_address(metadata["resolved_ip"])
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    port = target.port if target.port is not None else (443 if target.scheme == "https" else 80)
    if (address in policy["blocked_addresses"] or address.is_loopback
            or address.is_link_local or address.is_multicast or address.is_unspecified
            or target.username or target.password or not 1 <= port <= 65535):
        raise ValueError("MCP destination is outside the deployment policy")
    if ((target.scheme, target.hostname, port) == policy["filesystem_origin"]
            and address == policy["filesystem_address"]):
        return
    if (not target.query and not target.fragment
            and (target.scheme, target.hostname, port, target.path or "/", address)
            in policy["managed_endpoints"]):
        return
    if target.scheme == "https" and port == 443 and address.is_global:
        return
    if target.scheme in {"http", "https"} and any(address in network for network in policy["owner_networks"]):
        return
    raise ValueError("MCP destination is outside the deployment policy")


class _PinnedClient(httpx.AsyncClient):
    def __init__(self, policy: dict, **kwargs):
        if kwargs.get("verify") is False or "transport" in kwargs or "proxy" in kwargs or "mounts" in kwargs:
            raise ValueError("MCP pinning requires verified direct transport")
        # Different HTTPS hostnames may resolve to one IP. Never reuse an
        # IP-keyed TLS connection authenticated for another original hostname.
        limits = kwargs.get("limits", httpx.Limits())
        kwargs.update(trust_env=False, follow_redirects=False, http2=False,
                      limits=httpx.Limits(max_connections=limits.max_connections,
                                          max_keepalive_connections=0))
        super().__init__(**kwargs)
        self._eva_policy = policy

    async def send(self, request, **kwargs):
        kwargs["follow_redirects"] = False
        return await super().send(request, **kwargs)

    async def _send_single_request(self, request):
        # Each real request, including reconnects and pooled-session requests,
        # resolves/validates immediately before I/O. No second hostname lookup.
        from mcpgateway.common.validators import SecurityValidator
        from mcpgateway.services.tool_service import _pin_url_to_resolved_ip

        metadata = await SecurityValidator.validate_url_for_connection_pinning(str(request.url), "MCP URL")
        if not all(metadata.get(key) for key in ("resolved_ip", "hostname", "original_authority")):
            raise ValueError("MCP destination has no validated address")
        _admit(str(request.url), metadata, self._eva_policy)
        headers = request.headers.copy()
        headers["Host"] = metadata["original_authority"]
        extensions = dict(request.extensions)
        extensions["sni_hostname"] = metadata["hostname"]
        pinned = httpx.Request(request.method,
            _pin_url_to_resolved_ip(str(request.url), metadata["resolved_ip"]),
            headers=headers, stream=request.stream, extensions=extensions)
        return await super()._send_single_request(pinned)


def mcp_http_client(**kwargs):
    policy = _policy()
    return httpx.AsyncClient(**kwargs) if policy is None else _PinnedClient(policy, **kwargs)
