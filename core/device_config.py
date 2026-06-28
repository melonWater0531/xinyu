"""Shared reCamera device address configuration helpers."""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse


DEVICE_IP_ENV = "RECAMERA_DEVICE_IP"
DEVICE_BASE_URL_ENV = "RECAMERA_BASE_URL"
SSCMA_PORT = 8090

_HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


def normalize_device_ip(value: str | None = None, *, required: bool = False) -> str:
    """Return a host[:port] style device address without an HTTP/WS scheme."""
    raw = (value or "").strip()
    if not raw:
        raw = (os.environ.get(DEVICE_IP_ENV) or "").strip()
    if not raw:
        base_url = (os.environ.get(DEVICE_BASE_URL_ENV) or "").strip()
        if base_url:
            raw = base_url
    if not raw:
        if required:
            raise ValueError(
                f"reCamera device address is required. Set {DEVICE_IP_ENV} or pass --device-ip/--gimbal-ip."
            )
        return ""

    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    host = parsed.netloc or parsed.path
    host = host.strip().strip("/")
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    if not host or not _HOST_RE.match(host):
        raise ValueError(f"Invalid reCamera device address: {value!r}")
    return host


def device_http_url(value: str | None = None, *, required: bool = False) -> str:
    host = normalize_device_ip(value, required=required)
    return f"http://{host}" if host else ""


def device_sscma_ws_url(value: str | None = None, *, required: bool = False) -> str:
    host = normalize_device_ip(value, required=required)
    if not host:
        return ""
    if ":" in host and not (host.startswith("[") and "]" in host):
        return f"ws://{host}/"
    return f"ws://{host}:{SSCMA_PORT}/"
