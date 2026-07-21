"""Rewind AI provider profile.

Rewind AI provides an OpenAI-compatible chat completions API at
``https://api.rewind.ai/v1``. It offers 300+ models from various providers
(Anthropic, OpenAI, Google, DeepSeek, etc.) through a unified API.

Only free-tier models (``:free`` suffix) are surfaced in the picker.
"""

from __future__ import annotations

from typing import Any

from providers import register_provider
from providers.base import ProviderProfile


class RewindProfile(ProviderProfile):
    """Rewind AI — only surface free models from the live catalog."""

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        """Fetch all models, then keep only free ones (``:free`` suffix).

        Rewind's API returns ``{"models": [...]}`` instead of the standard
        ``{"data": [...]}``, so we override the base class to handle both
        formats and filter for free-tier models.
        """
        import json
        import urllib.request

        from hermes_cli.urllib_security import open_credentialed_url

        effective_base = base_url or self.base_url
        if not effective_base:
            return None
        url = effective_base.rstrip("/") + "/models"

        req = urllib.request.Request(url)
        if api_key:
            req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "hermes-cli")

        try:
            with open_credentialed_url(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
        except Exception:
            return None

        # Rewind uses {"models": [...]}, standard OpenAI uses {"data": [...]}
        items = data if isinstance(data, list) else data.get("models") or data.get("data") or []
        if not isinstance(items, list):
            return None

        free: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            mid = str(item.get("id") or "").strip()
            if not mid:
                continue
            if mid.endswith(":free"):
                free.append(mid)

        return free if free else None


rewind = RewindProfile(
    name="rewind",
    aliases=("rewind-ai",),
    env_vars=("REWIND_API_KEY",),
    display_name="Rewind AI",
    description="Rewind AI — free models via OpenAI-compatible API",
    signup_url="https://rewind.ai",
    base_url="https://api.rewind.ai/v1",
    fallback_models=(),
    supports_vision=True,
)

register_provider(rewind)
