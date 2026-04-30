"""OpenAI image generation wrapper used by the generate_plan_image tool."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Literal

from openai import AsyncOpenAI

from app.config import get_settings


@dataclass
class GeneratedImage:
    data: bytes
    mime_type: str
    model: str
    prompt: str


async def generate_image(
    prompt: str,
    *,
    size: Literal[
        "auto",
        "1024x1024",
        "1536x1024",
        "1024x1536",
        "256x256",
        "512x512",
        "1792x1024",
        "1024x1792",
    ] = "1024x1024",
    api_key: str | None = None,
) -> GeneratedImage:
    settings = get_settings()
    resolved_api_key = api_key or settings.openai_api_key
    if not resolved_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured — image generation requires OpenAI."
        )

    client = AsyncOpenAI(api_key=resolved_api_key)
    resp = await client.images.generate(
        model=settings.openai_image_model,
        prompt=prompt,
        size=size,
        n=1,
    )
    if not resp.data:
        raise RuntimeError("Image API returned no data")

    item = resp.data[0]
    b64_json = item.b64_json
    url = item.url
    if b64_json:
        data = base64.b64decode(b64_json)
    elif url:
        # Fallback: fetch URL bytes
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as http:
            r = await http.get(url)
            r.raise_for_status()
            data = r.content
    else:
        raise RuntimeError("Image API returned neither b64_json nor url")

    return GeneratedImage(
        data=data,
        mime_type="image/png",
        model=settings.openai_image_model,
        prompt=prompt,
    )
