"""Pure data processing for UniFi Site Manager Monitoring.

Deliberately has no Home Assistant imports so it can be unit tested on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .const import EVENT_MAX_ITEMS, MAX_LISTED

# {id: (consecutive polls seen offline, time first seen offline)}
OfflineState = dict[str, tuple[int, datetime | None]]
# {id: time a firmware update was first seen pending}
PendingState = dict[str, datetime]


@dataclass(slots=True)
class TrackerState:
    """Counters and timers carried from one poll to the next (and saved to disk)."""

    hosts: OfflineState = field(default_factory=dict)
    devices: OfflineState = field(default_factory=dict)
    updates: PendingState = field(default_factory=dict)


@dataclass(slots=True)
class ProcessingOptions:
    """Settings that influence how raw API data is interpreted."""

    ignore_prefixes: tuple[str, ...] = ()
    confirm_polls: int = 2
    outage_devices: int = 2
    outage_minutes: int = 30
    stale_update_days: int = 30


@dataclass(slots=True)
class AppUpdate:
    """A pending update for an application (Network, Protect, ...) on a console."""

    key: str
    name: str
    installed: str | None
    latest: str
    since: datetime
    overdue: bool


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
    update_since: datetime | None = None
    update_overdue: bool = False
    misses: int = 0
    offline_since: datetime | None = None
    confirmed_offline: bool = False
    outage: bool = False
    outage_devices: list[str] = field(default_factory=list)
    outage_minutes: int = 0
    apps: list[AppUpdate] = field(default_factory=list)
    installed: list[dict[str, Any]] = field(default_factory=list)
    ui_name: str | None = None
    devices_total: int = 0
    devices_offline: int = 0
    updates_available: int = 0
    updates_overdue: int = 0


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
    update_since: datetime | None = None
    update_overdue: bool = False


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


def _encode_offline(state: OfflineState) -> dict[str, list[Any]]:
    return {
        key: [misses, since.isoformat() if since else None]
        for key, (misses, since) in state.items()
    }


def _decode_offline(raw: Any) -> OfflineState:
    result: OfflineState = {}
    if not isinstance(raw, dict):
        return result
    for key, value in raw.items():
        try:
            misses = int(value[0])
            since = datetime.fromisoformat(value[1]) if value[1] else None
        except (TypeError, ValueError, IndexError):
            continue
        result[str(key)] = (misses, since)
    return result


def encode_state(state: TrackerState) -> dict[str, Any]:
    """Turn the tracker state into JSON-safe data for storage."""
    return {
        "hosts": _encode_offline(state.hosts),
        "devices": _encode_offline(state.devices),
        "updates": {key: since.isoformat() for key, since in state.updates.items()},
    }


def decode_state(raw: Any) -> TrackerState:
    """Rebuild the tracker state from stored data, skipping anything malformed."""
    if not isinstance(raw, dict):
        return TrackerState()
    updates: PendingState = {}
    raw_updates = raw.get("updates")
    if isinstance(raw_updates, dict):
        for key, value in raw_updates.items():
            try:
                updates[str(key)] = datetime.fromisoformat(value)
            except (TypeError, ValueError):
                continue
    return TrackerState(
        hosts=_decode_offline(raw.get("hosts")),
        devices=_decode_offline(raw.get("devices")),
        updates=updates,
    )


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


def _pending(
    previous: PendingState,
    current: PendingState,
    ident: str,
    update: str | None,
    now: datetime,
) -> datetime | None:
    """When was this item's update first seen? Clears once no update is pending."""
    if not update:
        return None
    since = previous.get(ident) or now
    current[ident] = since
    return since


def _installed_apps(controllers: Any) -> list[dict[str, Any]]:
    """Installed applications with their version and any pending update."""
    result: list[dict[str, Any]] = []
    if not isinstance(controllers, list):
        return result
    for ctrl in controllers:
        if not isinstance(ctrl, dict) or not ctrl.get("isInstalled"):
            continue
        name = str(ctrl.get("name") or "").strip()
        if not name:
            continue
        version = ctrl.get("version")
        result.append(
            {
                "name": name,
                "version": str(version) if version else None,
                "update": _update_value(ctrl.get("updateAvailable"), None),
                "running": bool(version),
            }
        )
    return result


def _app_updates(
    controllers: Any,
    host_id: str,
    prev: TrackerState,
    state: TrackerState,
    stale_cutoff: datetime,
    now: datetime,
) -> list[AppUpdate]:
    """Pending updates for installed applications, from a console's controllers."""
    found: list[AppUpdate] = []
    if not isinstance(controllers, list):
        return found
    for ctrl in controllers:
        if not isinstance(ctrl, dict) or not ctrl.get("isInstalled"):
            continue
        app = str(ctrl.get("name") or "").strip()
        update = _update_value(ctrl.get("updateAvailable"), None)
        if not app or not update:
            continue
        if not ctrl.get("version"):
            continue  # installed but not running: no version reported, so skip it
        key = f"app:{host_id}:{app}"
        since = _pending(prev.updates, state.updates, key, update, now)
        if since is None:
            continue
        version = ctrl.get("version")
        found.append(
            AppUpdate(
                key=key,
                name=app,
                installed=str(version) if version else None,
                latest=update,
                since=since,
                overdue=since <= stale_cutoff,
            )
        )
    return found


