"""iKair BLE Thermometer integration for Home Assistant."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Optional

from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import (
    CONF_KEEP_CONNECTED,
    DOMAIN,
    DEFAULT_UPDATE_INTERVAL_SECONDS,
    KEEP_CONNECTED_INTERVAL_SECONDS,
)
from .ikair_ble import IKairProtocol, SensorData

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up iKair BLE device from a config entry."""
    address = entry.unique_id
    assert address is not None

    coordinator = IKairDataCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.async_on_unload(coordinator.async_shutdown)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # 监听选项变更，自动重载
    entry.async_on_unload(
        entry.add_update_listener(async_update_options)
    )

    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """选项变更时重载插件使配置生效。"""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: IKairDataCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()

    return unload_ok


class IKairDataCoordinator(DataUpdateCoordinator[Optional[SensorData]]):
    """Coordinator to fetch iKair sensor data periodically."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._address = entry.unique_id
        self._entry = entry
        self._protocol = IKairProtocol()
        self._keep_connected = entry.options.get(CONF_KEEP_CONNECTED, False)
        self._persistent_client = None
        self._persistent_task: asyncio.Task | None = None
        self._last_data: SensorData | None = None

        interval = KEEP_CONNECTED_INTERVAL_SECONDS if self._keep_connected else DEFAULT_UPDATE_INTERVAL_SECONDS
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {self._address}",
            update_interval=timedelta(seconds=interval),
        )

        if self._keep_connected:
            self._persistent_task = asyncio.create_task(self._persistent_loop())

    async def _async_update_data(self) -> Optional[SensorData]:
        """Fetch sensor data - poll or return latest from persistent connection."""
        if self._keep_connected:
            if self._last_data is not None:
                return self._last_data
            # 等待首次数据
            for _ in range(30):
                await asyncio.sleep(1)
                if self._last_data is not None:
                    return self._last_data
            raise UpdateFailed(f"持续连接未获取到 {self._address} 的数据")

        # 普通轮询模式
        return await self._poll_once()

    async def _poll_once(self) -> SensorData:
        """单次连接 → 读取 → 断开"""
        for attempt in range(3):
            try:
                ble_device = async_ble_device_from_address(
                    self.hass, self._address, connectable=True
                )
                if ble_device is None:
                    from homeassistant.components.bluetooth import async_discovered_service_info
                    for info in async_discovered_service_info(self.hass, connectable=True):
                        if info.address == self._address:
                            ble_device = info.device
                            break

                if ble_device is None:
                    if attempt < 2:
                        await asyncio.sleep(10)
                        continue
                    raise UpdateFailed(f"找不到设备 {self._address}")

                data = await self._protocol.connect_and_read(ble_device)
                if data is not None:
                    _LOGGER.info("iKair数据: %.1f°C %.1f%%", data.temperature, data.humidity)
                    return data

                if attempt < 2:
                    await asyncio.sleep(10)
            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(10)
                else:
                    raise UpdateFailed(f"读取失败(3次): {e}") from e

        raise UpdateFailed(f"读取 {self._address} 失败(3次)")

    async def _persistent_loop(self):
        """持续连接设备，通过通知推送数据"""
        _LOGGER.info("启动持续连接模式 %s", self._address)
        while True:
            try:
                ble_device = async_ble_device_from_address(
                    self.hass, self._address, connectable=True
                )
                if ble_device is not None:
                    await self._protocol.connect_persistent(
                        ble_device,
                        data_callback=self._on_sensor_data,
                    )
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.warning("持续连接断开，30秒后重连: %s", e)
                await asyncio.sleep(30)

    def _on_sensor_data(self, data: SensorData):
        """收到持续连接推送的数据"""
        self._last_data = data
        self.async_set_updated_data(data)

    async def async_shutdown(self, *args) -> None:
        """Clean up."""
        _LOGGER.debug("关闭 coordinator %s", self._address)
        if self._persistent_task:
            self._persistent_task.cancel()
            try:
                await self._persistent_task
            except asyncio.CancelledError:
                pass
        await self._protocol.disconnect()
