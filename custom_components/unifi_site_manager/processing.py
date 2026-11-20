"""Pure data processing for UniFi Site Manager Monitoring.

Deliberately has no Home Assistant imports so it can be unit tested on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .const import MAX_LISTED

# {id: (consecutive polls seen offline, time first seen offline)}
OfflineState = dict[str, tuple[int, datetime | None]]


@dataclass(slots=True)
class ProcessingOptions:
    """Settings that influence how raw API data is interpreted."""

    ignore_prefixes: tuple[str, ...] = ()
    confirm_polls: int = 2
    outage_devices: int = 2
    outage_minutes: int = 30


@dataclass(slots=True)
class SiteHost:
    """A UniFi console (the API calls it a host)."""

    id: str
    name: str
    ip: str | None
    connected: bool
    ignored: bool
    model: str | None = None
    version: str | None = None
    firmware_status: str | None = None
    update_available: str | None = None
    misses: int = 0
    offline_since: datetime | None = None
    confirmed_offline: bool = False
    outage: bool = False
    devices_total: int = 0
    devices_offline: int = 0
    updates_available: int = 0


@dataclass(slots=True)
class SiteDevice:
    """A non-console UniFi device (switch, AP, camera, access hub, ...)."""

    key: str
    id: str
    host_id: str
    host_name: str
    name: str
    model: str | None
    ip: str | None
    mac: str | None
    product_line: str | None
    status: str
    offline: bool
    version: str | None
    firmware_status: str | None
    update_available: str | None
    ignored: bool
    misses: int = 0
    offline_since: datetime | None = None
    confirmed_offline: bool = False


@dataclass(slots=True)
class SiteManagerData:
    """Everything the entities need, rebuilt on every poll."""

    hosts: dict[str, SiteHost] = field(default_factory=dict)
    devices: dict[str, SiteDevice] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)


def parse_prefixes(raw: str | None) -> tuple[str, ...]:
    """Parse a comma separated list of name prefixes (case-insensitive)."""
    if not raw:
        return ()
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


def is_ignored(name: str, prefixes: tuple[str, ...]) -> bool:
    """Return True if the name starts with any ignored prefix."""
    return bool(prefixes) and name.strip().lower().startswith(prefixes)


def _update_value(update: Any, firmware_status: str | None) -> str | None:
    """Normalise the API's updateAvailable field to a version string or None."""
    if update in (None, False, ""):
        if (firmware_status or "").lower() == "updateavailable":
            return "update available"
        return None
    if update is True:
        return "update available"
    return str(update)


def _track(
    previous: OfflineState, ident: str, offline: bool, now: datetime
) -> tuple[int, datetime | None]:
    """Update the consecutive-offline counter for one id."""
    misses, since = previous.get(ident, (0, None))
    if not offline:
        return 0, None
    return misses + 1, since or now


def process(
    raw_hosts: list[dict[str, Any]],
    raw_devices: list[dict[str, Any]],
    prev_hosts: OfflineState,
    prev_devices: OfflineState,
    options: ProcessingOptions,
    now: datetime,
) -> tuple[SiteManagerData, OfflineState, OfflineState]:
    """Turn raw /v1/hosts and /v1/devices output into SiteManagerData."""
    hosts: dict[str, SiteHost] = {}
    host_state: OfflineState = {}

    for raw in raw_hosts:
        host_id = raw.get("id")
        if not host_id:
            continue
        reported = raw.get("reportedState") or {}
        name = str(reported.get("hostname") or reported.get("name") or host_id)
        status = str(reported.get("state") or "").lower()
        connected = status not in ("disconnected", "offline")
        misses, since = _track(prev_hosts, host_id, not connected, now)
        host_state[host_id] = (misses, since)
        hosts[host_id] = SiteHost(
            id=host_id,
            name=name,
            ip=raw.get("ipAddress"),
            connected=connected,
            ignored=is_ignored(name, options.ignore_prefixes),
            misses=misses,
            offline_since=since,
            confirmed_offline=(not connected and misses >= options.confirm_polls),
        )

    devices: dict[str, SiteDevice] = {}
    device_state: OfflineState = {}

    for group in raw_devices:
        host_id = group.get("hostId")
        host = hosts.get(host_id) if host_id else None
        if host is None:
            continue
        for raw in group.get("devices") or []:
            if not isinstance(raw, dict):
                continue
            dev_id = raw.get("id") or raw.get("mac")
            if not dev_id:
                continue
            firmware_status = raw.get("firmwareStatus")
            update = _update_value(raw.get("updateAvailable"), firmware_status)
            version = raw.get("version")

            if raw.get("isConsole"):
                # The console's own firmware belongs on the host.
                host.model = raw.get("model") or raw.get("shortname") or host.model
                host.version = version
                host.firmware_status = firmware_status
                host.update_available = update
                continue

            name = str(
                raw.get("name") or raw.get("shortname") or raw.get("model") or dev_id
            )
            status = str(raw.get("status") or "").lower()
            offline = status in ("offline", "disconnected")
            key = f"{host_id}_{dev_id}"
            misses, since = _track(prev_devices, key, offline, now)
            device_state[key] = (misses, since)
            devices[key] = SiteDevice(
                key=key,
                id=str(dev_id),
                host_id=host_id,
                host_name=host.name,
                name=name,
                model=raw.get("model"),
                ip=raw.get("ip"),
                mac=raw.get("mac"),
                product_line=raw.get("productLine"),
                status=status,
                offline=offline,
                version=version,
                firmware_status=firmware_status,
                update_available=update,
                ignored=host.ignored or is_ignored(name, options.ignore_prefixes),
                misses=misses,
                offline_since=since,
                confirmed_offline=(offline and misses >= options.confirm_polls),
            )

    # Per-host roll-ups and site outage detection.
    cutoff = now - timedelta(minutes=options.outage_minutes)
    for host in hosts.values():
        mine = [d for d in devices.values() if d.host_id == host.id and not d.ignored]
        host.devices_total = len(mine)
        host.devices_offline = sum(1 for d in mine if d.confirmed_offline)
        host.updates_available = sum(1 for d in mine if d.update_available)
        long_down = [
            d
            for d in mine
            if d.confirmed_offline and d.offline_since and d.offline_since <= cutoff
        ]
        host.outage = (not host.ignored) and len(long_down) >= options.outage_devices

    data = SiteManagerData(hosts=hosts, devices=devices)
    data.summary = build_summary(data, now)
    return data, host_state, device_state


