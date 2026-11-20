"""Diagnostics for UniFi Site Manager Monitoring.

The download shows which fields the Site Manager API returns, so it is easy to
check whether something (for example per-application versions) is available.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import UnifiConfigEntry

REDACT = {"ipAddress", "ip", "mac", "id", "hostId", "wanIp", "userData", "owner"}
SAMPLE_ROWS = 3


def _keys(rows: list[Any]) -> list[str]:
    found: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            found.update(row)
    return sorted(found)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: UnifiConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics."""
    coordinator = entry.runtime_data
    hosts = coordinator.raw_hosts or []
    groups = coordinator.raw_devices or []

    rows: list[dict[str, Any]] = []
    for group in groups:
        if isinstance(group, dict):
            rows.extend(r for r in group.get("devices", []) if isinstance(r, dict))
    consoles = [r for r in rows if r.get("isConsole")]
    others = [r for r in rows if not r.get("isConsole")]

    return {
        "options": dict(entry.options),
        "counts": {
            "hosts": len(hosts),
            "device_groups": len(groups),
            "devices": len(rows),
            "console_rows": len(consoles),
        },
        "host_fields": _keys(hosts),
        "device_fields": _keys(rows),
        "sample_hosts": async_redact_data(hosts[:SAMPLE_ROWS], REDACT),
        "sample_console_rows": async_redact_data(consoles[:SAMPLE_ROWS], REDACT),
        "sample_device_rows": async_redact_data(others[:SAMPLE_ROWS], REDACT),
    }
