"""OpenAI image generation wrapper used by the generate_plan_image tool."""

from __future__ import annotations

import base64
from dataclasses import dataclass

from openai import AsyncOpenAI

from app.config import get_settings


@dataclass
class GeneratedImage:
    data: bytes
    mime_type: str
    model: str
    prompt: str


async def generate_image(prompt: str, *, size: str = "1024x1024") -> GeneratedImage:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured — image generation requires OpenAI."
        )

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    resp = await client.images.generate(
        model=settings.openai_image_model,
        prompt=prompt,
        size=size,
        n=1,
    )
    if not resp.data:
        raise RuntimeError("Image API returned no data")

    item = resp.data[0]
    if getattr(item, "b64_json", None):
        data = base64.b64decode(item.b64_json)
    elif getattr(item, "url", None):
        # Fallback: fetch URL bytes
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as http:
            r = await http.get(item.url)
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
