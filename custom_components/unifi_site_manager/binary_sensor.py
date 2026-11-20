"""Binary sensors for UniFi Site Manager Monitoring."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_DEVICE_ENTITIES, DEFAULT_DEVICE_ENTITIES
from .coordinator import UnifiConfigEntry, UnifiSiteManagerCoordinator
from .entity import AccountEntity, DeviceEntity, HostEntity


@dataclass(frozen=True, kw_only=True)
class AccountBinaryDescription(BinarySensorEntityDescription):
    """Describes an account-wide problem sensor."""

    is_on_fn: Callable[[dict[str, Any]], bool]
    attr_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


ACCOUNT_BINARY_SENSORS: tuple[AccountBinaryDescription, ...] = (
    AccountBinaryDescription(
        key="console_offline",
        name="Console offline",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda s: s["consoles_offline"] > 0,
        attr_fn=lambda s: {"consoles": s["offline_consoles"]},
    ),
    AccountBinaryDescription(
        key="device_offline",
        name="Device offline",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda s: s["devices_offline"] > 0,
        attr_fn=lambda s: {"devices": s["offline_devices"]},
    ),
    AccountBinaryDescription(
        key="site_outage",
        name="Site outage",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda s: len(s["sites_with_outage"]) > 0,
        attr_fn=lambda s: {"sites": s["sites_with_outage"]},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors and add new ones as devices are discovered."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        coordinator.async_sync_devices()
        data = coordinator.data
        entities: list[BinarySensorEntity] = []

        if "account" not in known:
            known.add("account")
            entities.extend(
                AccountBinarySensor(coordinator, desc) for desc in ACCOUNT_BINARY_SENSORS
            )

        for host_id, host in data.hosts.items():
            if host.ignored or f"host_{host_id}" in known:
                continue
            known.add(f"host_{host_id}")
            entities.append(HostConnectivityBinarySensor(coordinator, host_id))
            entities.append(HostOutageBinarySensor(coordinator, host_id))

        if entry.options.get(CONF_DEVICE_ENTITIES, DEFAULT_DEVICE_ENTITIES):
            for key, dev in data.devices.items():
                if dev.ignored or f"device_{key}" in known:
                    continue
                known.add(f"device_{key}")
                entities.append(DeviceConnectivityBinarySensor(coordinator, key))

        if entities:
            async_add_entities(entities)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class AccountBinarySensor(AccountEntity, BinarySensorEntity):
    """Account-wide problem indicator (console offline, device offline, outage)."""

    entity_description: AccountBinaryDescription

    def __init__(
        self,
        coordinator: UnifiSiteManagerCoordinator,
        description: AccountBinaryDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_account_{description.key}"
        )

    @property
    def is_on(self) -> bool:
        return self.entity_description.is_on_fn(self.coordinator.data.summary)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attr_fn
        return fn(self.coordinator.data.summary) if fn else None


class HostConnectivityBinarySensor(HostEntity, BinarySensorEntity):
    """Whether a console is connected to the UniFi cloud."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_name = "Connectivity"

    def __init__(self, coordinator: UnifiSiteManagerCoordinator, host_id: str) -> None:
        super().__init__(coordinator, host_id)
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_host_{host_id}_connectivity"
        )

    @property
    def is_on(self) -> bool | None:
        host = self.host
        return host.connected if host else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        host = self.host
        if host is None:
            return None
        return {
            "ip": host.ip,
            "confirmed_offline": host.confirmed_offline,
            "offline_since": host.offline_since.isoformat() if host.offline_since else None,
        }


class HostOutageBinarySensor(HostEntity, BinarySensorEntity):
    """On when several devices at a site have been down for a while."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "Site outage"

    def __init__(self, coordinator: UnifiSiteManagerCoordinator, host_id: str) -> None:
        super().__init__(coordinator, host_id)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_host_{host_id}_outage"

    @property
    def is_on(self) -> bool | None:
        host = self.host
        return host.outage if host else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        host = self.host
        if host is None:
            return None
        return {"devices_offline": host.devices_offline, "devices_total": host.devices_total}


class DeviceConnectivityBinarySensor(DeviceEntity, BinarySensorEntity):
    """Whether a switch, AP, camera or other device is online."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_name = "Connectivity"

    def __init__(self, coordinator: UnifiSiteManagerCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_device_{key}_connectivity"

    @property
    def is_on(self) -> bool | None:
        dev = self.dev
        return (not dev.offline) if dev else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        dev = self.dev
        if dev is None:
            return None
        return {
            "status": dev.status,
            "host": dev.host_name,
            "ip": dev.ip,
            "product_line": dev.product_line,
            "confirmed_offline": dev.confirmed_offline,
            "offline_since": dev.offline_since.isoformat() if dev.offline_since else None,
        }
