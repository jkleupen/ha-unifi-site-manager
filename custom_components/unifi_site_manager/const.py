"""Constants for UniFi Site Manager Monitoring."""

from __future__ import annotations

DOMAIN = "unifi_site_manager"

API_BASE = "https://api.ui.com"
REQUEST_TIMEOUT = 30  # seconds per request
MAX_ATTEMPTS = 3  # attempts per request when rate limited (HTTP 429)
MAX_PAGES = 50  # safety cap when following nextToken pagination

ACCOUNT_DEVICE_ID = "account"

# Options
CONF_SCAN_INTERVAL = "scan_interval"  # minutes
CONF_IGNORE_PREFIXES = "ignore_prefixes"
CONF_CONFIRM_POLLS = "confirm_polls"
CONF_OUTAGE_DEVICES = "outage_devices"
CONF_OUTAGE_MINUTES = "outage_minutes"
CONF_DEVICE_ENTITIES = "device_entities"

DEFAULT_SCAN_INTERVAL = 15
DEFAULT_IGNORE_PREFIXES = "OLD,SPARE,IT Spare"
DEFAULT_CONFIRM_POLLS = 2
DEFAULT_OUTAGE_DEVICES = 2
DEFAULT_OUTAGE_MINUTES = 30
DEFAULT_DEVICE_ENTITIES = True

# Cap on the number of offline items listed in entity attributes.
MAX_LISTED = 50
