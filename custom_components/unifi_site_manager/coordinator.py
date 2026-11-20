"""Data update coordinator for UniFi Site Manager Monitoring."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    SiteManagerClient,
    UnifiApiError,
    UnifiAuthError,
    UnifiRateLimitError,
)
from .const import (
    ACCOUNT_DEVICE_ID,
    CONF_CONFIRM_POLLS,
    CONF_IGNORE_PREFIXES,
    CONF_OUTAGE_DEVICES,
    CONF_OUTAGE_MINUTES,
    CONF_SCAN_INTERVAL,
    DEFAULT_CONFIRM_POLLS,
    DEFAULT_IGNORE_PREFIXES,
    DEFAULT_OUTAGE_DEVICES,
    DEFAULT_OUTAGE_MINUTES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .processing import (
    OfflineState,
    ProcessingOptions,
    SiteManagerData,
    parse_prefixes,
    process,
)

_LOGGER = logging.getLogger(__name__)


def host_device_id(host_id: str) -> str:
    """Device registry identifier for a console."""
    return f"host_{host_id}"


def device_device_id(key: str) -> str:
    """Device registry identifier for a non-console device."""
    return f"device_{key}"


class UnifiSiteManagerCoordinator(DataUpdateCoordinator[SiteManagerData]):
    """Polls the Site Manager API and keeps offline counters between polls."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: SiteManagerClient,
    ) -> None:
        """Initialize the coordinator."""
        minutes = int(config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=timedelta(minutes=minutes),
        )
        self._client = client
        self._host_state: OfflineState = {}
        self._device_state: OfflineState = {}

    def _options(self) -> ProcessingOptions:
        opts = self.config_entry.options
        return ProcessingOptions(
            ignore_prefixes=parse_prefixes(
                opts.get(CONF_IGNORE_PREFIXES, DEFAULT_IGNORE_PREFIXES)
            ),
            confirm_polls=int(opts.get(CONF_CONFIRM_POLLS, DEFAULT_CONFIRM_POLLS)),
            outage_devices=int(opts.get(CONF_OUTAGE_DEVICES, DEFAULT_OUTAGE_DEVICES)),
            outage_minutes=int(opts.get(CONF_OUTAGE_MINUTES, DEFAULT_OUTAGE_MINUTES)),
        )

    async def _async_update_data(self) -> SiteManagerData:
        """Fetch hosts and devices, then work out what is offline."""
        try:
            raw_hosts = await self._client.get_hosts()
            raw_devices = await self._client.get_devices()
        except UnifiAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except UnifiRateLimitError as err:
            raise UpdateFailed(f"Rate limited by the Site Manager API: {err}") from err
        except UnifiApiError as err:
            raise UpdateFailed(str(err)) from err

        data, self._host_state, self._device_state = process(
            raw_hosts,
            raw_devices,
            self._host_state,
            self._device_state,
            self._options(),
            dt_util.utcnow(),
        )
        return data

    @callback
    def async_sync_devices(self) -> None:
        """Make sure the account and console devices exist in the registry.

        Per-device entities reference their console with via_device, so the
        consoles must be registered first. Safe to call repeatedly.
        """
        registry = dr.async_get(self.hass)
        entry_id = self.config_entry.entry_id
        registry.async_get_or_create(
            config_entry_id=entry_id,
            identifiers={(DOMAIN, ACCOUNT_DEVICE_ID)},
            name="UniFi Site Manager",
            manufacturer="Ubiquiti",
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        if self.data is None:
            return
        for host in self.data.hosts.values():
            if host.ignored:
                continue
            registry.async_get_or_create(
                config_entry_id=entry_id,
                identifiers={(DOMAIN, host_device_id(host.id))},
                name=host.name,
                manufacturer="Ubiquiti",
                model=host.model,
                sw_version=host.version,
            )


type UnifiConfigEntry = ConfigEntry[UnifiSiteManagerCoordinator]
