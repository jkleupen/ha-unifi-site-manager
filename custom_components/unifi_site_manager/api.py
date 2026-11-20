"""Minimal async client for the UniFi Site Manager (cloud) API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import ClientError, ClientSession

from .const import API_BASE, MAX_ATTEMPTS, MAX_PAGES, REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class UnifiSiteManagerError(Exception):
    """Base error for the Site Manager API client."""


class UnifiAuthError(UnifiSiteManagerError):
    """The API key was rejected."""


class UnifiRateLimitError(UnifiSiteManagerError):
    """The API kept answering HTTP 429."""


class UnifiApiError(UnifiSiteManagerError):
    """Any other API or connection failure."""


def _retry_delay(value: str | None) -> float:
    """Turn a Retry-After header into a bounded delay in seconds."""
    try:
        delay = float(value) if value is not None else 5.0
    except ValueError:
        delay = 5.0
    return min(max(delay, 1.0), 30.0)


class SiteManagerClient:
    """Talks to https://api.ui.com using an API key."""

    def __init__(self, session: ClientSession, api_key: str) -> None:
        """Initialize the client."""
        self._session = session
        self._api_key = api_key

    async def _request(
        self, path: str, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """GET a path and return the decoded JSON object."""
        headers = {"X-API-KEY": self._api_key, "Accept": "application/json"}
        url = f"{API_BASE}{path}"

        for attempt in range(1, MAX_ATTEMPTS + 1):
            retry_delay: float | None = None
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT):
                    async with self._session.get(
                        url, headers=headers, params=params
                    ) as response:
                        if response.status == 429:
                            retry_delay = _retry_delay(
                                response.headers.get("Retry-After")
                            )
                        elif response.status in (401, 403):
                            raise UnifiAuthError(
                                f"API key rejected (HTTP {response.status})"
                            )
                        elif response.status >= 400:
                            raise UnifiApiError(
                                f"HTTP {response.status} from {path}"
                            )
                        else:
                            payload = await response.json(content_type=None)
                            if not isinstance(payload, dict):
                                raise UnifiApiError(
                                    f"Unexpected response from {path}"
                                )
                            return payload
            except TimeoutError as err:
                raise UnifiApiError(f"Timeout calling {path}") from err
            except ClientError as err:
                raise UnifiApiError(f"Error calling {path}: {err}") from err

            # Only reached after a 429.
            if attempt == MAX_ATTEMPTS:
                break
            _LOGGER.debug(
                "Rate limited on %s, retrying in %.0fs (attempt %s/%s)",
                path,
                retry_delay,
                attempt,
                MAX_ATTEMPTS,
            )
            await asyncio.sleep(retry_delay or 5.0)

        raise UnifiRateLimitError(f"Rate limited calling {path}")

    async def _get_paged(self, path: str) -> list[dict[str, Any]]:
        """GET every page of a list endpoint, following nextToken."""
        items: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(MAX_PAGES):
            params = {"nextToken": token} if token else None
            payload = await self._request(path, params)
            data = payload.get("data")
            if isinstance(data, list):
                items.extend(item for item in data if isinstance(item, dict))
            token = payload.get("nextToken")
            if not token:
                return items
        _LOGGER.warning("Stopped reading %s after %s pages", path, MAX_PAGES)
        return items

    async def validate(self) -> None:
        """Make one cheap call to confirm the key works."""
        await self._request("/v1/hosts")

    async def get_hosts(self) -> list[dict[str, Any]]:
        """Return all hosts (UniFi consoles) on the account."""
        return await self._get_paged("/v1/hosts")

    async def get_devices(self) -> list[dict[str, Any]]:
        """Return devices grouped by host: [{hostId, hostName, devices: [...]}]."""
        return await self._get_paged("/v1/devices")
