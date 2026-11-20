"""Tests for the pure processing logic. Run: python tests/test_processing.py"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
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

from usm.processing import (  # noqa: E402
    ProcessingOptions,
    TrackerState,
    decode_state,
    diff_events,
    encode_state,
    parse_prefixes,
    process,
)

T0 = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

HOSTS = [
    {"id": "h1", "ipAddress": "10.0.0.1", "reportedState": {"hostname": "Client A", "state": "connected"}},
    {"id": "h2", "ipAddress": "10.0.0.2", "reportedState": {"hostname": "Client B", "state": "disconnected"}},
    {"id": "h3", "ipAddress": "10.0.0.3", "reportedState": {"hostname": "OLD Console", "state": "disconnected"}},
]


def devices(sw2_status="offline", ap_update="7.0.2"):
    return [
        {
            "hostId": "h1",
            "hostName": "Client A",
            "devices": [
                {"id": "c1", "name": "Console A", "model": "UDM Pro", "isConsole": True,
                 "version": "5.0.1", "updateAvailable": "5.0.2", "firmwareStatus": "updateAvailable", "status": "online"},
                {"id": "sw1", "name": "Switch 1", "model": "USW", "status": "offline", "version": "7.0.0", "updateAvailable": None},
                {"id": "sw2", "name": "Switch 2", "model": "USW", "status": sw2_status, "version": "7.0.0"},
                {"id": "ap1", "name": "AP 1", "model": "U7", "status": "online", "version": "7.0.0", "updateAvailable": ap_update},
                {"id": "x", "name": "Old switch", "status": "offline"},
                {"id": "y", "name": "it spare AP", "status": "offline"},
            ],
        }
    ]


OPTS = ProcessingOptions(
    ignore_prefixes=parse_prefixes("OLD,SPARE,IT Spare"),
    confirm_polls=2,
    outage_devices=2,
    outage_minutes=30,
    stale_update_days=30,
)


def run(hs, ds, now, prev=None, opts=OPTS):
    return process(hs, ds, prev or TrackerState(), opts, now)


def minutes(n):
    return T0 + timedelta(minutes=n)


def test_offline_and_ignore():
    # Poll 1: offline once -> pending, not confirmed.
    data, st = run(HOSTS, devices(), T0)
    s = data.summary
    assert s["devices_offline"] == 0 and s["devices_pending"] == 2, s
    assert s["consoles_offline"] == 0 and s["status"] == "ok", s
    assert s["ignored"] == 3, s  # OLD console, Old switch, it spare AP
    assert data.hosts["h1"].version == "5.0.1" and data.hosts["h1"].update_available == "5.0.2"
    assert s["updates_available"] == 2, s  # AP 1 + console A

    # Poll 2, 15 min later: confirmed, but no site outage yet (< 30 min).
    data, st = run(HOSTS, devices(), minutes(15), st)
    s = data.summary
    assert s["devices_offline"] == 2 and s["devices_pending"] == 0, s
    assert s["consoles_offline"] == 1 and s["status"] == "console_offline", s
    assert not data.hosts["h1"].outage and s["sites_with_outage"] == []

    # Poll 3, 45 min after first seen: site outage on Client A.
    data, st = run(HOSTS, devices(), minutes(45), st)
    s = data.summary
    assert data.hosts["h1"].outage and s["sites_with_outage"] == ["Client A"], s
    assert s["offline_by_host"] == {"Client A": 2}, s

    # Switch 2 recovers: outage clears, counters reset.
    data, st = run(HOSTS, devices("online"), minutes(60), st)
    s = data.summary
    assert not data.hosts["h1"].outage and s["devices_offline"] == 1, s
    assert data.devices["h1_sw2"].misses == 0 and data.devices["h1_sw2"].offline_since is None
    assert all(d["name"] != "Old switch" for d in s["offline_devices"])
    assert data.devices["h1_x"].ignored and data.hosts["h3"].ignored


def test_odd_inputs():
    data, _ = run(HOSTS, [{"hostId": "nope", "devices": [{"id": "z", "status": "offline"}]}], T0)
    assert data.devices == {}

    odd = [{"hostId": "h1", "devices": [
        {"id": "a", "name": "A", "status": "online", "version": "1", "updateAvailable": True},
        {"id": "b", "name": "B", "status": "online", "version": "1", "firmwareStatus": "updateAvailable"},
        {"id": "c", "name": "C", "status": "online", "version": "1", "firmwareStatus": "upToDate"},
    ]}]
    data, _ = run(HOSTS, odd, T0)
    assert data.devices["h1_a"].update_available == "update available"
    assert data.devices["h1_b"].update_available == "update available"
    assert data.devices["h1_c"].update_available is None


def test_state_round_trip():
    data, st = run(HOSTS, devices(), T0)
    data, st = run(HOSTS, devices(), minutes(15), st)
    restored = decode_state(json.loads(json.dumps(encode_state(st))))
    assert restored == st
    data, _ = run(HOSTS, devices(), minutes(45), restored)
    assert data.hosts["h1"].outage, "outage must still fire after a restart"
    assert data.devices["h1_sw1"].offline_since == T0
    assert data.devices["h1_ap1"].update_since == T0, "update age must survive a restart"
    # Garbage or old-format storage is ignored, not fatal.
    assert decode_state(None) == TrackerState()
    assert decode_state({"hosts": "bad", "devices": {"a": [1]}, "updates": {"k": "nope"}}) == TrackerState()
    old_format = {"hosts": {"h1": [0, None]}, "devices": {}}
    assert decode_state(old_format).updates == {}


def test_events():
    d1, st = run(HOSTS, devices(), T0)
    assert diff_events(None, d1) == []
    d2, st = run(HOSTS, devices(), minutes(15), st)
    assert diff_events(d1, d2) == [
        ("console_offline", {"consoles": [{"name": "Client B", "ip": "10.0.0.2", "since": T0.isoformat()}]})
    ], diff_events(d1, d2)
    d3, st = run(HOSTS, devices(), minutes(45), st)
    assert diff_events(d2, d3) == [
        ("site_outage", {"site": "Client A", "count": 2, "minutes": 45, "devices": "Switch 1, Switch 2"})
    ], diff_events(d2, d3)
    assert d3.summary["sites_down"] == [
        {"site": "Client A", "count": 2, "minutes": 45, "devices": ["Switch 1", "Switch 2"]}
    ]
    assert d3.summary["offline_consoles"][0]["name"] == "Client B"
    assert diff_events(d3, d3) == []  # steady state: no repeat alerts
    d4, st = run(HOSTS, devices("online"), minutes(60), st)
    assert diff_events(d3, d4) == [("site_recovered", {"site": "Client A"})]
    back = [dict(h) for h in HOSTS]
    back[1] = {**back[1], "reportedState": {"hostname": "Client B", "state": "connected"}}
    d5, st = run(back, devices("online"), minutes(75), st)
    assert diff_events(d4, d5) == [("console_recovered", {"consoles": [{"name": "Client B"}]})]


def test_stale_updates():
    day = timedelta(days=1)
    d1, st = run(HOSTS, devices(), T0)
    assert d1.summary["updates_overdue"] == 0 and d1.summary["oldest_update_days"] == 0
    assert d1.devices["h1_ap1"].update_since == T0

    pend = d1.summary["pending_updates"]
    assert len(pend) == d1.summary["updates_available"], pend
    assert {"site", "name", "model", "installed", "latest", "since", "days", "overdue"} <= set(pend[0])
    assert not any(i["overdue"] for i in pend)

    # 29 days later: still within the 30 day window.
    d2, st = run(HOSTS, devices(), T0 + 29 * day, st)
    assert d2.summary["updates_overdue"] == 0 and d2.summary["oldest_update_days"] == 29
    assert not any(e[0] == "updates_overdue" for e in diff_events(d1, d2))

    # 31 days later: AP 1 and Console A are overdue; one event lists both.
    d3, st = run(HOSTS, devices(), T0 + 31 * day, st)
    assert d3.summary["updates_overdue"] == 2, d3.summary
    assert d3.hosts["h1"].updates_overdue == 1 and d3.hosts["h1"].update_overdue
    assert sum(i["overdue"] for i in d3.summary["pending_updates"]) == 2
    events = [e for e in diff_events(d2, d3) if e[0] == "updates_overdue"]
    assert len(events) == 1 and events[0][1]["count"] == 2, events
    assert {i["name"] for i in events[0][1]["devices"]} == {"AP 1", "Client A (console)"}
    assert all(i["days"] == 31 for i in events[0][1]["devices"])

    # Steady state: no repeat event while they stay overdue.
    d4, st = run(HOSTS, devices(), T0 + 32 * day, st)
    assert [e for e in diff_events(d3, d4) if e[0] == "updates_overdue"] == []

    # AP 1 gets updated: its timer clears and overdue drops to the console only.
    d5, st = run(HOSTS, devices(ap_update=None), T0 + 33 * day, st)
    assert d5.devices["h1_ap1"].update_since is None and not d5.devices["h1_ap1"].update_overdue
    assert d5.summary["updates_overdue"] == 1

    # A newer version appearing keeps the original pending-since (age of the oldest wait).
    d6, st = run(HOSTS, devices(ap_update="7.0.3"), T0 + 34 * day, st)
    assert d6.devices["h1_ap1"].update_since == T0 + 34 * day  # new wait started after clearing

    # The threshold is configurable.
    short = ProcessingOptions(ignore_prefixes=OPTS.ignore_prefixes, stale_update_days=5)
    s1, sst = run(HOSTS, devices(), T0, opts=short)
    s2, sst = run(HOSTS, devices(), T0 + 6 * day, sst, opts=short)
    assert s2.summary["updates_overdue"] == 2


def test_app_updates():
    day = timedelta(days=1)
    hosts = [
        {
            "id": "h1",
            "ipAddress": "10.0.0.1",
            "reportedState": {
                "hostname": "Client A",
                "state": "connected",
                "controllers": [
                    {"name": "network", "isInstalled": True, "version": "11.0.99", "updateAvailable": "11.0.103"},
                    {"name": "protect", "isInstalled": False, "version": "", "updateAvailable": None},
                    {"name": "access", "isInstalled": True, "version": "4.0", "updateAvailable": None},
                    "junk",
                ],
            },
        },
        {
            "id": "h3",
            "ipAddress": "10.0.0.3",
            "reportedState": {
                "hostname": "OLD Console",
                "state": "connected",
                "controllers": [{"name": "network", "isInstalled": True, "version": "1", "updateAvailable": "2"}],
            },
        },
    ]
    d1, st = run(hosts, [], T0)
    apps = d1.hosts["h1"].apps
    assert [a.name for a in apps] == ["network"] and apps[0].latest == "11.0.103"
    assert d1.hosts["h1"].updates_available == 1
    assert d1.summary["updates_available"] == 1, d1.summary  # ignored console excluded
    pend = d1.summary["pending_updates"]
    assert len(pend) == 1 and pend[0]["name"] == "Network (application)", pend
    assert pend[0]["installed"] == "11.0.99" and pend[0]["latest"] == "11.0.103"

    d2, st = run(hosts, [], T0 + 31 * day, st)
    assert d2.summary["updates_overdue"] == 1 and d2.hosts["h1"].updates_overdue == 1
    events = [e for e in diff_events(d1, d2) if e[0] == "updates_overdue"]
    assert len(events) == 1 and events[0][1]["devices"][0]["name"] == "Network (application)"

    av = d1.summary["app_versions"]
    assert [i["site"] for i in av] == ["Client A"], av  # ignored console excluded
    names = {a["name"]: a for a in av[0]["apps"]}
    assert set(names) == {"network", "access"}, names
    assert names["network"]["update"] == "11.0.103" and names["access"]["update"] is None
    assert d1.summary["apps_installed"] == 2

    # Updated: the pending entry clears.
    hosts[0]["reportedState"]["controllers"][0]["updateAvailable"] = None
    d3, st = run(hosts, [], T0 + 32 * day, st)
    assert d3.summary["updates_available"] == 0 and not d3.hosts["h1"].apps


def main() -> None:
    test_offline_and_ignore()
    test_odd_inputs()
    test_state_round_trip()
    test_events()
    test_stale_updates()
    test_app_updates()
    print("all processing tests passed")


if __name__ == "__main__":
    main()
