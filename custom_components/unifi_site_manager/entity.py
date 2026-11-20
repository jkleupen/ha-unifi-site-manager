"""Base entities for UniFi Site Manager Monitoring."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ACCOUNT_DEVICE_ID, DOMAIN
from .coordinator import (
    UnifiSiteManagerCoordinator,
    device_device_id,
    host_device_id,
)
from .processing import SiteDevice, SiteHost


class UnifiEntity(CoordinatorEntity[UnifiSiteManagerCoordinator]):
    """Common base."""

    _attr_has_entity_name = True


class AccountEntity(UnifiEntity):
    """Entity belonging to the account-wide 'UniFi Site Manager' device."""

    def __init__(self, coordinator: UnifiSiteManagerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, ACCOUNT_DEVICE_ID)})


class HostEntity(UnifiEntity):
    """Entity belonging to one console."""

    def __init__(self, coordinator: UnifiSiteManagerCoordinator, host_id: str) -> None:
        super().__init__(coordinator)
        self._host_id = host_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, host_device_id(host_id))}
        )

    @property
    def host(self) -> SiteHost | None:
        return self.coordinator.data.hosts.get(self._host_id)

    @property
    def available(self) -> bool:
        host = self.host
        return super().available and host is not None and not host.ignored


class DeviceEntity(UnifiEntity):
    """Entity belonging to one non-console device."""

    def __init__(self, coordinator: UnifiSiteManagerCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        dev = coordinator.data.devices[key]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_device_id(key))},
            name=dev.name,
            manufacturer="Ubiquiti",
            model=dev.model,
            sw_version=dev.version,
            via_device=(DOMAIN, host_device_id(dev.host_id)),
        )

    @property
    def dev(self) -> SiteDevice | None:
        return self.coordinator.data.devices.get(self._key)

    @property
    def available(self) -> bool:
        dev = self.dev
        return super().available and dev is not None and not dev.ignored
