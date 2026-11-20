"""Sensors for UniFi Site Manager Monitoring."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import UnifiConfigEntry, UnifiSiteManagerCoordinator
from .entity import AccountEntity, HostEntity
from .processing import SiteHost


@dataclass(frozen=True, kw_only=True)
class AccountSensorDescription(SensorEntityDescription):
    """Describes an account-wide sensor."""

    value_fn: Callable[[dict[str, Any]], Any]
    attr_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class HostSensorDescription(SensorEntityDescription):
    """Describes a per-console sensor."""

    value_fn: Callable[[SiteHost], Any]


ACCOUNT_SENSORS: tuple[AccountSensorDescription, ...] = (
    AccountSensorDescription(
        key="status",
        name="Status",
        device_class=SensorDeviceClass.ENUM,
        options=["ok", "devices_offline", "console_offline"],
        value_fn=lambda s: s["status"],
        # Same attribute names as the old pyscript sensor, to ease dashboard migration.
        attr_fn=lambda s: {
            "badge_text": s["badge_text"],
            "summary": s["summary"],
            "offline_count": s["devices_offline"],
            "offline_by_host": s["offline_by_host"],
            "offline_devices": s["offline_devices"],
            "disconnected_consoles": s["offline_consoles"],
            "sites_down": s["sites_down"],
        },
    ),
    AccountSensorDescription(
        key="consoles_total",
        name="Consoles",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s["consoles_total"],
    ),
    AccountSensorDescription(
        key="consoles_offline",
        name="Consoles offline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s["consoles_offline"],
        attr_fn=lambda s: {"consoles": s["offline_consoles"]},
    ),
    AccountSensorDescription(
        key="devices_total",
        name="Devices",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s["devices_total"],
    ),
    AccountSensorDescription(
        key="devices_online",
        name="Devices online",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s["devices_online"],
    ),
    AccountSensorDescription(
        key="devices_offline",
        name="Devices offline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s["devices_offline"],
        attr_fn=lambda s: {
            "devices": s["offline_devices"],
            "by_host": s["offline_by_host"],
        },
    ),
    AccountSensorDescription(
        key="devices_pending",
        name="Devices offline (unconfirmed)",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s["devices_pending"],
    ),
    AccountSensorDescription(
        key="updates_available",
        name="Firmware updates available",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s["updates_available"],
    ),
    AccountSensorDescription(
        key="updates_overdue",
        name="Firmware updates overdue",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s["updates_overdue"],
        attr_fn=lambda s: {"devices": s["overdue_updates"]},
    ),
    AccountSensorDescription(
        key="oldest_update_days",
        name="Oldest pending firmware update",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s["oldest_update_days"],
    ),
    AccountSensorDescription(
        key="ignored",
        name="Ignored",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s["ignored"],
    ),
)

HOST_SENSORS: tuple[HostSensorDescription, ...] = (
    HostSensorDescription(
        key="devices_offline",
        name="Devices offline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda h: h.devices_offline,
    ),
    HostSensorDescription(
        key="updates_available",
        name="Firmware updates available",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda h: h.updates_available,
    ),
    HostSensorDescription(
        key="updates_overdue",
        name="Firmware updates overdue",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda h: h.updates_overdue,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors and add new ones as consoles are discovered."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        coordinator.async_sync_devices()
        entities: list[SensorEntity] = []

        if "account" not in known:
            known.add("account")
            entities.extend(
                AccountSensor(coordinator, desc) for desc in ACCOUNT_SENSORS
            )

        for host_id, host in coordinator.data.hosts.items():
            if host.ignored or f"host_{host_id}" in known:
                continue
            known.add(f"host_{host_id}")
            entities.extend(
                HostSensor(coordinator, host_id, desc) for desc in HOST_SENSORS
            )

        if entities:
            async_add_entities(entities)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class AccountSensor(AccountEntity, SensorEntity):
    """Account-wide count or status."""

    entity_description: AccountSensorDescription

    def __init__(
        self,
        coordinator: UnifiSiteManagerCoordinator,
        description: AccountSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_account_{description.key}"
        )

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data.summary)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attr_fn
        return fn(self.coordinator.data.summary) if fn else None


class HostSensor(HostEntity, SensorEntity):
    """Per-console count."""

    entity_description: HostSensorDescription

    def __init__(
        self,
        coordinator: UnifiSiteManagerCoordinator,
        host_id: str,
        description: HostSensorDescription,
    ) -> None:
        super().__init__(coordinator, host_id)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_host_{host_id}_{description.key}"
        )

    @property
    def native_value(self) -> Any:
        host = self.host
        return self.entity_description.value_fn(host) if host else None