def build_summary(data: SiteManagerData, now: datetime) -> dict[str, Any]:
    """Account-wide counts and lists used by the aggregate entities."""
    mon_hosts = [h for h in data.hosts.values() if not h.ignored]
    mon_devs = [d for d in data.devices.values() if not d.ignored]

    consoles_offline = [h for h in mon_hosts if h.confirmed_offline]
    devs_offline = sorted(
        (d for d in mon_devs if d.confirmed_offline),
        key=lambda d: (d.offline_since or now, d.host_name, d.name),
    )
    devs_pending = [d for d in mon_devs if d.offline and not d.confirmed_offline]
    outage_hosts = [h for h in mon_hosts if h.outage]
    updates = sum(1 for d in mon_devs if d.update_available) + sum(
        1 for h in mon_hosts if h.update_available
    )

    offline_by_host: dict[str, int] = {}
    for dev in devs_offline:
        offline_by_host[dev.host_name] = offline_by_host.get(dev.host_name, 0) + 1

    if consoles_offline:
        status = "console_offline"
        badge = f"{len(consoles_offline)} console{'s' if len(consoles_offline) != 1 else ''}"
    elif devs_offline:
        status = "devices_offline"
        badge = f"{len(devs_offline)} device{'s' if len(devs_offline) != 1 else ''}"
    else:
        status = "ok"
        badge = "OK"

    def _device_item(dev: SiteDevice) -> dict[str, Any]:
        minutes = (
            int((now - dev.offline_since).total_seconds() // 60)
            if dev.offline_since
            else None
        )
        return {
            "host": dev.host_name,
            "name": dev.name,
            "model": dev.model,
            "line": dev.product_line,
            "ip": dev.ip,
            "since": dev.offline_since.isoformat() if dev.offline_since else None,
            "age_min": minutes,
        }

    def _console_item(host: SiteHost) -> dict[str, Any]:
        return {
            "host": host.name,
            "ip": host.ip,
            "since": host.offline_since.isoformat() if host.offline_since else None,
        }

    return {
        "status": status,
        "badge_text": badge,
        "consoles_total": len(mon_hosts),
        "consoles_offline": len(consoles_offline),
        "devices_total": len(mon_devs),
        "devices_offline": len(devs_offline),
        "devices_pending": len(devs_pending),
        "devices_online": len(mon_devs) - len(devs_offline) - len(devs_pending),
        "updates_available": updates,
        "ignored": sum(1 for d in data.devices.values() if d.ignored)
        + sum(1 for h in data.hosts.values() if h.ignored),
        "sites_with_outage": [h.name for h in outage_hosts],
        "offline_by_host": offline_by_host,
        "offline_devices": [_device_item(d) for d in devs_offline[:MAX_LISTED]],
        "offline_consoles": [_console_item(h) for h in consoles_offline[:MAX_LISTED]],
        "summary": "\n".join(
            [f"{h.name}: console offline" for h in consoles_offline[:MAX_LISTED]]
            + [f"{d.host_name}: {d.name}" for d in devs_offline[:MAX_LISTED]]
        ),
    }