def process(
    raw_hosts: list[dict[str, Any]],
    raw_devices: list[dict[str, Any]],
    prev: TrackerState,
    options: ProcessingOptions,
    now: datetime,
) -> tuple[SiteManagerData, TrackerState]:
    """Turn raw /v1/hosts and /v1/devices output into SiteManagerData."""
    state = TrackerState()
    stale_cutoff = now - timedelta(days=options.stale_update_days)

    hosts: dict[str, SiteHost] = {}
    for raw in raw_hosts:
        host_id = raw.get("id")
        if not host_id:
            continue
        reported = raw.get("reportedState") or {}
        name = str(reported.get("name") or reported.get("hostname") or host_id).strip()
        status = str(reported.get("state") or "").lower()
        connected = status not in ("disconnected", "offline")
        misses, since = _track(prev.hosts, host_id, not connected, now)
        state.hosts[host_id] = (misses, since)
        hosts[host_id] = SiteHost(
            id=host_id,
            name=name,
            ui_name=str(reported.get("name") or "").strip() or None,
            ip=raw.get("ipAddress"),
            connected=connected,
            ignored=is_ignored(name, options.ignore_prefixes),
            misses=misses,
            offline_since=since,
            confirmed_offline=(not connected and misses >= options.confirm_polls),
        )
        hosts[host_id].apps = _app_updates(
            reported.get("controllers"), host_id, prev, state, stale_cutoff, now
        )
        hosts[host_id].installed = _installed_apps(reported.get("controllers"))

    devices: dict[str, SiteDevice] = {}
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
                host.update_since = _pending(
                    prev.updates, state.updates, f"host:{host_id}", update, now
                )
                host.update_overdue = bool(
                    host.update_since and host.update_since <= stale_cutoff
                )
                continue

            name = str(
                raw.get("name") or raw.get("shortname") or raw.get("model") or dev_id
            )
            status = str(raw.get("status") or "").lower()
            offline = status in ("offline", "disconnected")
            key = f"{host_id}_{dev_id}"
            misses, since = _track(prev.devices, key, offline, now)
            state.devices[key] = (misses, since)
            update_since = _pending(prev.updates, state.updates, key, update, now)
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
                update_since=update_since,
                update_overdue=bool(update_since and update_since <= stale_cutoff),
            )

    # Per-host roll-ups and site outage detection.
    cutoff = now - timedelta(minutes=options.outage_minutes)
    for host in hosts.values():
        mine = [d for d in devices.values() if d.host_id == host.id and not d.ignored]
        host.devices_total = len(mine)
        host.devices_offline = sum(1 for d in mine if d.confirmed_offline)
        host.updates_available = sum(1 for d in mine if d.update_available) + len(
            host.apps
        )
        host.updates_overdue = sum(1 for d in mine if d.update_overdue) + sum(
            1 for a in host.apps if a.overdue
        )
        long_down = [
            d
            for d in mine
            if d.confirmed_offline and d.offline_since and d.offline_since <= cutoff
        ]
        host.outage = (not host.ignored) and len(long_down) >= options.outage_devices
        if host.outage:
            host.outage_devices = sorted(d.name for d in long_down)
            host.outage_minutes = int(
                (now - min(d.offline_since for d in long_down if d.offline_since))
                .total_seconds()
                // 60
            )

    data = SiteManagerData(hosts=hosts, devices=devices)
    data.summary = build_summary(data, now)
    return data, state


