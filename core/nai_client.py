"""Standard-library client for Nai2API-compatible stations."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .pricing import cost_for_size


class NaiAPIError(RuntimeError):
    """An API failure with secrets removed from its message."""


class NaiClient:
    def __init__(self, base_url: str, request_timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.request_timeout = request_timeout

    def _request_sync(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        body: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> tuple[dict[str, Any] | bytes, str]:
        headers = {"Accept": "application/json"}
        if token:
            headers["x-user-token"] = token
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.request_timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read(1024).decode("utf-8", "replace")
            except OSError:
                pass
            raise NaiAPIError(f"站点请求失败（HTTP {exc.code}）: {detail[:300]}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise NaiAPIError(f"无法连接站点: {str(reason)[:200]}") from None
        if "application/json" in content_type or payload[:1] in (b"{", b"["):
            try:
                parsed = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise NaiAPIError("站点返回了无效 JSON") from None
            if not isinstance(parsed, dict):
                raise NaiAPIError("站点返回的数据格式无效")
            return parsed, content_type
        return payload, content_type

    async def _request(self, method: str, path: str, **kwargs: Any) -> tuple[dict[str, Any] | bytes, str]:
        return await asyncio.to_thread(self._request_sync, method, path, **kwargs)

    async def settings(self) -> dict[str, Any]:
        result, _ = await self._request("GET", "/api/settings")
        assert isinstance(result, dict)
        return result

    async def me(self, token: str) -> dict[str, Any]:
        result, _ = await self._request("GET", "/api/me", token=token)
        assert isinstance(result, dict)
        return result

    async def submit_job(
        self,
        token: str,
        *,
        tag: str,
        artist: str,
        model: str,
        size: str,
        steps: int,
        scale: float = 6,
        cfg: float = 0,
        sampler: str = "k_dpmpp_2m_sde",
        negative: str = "",
        noise_schedule: str = "karras",
    ) -> dict[str, Any]:
        steps = max(1, min(50, int(steps)))
        cost = cost_for_size(size, model, steps)
        body = {
            "token": token,
            "tag": tag,
            "artist": artist,
            "model": model,
            "size": size,
            "cost": cost,
            "steps": steps,
            "scale": float(scale),
            "cfg": float(cfg),
            "sampler": sampler,
            "negative": negative,
            "nocache": "1",
            "noise_schedule": noise_schedule,
        }
        result, _ = await self._request("POST", "/api/web/jobs", token=token, body=body, timeout=30)
        assert isinstance(result, dict)
        if not result.get("id"):
            raise NaiAPIError("站点未返回任务编号")
        return result

    async def poll_job(
        self,
        token: str,
        job_id: str,
        *,
        poll_interval: float = 3.0,
        timeout: float = 300.0,
        max_server_errors: int = 5,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        server_errors = 0
        while time.monotonic() < deadline:
            try:
                result, _ = await self._request("GET", f"/api/jobs/{urllib.parse.quote(job_id, safe='')}", token=token)
                assert isinstance(result, dict)
                server_errors = 0
            except NaiAPIError as exc:
                if "HTTP 5" not in str(exc):
                    raise
                server_errors += 1
                if server_errors >= max_server_errors:
                    raise NaiAPIError("站点连续返回服务端错误，已停止轮询") from None
                await asyncio.sleep(poll_interval)
                continue
            status = result.get("status")
            if status == "done":
                return result
            if status == "failed":
                raise NaiAPIError(str(result.get("error") or "生成失败")[:300])
            if status not in ("queued", "running"):
                raise NaiAPIError("站点返回了未知任务状态")
            await asyncio.sleep(poll_interval)
        raise NaiAPIError("任务等待超时")

    async def download_image(self, token: str, image_url: str) -> str:
        parsed = urllib.parse.urlsplit(image_url)
        if parsed.scheme or parsed.netloc:
            if parsed.scheme != "https" or parsed.hostname != urllib.parse.urlsplit(self.base_url).hostname:
                raise NaiAPIError("站点返回了不安全的图片地址")
            path = parsed.path + (("?" + parsed.query) if parsed.query else "")
        else:
            if not image_url.startswith("/"):
                raise NaiAPIError("站点返回了无效的图片地址")
            path = image_url
        payload, _ = await self._request("GET", path, token=token, timeout=60)
        if not isinstance(payload, bytes) or not payload:
            raise NaiAPIError("图片下载失败")
        suffix = ".png" if payload.startswith(b"\x89PNG\r\n\x1a\n") else ".jpg"
        descriptor, filename = tempfile.mkstemp(prefix="nai-draw-", suffix=suffix)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
        except OSError:
            try:
                os.unlink(filename)
            except OSError:
                pass
            raise NaiAPIError("无法保存生成图片") from None
        return filename

    async def generate(self, token: str, *, timeout: float = 300.0, **params: Any) -> tuple[dict[str, Any], str]:
        job = await self.submit_job(token, **params)
        finished = await self.poll_job(token, str(job["id"]), timeout=timeout)
        image_url = finished.get("imageUrl")
        if not image_url:
            raise NaiAPIError("任务完成但站点未返回图片地址")
        filename = await self.download_image(token, str(image_url))
        return finished, filename
