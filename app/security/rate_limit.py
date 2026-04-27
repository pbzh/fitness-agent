"""Authentication rate limiting.

NPMplus can own rate limiting at the proxy layer. When Redis is configured,
the app enforces a shared counter across workers/instances without storing
process-local state.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
from typing import Any
from urllib.parse import unquote, urlparse

from fastapi import HTTPException, Request, status

from app.config import get_settings


def client_ip(request: Request) -> str:
    direct = request.client.host if request.client else "unknown"
    settings = get_settings()
    trusted = [c.strip() for c in settings.trusted_proxy_cidrs.split(",") if c.strip()]
    try:
        direct_ip = ipaddress.ip_address(direct)
    except ValueError:
        return direct

    if not any(direct_ip in ipaddress.ip_network(cidr, strict=False) for cidr in trusted):
        return direct

    forwarded_for = request.headers.get("x-forwarded-for", "")
    first = forwarded_for.split(",", 1)[0].strip()
    return first or direct


def _auth_key(request: Request, email: str) -> str:
    raw = f"{client_ip(request)}:{email.lower()}".encode()
    return "fitness-agent:auth-rate:" + hashlib.sha256(raw).hexdigest()


def _encode_command(*parts: str | int) -> bytes:
    out = [f"*{len(parts)}\r\n".encode("ascii")]
    for part in parts:
        data = str(part).encode("utf-8")
        out.append(f"${len(data)}\r\n".encode("ascii"))
        out.append(data + b"\r\n")
    return b"".join(out)


async def _read_resp(reader: asyncio.StreamReader) -> Any:
    prefix = await reader.readexactly(1)
    if prefix == b"+":
        return (await reader.readline()).rstrip(b"\r\n").decode()
    if prefix == b"-":
        msg = (await reader.readline()).rstrip(b"\r\n").decode()
        raise RuntimeError(f"Redis error: {msg}")
    if prefix == b":":
        return int((await reader.readline()).rstrip(b"\r\n"))
    if prefix == b"$":
        length = int((await reader.readline()).rstrip(b"\r\n"))
        if length == -1:
            return None
        data = await reader.readexactly(length)
        await reader.readexactly(2)
        return data
    if prefix == b"*":
        length = int((await reader.readline()).rstrip(b"\r\n"))
        return [await _read_resp(reader) for _ in range(length)]
    raise RuntimeError("Unknown Redis response")


async def _redis_command(*parts: str | int) -> Any:
    settings = get_settings()
    if not settings.auth_rate_limit_redis_url:
        raise RuntimeError("AUTH_RATE_LIMIT_REDIS_URL is required for Redis auth rate limiting")

    parsed = urlparse(settings.auth_rate_limit_redis_url)
    if parsed.scheme != "redis":
        raise RuntimeError("Only redis:// URLs are supported for auth rate limiting")

    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    db = (parsed.path or "/0").lstrip("/") or "0"
    password = unquote(parsed.password) if parsed.password else None

    reader, writer = await asyncio.open_connection(host, port)
    try:
        if password:
            writer.write(_encode_command("AUTH", password))
            await writer.drain()
            await _read_resp(reader)
        if db != "0":
            writer.write(_encode_command("SELECT", db))
            await writer.drain()
            await _read_resp(reader)
        writer.write(_encode_command(*parts))
        await writer.drain()
        return await _read_resp(reader)
    finally:
        writer.close()
        await writer.wait_closed()


async def check_auth_rate_limit(request: Request, email: str) -> None:
    settings = get_settings()
    if settings.auth_rate_limit_backend in {"proxy", "disabled"}:
        return

    key = _auth_key(request, email)
    try:
        count = int(await _redis_command("INCR", key))
        if count == 1:
            await _redis_command("EXPIRE", key, settings.auth_rate_limit_window_seconds)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication rate limiter is unavailable",
        ) from exc

    if count > settings.auth_rate_limit_max_attempts:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many authentication attempts. Try again later.",
        )


async def clear_auth_rate_limit(request: Request, email: str) -> None:
    settings = get_settings()
    if settings.auth_rate_limit_backend != "redis":
        return
    try:
        await _redis_command("DEL", _auth_key(request, email))
    except Exception:
        return