def diff_events(
    prev: SiteManagerData | None, curr: SiteManagerData
) -> list[tuple[str, dict[str, Any]]]:
    """Work out which alert events to fire between two consecutive polls.

    Returns (event suffix, payload) pairs. Nothing fires on the first poll
    after startup (prev is None), so a restart never re-sends old alerts.
    """
    if prev is None:
        return []

    went_down: list[dict[str, Any]] = []
    came_back: list[dict[str, Any]] = []
    site_events: list[tuple[str, dict[str, Any]]] = []

    for host in curr.hosts.values():
        if host.ignored:
            continue
        old = prev.hosts.get(host.id)
        was_down = bool(old and not old.ignored and old.confirmed_offline)
        was_outage = bool(old and not old.ignored and old.outage)

        if host.confirmed_offline and not was_down:
            went_down.append(
                {
                    "name": host.name,
                    "ip": host.ip,
                    "since": host.offline_since.isoformat()
                    if host.offline_since
                    else None,
                }
            )
        elif was_down and not host.confirmed_offline:
            came_back.append({"name": host.name})

        if host.outage and not was_outage:
            site_events.append(
                (
                    "site_outage",
                    {
                        "site": host.name,
                        "count": len(host.outage_devices),
                        "minutes": host.outage_minutes,
                        "devices": ", ".join(host.outage_devices),
                    },
                )
            )
        elif was_outage and not host.outage:
            site_events.append(("site_recovered", {"site": host.name}))

    events: list[tuple[str, dict[str, Any]]] = []
    if went_down:
        events.append(("console_offline", {"consoles": went_down}))
    if came_back:
        events.append(("console_recovered", {"consoles": came_back}))
    events.extend(site_events)

    # Firmware updates that just crossed the "overdue" threshold.
    curr_overdue: dict[str, dict[str, Any]] = curr.summary.get("overdue_by_key", {})
    prev_overdue: dict[str, dict[str, Any]] = prev.summary.get("overdue_by_key", {})
    newly = [item for key, item in curr_overdue.items() if key not in prev_overdue]
    if newly:
        newly.sort(key=lambda item: -item["days"])
        events.append(
            (
                "updates_overdue",
                {"count": len(newly), "devices": newly[:EVENT_MAX_ITEMS]},
            )
        )

    return events


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
    updates = (
        sum(1 for d in mon_devs if d.update_available)
        + sum(1 for h in mon_hosts if h.update_available)
        + sum(len(h.apps) for h in mon_hosts)
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
        minutes = (
            int((now - host.offline_since).total_seconds() // 60)
            if host.offline_since
            else None
        )
        return {
            "name": host.name,
            "ip": host.ip,
            "since": host.offline_since.isoformat() if host.offline_since else None,
            "age_min": minutes,
        }

    def _site_item(host: SiteHost) -> dict[str, Any]:
        return {
            "site": host.name,
            "count": len(host.outage_devices),
            "minutes": host.outage_minutes,
            "devices": list(host.outage_devices),
        }

    def _update_item(
        site: str,
        name: str,
        model: str | None,
        installed: str | None,
        latest: str | None,
        since: datetime,
    ) -> dict[str, Any]:
        return {
            "site": site,
            "name": name,
            "model": model,
            "installed": installed,
            "latest": latest,
            "since": since.isoformat(),
            "days": int((now - since).total_seconds() // 86400),
        }

    # Pending firmware updates and how long each has been waiting.
    pending: dict[str, tuple[bool, dict[str, Any]]] = {}
    for dev in mon_devs:
        if dev.update_since:
            pending[dev.key] = (
                dev.update_overdue,
                _update_item(
                    dev.host_name,
                    dev.name,
                    dev.model,
                    dev.version,
                    dev.update_available,
                    dev.update_since,
                ),
            )
    for host in mon_hosts:
        if host.update_since:
            pending[f"host:{host.id}"] = (
                host.update_overdue,
                _update_item(
                    host.name,
                    f"{host.name} (console)",
                    host.model,
                    host.version,
                    host.update_available,
                    host.update_since,
                ),
            )
    for host in mon_hosts:
        for app in host.apps:
            pending[app.key] = (
                app.overdue,
                _update_item(
                    host.name,
                    f"{app.name.capitalize()} (application)",
                    "Application",
                    app.installed,
                    app.latest,
                    app.since,
                ),
            )
    overdue_by_key = {key: item for key, (over, item) in pending.items() if over}
    pending_items = sorted(
        ({**item, "overdue": over} for over, item in pending.values()),
        key=lambda i: (i["site"].lower(), -i["days"], i["name"].lower()),
    )
    overdue_items = sorted(overdue_by_key.values(), key=lambda i: -i["days"])
    oldest_days = max((item["days"] for _, item in pending.values()), default=0)

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
        "updates_overdue": len(overdue_by_key),
        "oldest_update_days": oldest_days,
        "overdue_updates": overdue_items[:MAX_LISTED],
        "pending_updates": pending_items,
        "app_versions": sorted(
            (
                {"site": h.name, "ui_name": h.ui_name, "os": h.version, "apps": h.installed}
                for h in mon_hosts
            ),
            key=lambda item: item["site"].lower(),
        ),
        "apps_installed": sum(len(h.installed) for h in mon_hosts),
        "overdue_by_key": overdue_by_key,
        "ignored": sum(1 for d in data.devices.values() if d.ignored)
        + sum(1 for h in data.hosts.values() if h.ignored),
        "sites_with_outage": [h.name for h in outage_hosts],
        "sites_down": [_site_item(h) for h in outage_hosts],
        "offline_by_host": offline_by_host,
        "offline_devices": [_device_item(d) for d in devs_offline[:MAX_LISTED]],
        "offline_consoles": [_console_item(h) for h in consoles_offline[:MAX_LISTED]],
        "summary": "\n".join(
            [f"{h.name}: console offline" for h in consoles_offline[:MAX_LISTED]]
            + [f"{d.host_name}: {d.name}" for d in devs_offline[:MAX_LISTED]]
        ),
    }
