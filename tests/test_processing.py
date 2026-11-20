"""Tests for the pure processing logic. Run: python tests/test_processing.py"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import types

# Load the integration's modules without importing Home Assistant.
PKG_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "unifi_site_manager"
pkg = types.ModuleType("usm")
pkg.__path__ = [str(PKG_DIR)]
sys.modules["usm"] = pkg
for name in ("const", "processing"):
    spec = importlib.util.spec_from_file_location(f"usm.{name}", PKG_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"usm.{name}"] = mod
    spec.loader.exec_module(mod)

from usm.processing import ProcessingOptions, parse_prefixes, process  # noqa: E402

T0 = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

HOSTS = [
    {"id": "h1", "ipAddress": "10.0.0.1", "reportedState": {"hostname": "Client A", "state": "connected"}},
    {"id": "h2", "ipAddress": "10.0.0.2", "reportedState": {"hostname": "Client B", "state": "disconnected"}},
    {"id": "h3", "ipAddress": "10.0.0.3", "reportedState": {"hostname": "OLD Console", "state": "disconnected"}},
]


def devices(sw2_status="offline"):
    return [
        {
            "hostId": "h1",
            "hostName": "Client A",
            "devices": [
                {"id": "c1", "name": "Console A", "model": "UDM Pro", "isConsole": True,
                 "version": "5.0.1", "updateAvailable": "5.0.2", "firmwareStatus": "updateAvailable", "status": "online"},
                {"id": "sw1", "name": "Switch 1", "model": "USW", "status": "offline", "version": "7.0.0", "updateAvailable": None},
                {"id": "sw2", "name": "Switch 2", "model": "USW", "status": sw2_status, "version": "7.0.0"},
                {"id": "ap1", "name": "AP 1", "model": "U7", "status": "online", "version": "7.0.0", "updateAvailable": "7.0.2"},
                {"id": "x", "name": "Old switch", "status": "offline"},
                {"id": "y", "name": "it spare AP", "status": "offline"},
            ],
        }
    ]


def run(opts, hs, ds, now, prev=None):
    prev_h, prev_d = prev or ({}, {})
    return process(hs, ds, prev_h, prev_d, opts, now)


def main() -> None:
    opts = ProcessingOptions(
        ignore_prefixes=parse_prefixes("OLD,SPARE,IT Spare"),
        confirm_polls=2,
        outage_devices=2,
        outage_minutes=30,
    )

    # Poll 1: offline once -> pending, not confirmed.
    data, hs, ds = run(opts, HOSTS, devices(), T0)
    s = data.summary
    assert s["devices_offline"] == 0 and s["devices_pending"] == 2, s
    assert s["consoles_offline"] == 0, s
    assert s["status"] == "ok"
    assert s["ignored"] == 3, s  # OLD console, Old switch, it spare AP
    assert data.hosts["h1"].version == "5.0.1" and data.hosts["h1"].update_available == "5.0.2"
    assert s["updates_available"] == 2, s  # AP 1 + console A

    # Poll 2, 15 min later: confirmed, but no site outage yet (< 30 min).
    data, hs, ds = run(opts, HOSTS, devices(), T0 + timedelta(minutes=15), (hs, ds))
    s = data.summary
    assert s["devices_offline"] == 2 and s["devices_pending"] == 0, s
    assert s["consoles_offline"] == 1 and s["status"] == "console_offline", s
    assert not data.hosts["h1"].outage and s["sites_with_outage"] == []

    # Poll 3, 45 min after first seen: site outage on Client A.
    data, hs, ds = run(opts, HOSTS, devices(), T0 + timedelta(minutes=45), (hs, ds))
    s = data.summary
    assert data.hosts["h1"].outage and s["sites_with_outage"] == ["Client A"], s
    assert s["offline_by_host"] == {"Client A": 2}, s

    # Switch 2 recovers: only one device down -> outage clears, counters reset.
    data, hs, ds = run(opts, HOSTS, devices("online"), T0 + timedelta(minutes=60), (hs, ds))
    s = data.summary
    assert not data.hosts["h1"].outage and s["devices_offline"] == 1, s
    assert data.devices["h1_sw2"].misses == 0 and data.devices["h1_sw2"].offline_since is None

    # Ignored devices/consoles never appear in counts or lists.
    assert all(d["name"] != "Old switch" for d in s["offline_devices"])
    assert data.devices["h1_x"].ignored and data.hosts["h3"].ignored

    # Devices for unknown hosts are skipped, not crashed on.
    data, *_ = run(opts, HOSTS, [{"hostId": "nope", "devices": [{"id": "z", "status": "offline"}]}], T0)
    assert data.devices == {}

    # updateAvailable given as a bool or only via firmwareStatus.
    odd = [{"hostId": "h1", "devices": [
        {"id": "a", "name": "A", "status": "online", "version": "1", "updateAvailable": True},
        {"id": "b", "name": "B", "status": "online", "version": "1", "firmwareStatus": "updateAvailable"},
        {"id": "c", "name": "C", "status": "online", "version": "1", "firmwareStatus": "upToDate"},
    ]}]
    data, *_ = run(opts, HOSTS, odd, T0)
    assert data.devices["h1_a"].update_available == "update available"
    assert data.devices["h1_b"].update_available == "update available"
    assert data.devices["h1_c"].update_available is None

    print("all processing tests passed")


if __name__ == "__main__":
    main()
