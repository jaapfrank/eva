"""Render a narrow nginx ingress from the currently loaded managed-slot route.

Run as the unprivileged voice-ingress service, after eva.service starts. The
service is bound to eva.service so quiescent cutover also closes voice ingress.
No credentials are copied into nginx configuration or logs.
"""
from __future__ import annotations

import argparse
import ipaddress
from pathlib import Path
import re
import subprocess


def render(*, environment: str, listen: str, home_assistant: str,
           port: int = 44714, runtime: str = "/run/eva2-voice-ingress") -> str:
    addresses = [ipaddress.ip_address(value) for value in (listen, home_assistant)]
    if any(address.version != 4 or not address.is_private
           or address.is_unspecified or address.is_multicast for address in addresses):
        raise ValueError("voice ingress requires explicit private IPv4 addresses")
    if not 1024 <= port <= 65535 or not re.fullmatch(r"/[a-zA-Z0-9_/.-]+", runtime):
        raise ValueError("invalid ingress port or runtime directory")
    routes = re.findall(r"(?:^|\s)EVA2_CANARY_URL=([^\s]+)", environment)
    if len(routes) != 1 or routes[0] not in {
        "http://127.0.0.1:44710", "http://127.0.0.1:44711"
    }:
        raise ValueError("loaded Eva route is not one exact retained slot")
    return f"""# Generated from the loaded eva.service route; contains no credentials.
daemon off;
master_process off;
pid {runtime}/nginx.pid;
error_log stderr crit;
events {{ worker_connections 128; }}
http {{
    access_log off;
    server_tokens off;
    client_body_temp_path {runtime}/body;
    proxy_temp_path {runtime}/proxy;
    fastcgi_temp_path {runtime}/fastcgi;
    uwsgi_temp_path {runtime}/uwsgi;
    scgi_temp_path {runtime}/scgi;
    client_max_body_size 128k;
    client_body_buffer_size 128k;
    client_body_timeout 5s;
    client_header_timeout 5s;
    send_timeout 5s;
    keepalive_timeout 5s;
    server {{
        listen {listen}:{port};
        allow {home_assistant};
        deny all;
        location ~ "^/internal/v1/ha-voice/turns(?:/[0-9a-f]{{32}}/interrupt)?$" {{
            if ($request_method != POST) {{ return 405; }}
            if ($is_args != "") {{ return 400; }}
            proxy_pass {routes[0]};
            proxy_http_version 1.1;
            proxy_pass_request_headers off;
            proxy_set_header Host 127.0.0.1;
            proxy_set_header Authorization $http_authorization;
            proxy_set_header Content-Type application/json;
            proxy_set_header Connection "";
            proxy_buffering off;
            proxy_request_buffering off;
            proxy_cache off;
            proxy_max_temp_file_size 0;
            proxy_next_upstream off;
            proxy_ignore_client_abort off;
            proxy_connect_timeout 2s;
            proxy_read_timeout 35s;
            proxy_send_timeout 5s;
        }}
        location / {{ return 404; }}
    }}
}}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", required=True)
    parser.add_argument("--home-assistant", required=True)
    parser.add_argument("--runtime", default="/run/eva2-voice-ingress")
    parser.add_argument("--source-service", default="eva.service")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_-]+\.service", args.source_service):
        raise ValueError("invalid source service name")
    state = subprocess.run(
        ["/usr/bin/systemctl", "is-active", args.source_service],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if state != "active":
        raise RuntimeError("Eva ingress is not active")
    environment = subprocess.run(
        ["/usr/bin/systemctl", "show", args.source_service, "--property=Environment", "--value"],
        check=True, capture_output=True, text=True,
    ).stdout
    config = render(environment=environment, listen=args.listen,
                    home_assistant=args.home_assistant, runtime=args.runtime)
    directory = Path(args.runtime)
    for name in ("body", "proxy"):
        (directory / name).mkdir(mode=0o700, exist_ok=True)
    temporary = directory / "nginx.conf.new"
    temporary.write_text(config)
    temporary.replace(directory / "nginx.conf")


if __name__ == "__main__":
    main()
