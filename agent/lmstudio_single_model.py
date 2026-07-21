"""Single-model request gate for LM Studio endpoints.

LM Studio can auto-evict JIT-loaded models when requests arrive sequentially,
but concurrent requests to different local models can race that unload/load
cycle.  This module serializes Hermes calls to LM Studio-compatible local
endpoints while leaving cloud providers untouched.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import tempfile
import threading
import time
from typing import Any, Optional
from urllib.parse import urlparse

try:
    import fcntl
except ImportError:
    fcntl = None

msvcrt = None
if fcntl is None:
    try:
        import msvcrt
    except ImportError:
        pass

_PROCESS_LOCK = threading.Lock()
_LOCK_PATH = os.getenv(
    "HERMES_LMSTUDIO_SINGLE_MODEL_LOCK",
    os.path.join(tempfile.gettempdir(), "hermes-lmstudio-single-model.lock"),
)
_LOCAL_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "host.docker.internal",
    "host.containers.internal",
}


def _env_enabled() -> bool:
    value = os.getenv("HERMES_LMSTUDIO_SINGLE_MODEL", "1").strip().lower()
    return value not in {"0", "false", "no", "off", "disabled"}


def _hostname_is_private(hostname: str) -> bool:
    if not hostname:
        return False
    host = hostname.strip().lower().rstrip(".")
    if host in _LOCAL_HOSTS:
        return True
    if host.startswith("192.168.") or host.startswith("10."):
        return True
    if host.startswith("172."):
        try:
            second = int(host.split(".", 2)[1])
            return 16 <= second <= 31
        except Exception:
            return False
    return False


def _parse_base_url(base_url: Any):
    raw = str(base_url or "").strip()
    if not raw:
        return None
    try:
        return urlparse(raw if "://" in raw else f"http://{raw}")
    except Exception:
        return None


def should_gate_lmstudio_endpoint(provider: Optional[str] = None, base_url: Any = None) -> bool:
    """Return True when this request should be serialized for LM Studio."""
    if not _env_enabled():
        return False
    provider_name = (provider or "").strip().lower()
    if provider_name == "lmstudio":
        return True
    parsed = _parse_base_url(base_url)
    if parsed is None:
        return False
    host = parsed.hostname or ""
    port = parsed.port
    if port == 1234 and _hostname_is_private(host):
        return True
    return "lmstudio" in provider_name and _hostname_is_private(host)


def _acquire_file_lock():
    if msvcrt is not None:
        handle = open(_LOCK_PATH, "a+b")
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    elif fcntl is not None:
        handle = open(_LOCK_PATH, "a+", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    else:
        handle = open(_LOCK_PATH, "a+", encoding="utf-8")
    return handle


def _release_file_lock(handle) -> None:
    try:
        if msvcrt is not None:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
        elif fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


@contextlib.contextmanager
def lmstudio_single_model_guard(
    *,
    provider: Optional[str] = None,
    base_url: Any = None,
    model: Optional[str] = None,
    purpose: str = "",
    logger: Any = None,
):
    """Serialize local LM Studio requests across threads and processes."""
    if not should_gate_lmstudio_endpoint(provider, base_url):
        yield
        return

    started = time.monotonic()
    _PROCESS_LOCK.acquire()
    file_handle = None
    try:
        file_handle = _acquire_file_lock()
        waited = time.monotonic() - started
        if logger is not None and waited >= 1.0:
            logger.info(
                "LM Studio single-model gate acquired after %.1fs for %s (%s)",
                waited,
                model or "default",
                purpose or "request",
            )
        yield
    finally:
        if file_handle is not None:
            _release_file_lock(file_handle)
        _PROCESS_LOCK.release()


class _LockedStream:
    """Proxy that holds the LM Studio gate until a streaming response ends."""

    def __init__(self, stream: Any, guard: Any):
        self._stream = stream
        self._guard = guard
        self._released = False

    def _release(self, exc_type=None, exc=None, tb=None) -> None:
        if self._released:
            return
        self._released = True
        self._guard.__exit__(exc_type, exc, tb)

    def __iter__(self):
        try:
            for item in self._stream:
                yield item
        finally:
            self._release()

    def __enter__(self):
        enter = getattr(self._stream, "__enter__", None)
        if callable(enter):
            enter()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            exit_fn = getattr(self._stream, "__exit__", None)
            if callable(exit_fn):
                return exit_fn(exc_type, exc, tb)
            return False
        finally:
            self._release(exc_type, exc, tb)

    def close(self) -> None:
        try:
            close_fn = getattr(self._stream, "close", None)
            if callable(close_fn):
                close_fn()
        finally:
            self._release()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    def __del__(self):  # pragma: no cover - best-effort cleanup.
        try:
            self.close()
        except Exception:
            pass


def create_chat_completion_with_lmstudio_gate(
    client: Any,
    kwargs: dict,
    *,
    provider: Optional[str] = None,
    base_url: Any = None,
    model: Optional[str] = None,
    purpose: str = "",
    logger: Any = None,
):
    """Call ``chat.completions.create`` with LM Studio serialization if needed."""
    effective_base_url = base_url if base_url is not None else getattr(client, "base_url", None)
    effective_model = model or kwargs.get("model")
    if not should_gate_lmstudio_endpoint(provider, effective_base_url):
        return client.chat.completions.create(**kwargs)

    guard = lmstudio_single_model_guard(
        provider=provider,
        base_url=effective_base_url,
        model=effective_model,
        purpose=purpose,
        logger=logger,
    )
    guard.__enter__()
    try:
        response = client.chat.completions.create(**kwargs)
    except Exception:
        guard.__exit__(*sys.exc_info())
        raise

    if kwargs.get("stream"):
        return _LockedStream(response, guard)

    guard.__exit__(None, None, None)
    return response


@contextlib.asynccontextmanager
async def async_lmstudio_single_model_guard(
    *,
    provider: Optional[str] = None,
    base_url: Any = None,
    model: Optional[str] = None,
    purpose: str = "",
    logger: Any = None,
):
    """Async variant of ``lmstudio_single_model_guard``."""
    if not should_gate_lmstudio_endpoint(provider, base_url):
        yield
        return

    started = time.monotonic()
    await asyncio.to_thread(_PROCESS_LOCK.acquire)
    file_handle = None
    try:
        file_handle = await asyncio.to_thread(_acquire_file_lock)
        waited = time.monotonic() - started
        if logger is not None and waited >= 1.0:
            logger.info(
                "LM Studio single-model gate acquired after %.1fs for %s (%s)",
                waited,
                model or "default",
                purpose or "request",
            )
        yield
    finally:
        if file_handle is not None:
            await asyncio.to_thread(_release_file_lock, file_handle)
        _PROCESS_LOCK.release()


async def async_create_chat_completion_with_lmstudio_gate(
    client: Any,
    kwargs: dict,
    *,
    provider: Optional[str] = None,
    base_url: Any = None,
    model: Optional[str] = None,
    purpose: str = "",
    logger: Any = None,
):
    """Async ``chat.completions.create`` wrapper with LM Studio serialization."""
    effective_base_url = base_url if base_url is not None else getattr(client, "base_url", None)
    effective_model = model or kwargs.get("model")
    async with async_lmstudio_single_model_guard(
        provider=provider,
        base_url=effective_base_url,
        model=effective_model,
        purpose=purpose,
        logger=logger,
    ):
        return await client.chat.completions.create(**kwargs)
