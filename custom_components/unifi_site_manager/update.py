"""Firmware update entities (read-only) for UniFi Site Manager Monitoring."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_DEVICE_ENTITIES, DEFAULT_DEVICE_ENTITIES
from .coordinator import UnifiConfigEntry, UnifiSiteManagerCoordinator
from .entity import DeviceEntity, HostEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up update entities and add new ones as devices are discovered."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        coordinator.async_sync_devices()
        data = coordinator.data
        entities: list[UpdateEntity] = []

        for host_id, host in data.hosts.items():
            if host.ignored or f"host_{host_id}" in known:
                continue
            known.add(f"host_{host_id}")
            entities.append(HostFirmwareUpdate(coordinator, host_id))

        if entry.options.get(CONF_DEVICE_ENTITIES, DEFAULT_DEVICE_ENTITIES):
            for key, dev in data.devices.items():
                if dev.ignored or f"device_{key}" in known:
                    continue
                known.add(f"device_{key}")
                entities.append(DeviceFirmwareUpdate(coordinator, key))

        if entities:
            async_add_entities(entities)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class HostFirmwareUpdate(HostEntity, UpdateEntity):
    """Console firmware: reports whether an update is available (no install)."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_name = "Firmware"

    def __init__(self, coordinator: UnifiSiteManagerCoordinator, host_id: str) -> None:
        super().__init__(coordinator, host_id)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_host_{host_id}_firmware"

    @property
    def installed_version(self) -> str | None:
        host = self.host
        return host.version if host else None

    @property
    def latest_version(self) -> str | None:
        host = self.host
        if host is None or host.version is None:
            return None
        return host.update_available or host.version

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        host = self.host
        if host is None:
            return None
        return {
            "firmware_status": host.firmware_status,
            "update_pending_since": host.update_since.isoformat()
            if host.update_since
            else None,
            "update_overdue": host.update_overdue,
        }


class DeviceFirmwareUpdate(DeviceEntity, UpdateEntity):
    """Device firmware: reports whether an update is available (no install)."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_name = "Firmware"

    def __init__(self, coordinator: UnifiSiteManagerCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_device_{key}_firmware"

    @property
    def installed_version(self) -> str | None:
        dev = self.dev
        return dev.version if dev else None

    @property
    def latest_version(self) -> str | None:
        dev = self.dev
        if dev is None or dev.version is None:
            return None
        return dev.update_available or dev.version

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        dev = self.dev
        if dev is None:
            return None
        return {
            "firmware_status": dev.firmware_status,
            "update_pending_since": dev.update_since.isoformat()
            if dev.update_since
            else None,
            "update_overdue": dev.update_overdue,
        }
