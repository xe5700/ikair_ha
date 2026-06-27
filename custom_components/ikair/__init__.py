"""iKair BLE Thermometer integration for Home Assistant."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Optional

from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN, MANUFACTURER, DEFAULT_UPDATE_INTERVAL_MINUTES
from .ikair_ble import IKairProtocol, SensorData

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up iKair BLE device from a config entry."""
    address = entry.unique_id
    assert address is not None

    coordinator = IKairDataCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, coordinator.shutdown)
    )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: IKairDataCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.shutdown()

    return unload_ok


class IKairDataCoordinator(DataUpdateCoordinator[Optional[SensorData]]):
    """Coordinator to fetch iKair sensor data periodically."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._address = entry.unique_id
        self._entry = entry
        self._protocol = IKairProtocol()
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {self._address}",
            update_interval=timedelta(seconds=30),  # 每3分钟更新一次
        )

    async def _async_update_data(self) -> Optional[SensorData]:
        """Fetch sensor data from the device."""
        _LOGGER.debug("开始获取iKair数据 %s", self._address)
        
        ble_device = async_ble_device_from_address(
            self.hass, self._address, connectable=True
        )
        if ble_device is None:
            _LOGGER.debug("设备 %s 不在缓存中", self._address)
            # 让 HA 蓝牙集成去发现
            from homeassistant.components.bluetooth import async_discovered_service_info
            for info in async_discovered_service_info(self.hass, connectable=True):
                if info.address == self._address:
                    ble_device = info.device
                    break

        if ble_device is None:
            raise UpdateFailed(f"找不到设备 {self._address}")

        _LOGGER.debug("连接 %s", self._address)
        data = await self._protocol.connect_and_read(ble_device)
        
        if data is None:
            raise UpdateFailed(f"读取 {self._address} 数据失败")

        _LOGGER.info("iKair数据: %.1f°C %.1f%%", data.temperature, data.humidity)
        return data

    async def shutdown(self, *args) -> None:
        """Clean up."""
        _LOGGER.debug("关闭 coordinator %s", self._address)
