"""iKair BLE protocol implementation.

Active (GATT connection) mode: connects, authenticates, reads temperature/humidity
notifications. Based on reverse-engineered Android app.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection

_LOGGER = logging.getLogger(__name__)

# Timeout constants
SCAN_TIMEOUT = 30
CONNECT_TIMEOUT = 20

# UUIDs
SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
OPERATION_NOTIFY_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
USER_BIND_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"
HISTORICAL_DATA_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"
REAL_TIME_DATA_UUID = "0000fff4-0000-1000-8000-00805f9b34fb"
CONFIG_CHAR_UUID = "0000fff5-0000-1000-8000-00805f9b34fb"
NOTIFY_DESC_UUID = "00002902-0000-1000-8000-00805f9b34fb"


class NotifyDataType(IntEnum):
    CLOCK_READ = 1
    DEVICE_SN = 9
    CLOCK_SYNC = 17
    DEVICE_VERIFICATION = 32
    FIRMWARE_VERSION = 35
    BATTERY_DATA = 36


@dataclass
class SensorData:
    temperature: float
    humidity: float
    timestamp: datetime
    battery: Optional[int] = None


class IKairProtocol:
    """Handles the iKair BLE communication protocol."""

    def __init__(self, adapter: str | None = None) -> None:
        self.adapter = adapter
        self._device: BLEDevice | None = None
        self._client: BleakClient | None = None
        self._config_char_handle: str | None = None
        self._sensor_data: SensorData | None = None
        self._event_loop = asyncio.get_event_loop()
        self._ready = asyncio.Event()
        self._ad_data = None


    async def discover(self) -> list[BLEDevice]:
        """Scan for iKair devices."""
        _LOGGER.info("Scanning for iKair devices...")
        devices = await BleakScanner.discover(
            timeout=SCAN_TIMEOUT,
            adapter=self.adapter,
        )
        ikair_devices = [
            d for d in devices
            if d.name and d.name.startswith("iKair H&T")
        ]
        return ikair_devices

    async def connect_and_read(self, device: BLEDevice, read_battery: bool = True) -> SensorData | None:
        """Connect to device, perform auth, read sensor data, then disconnect.

        This is a self-contained session: connect -> bind -> get data -> disconnect.
        Called periodically by the HA coordinator.
        """
        self._device = device
        self._sensor_data = None
        self._ready.clear()

        client = None
        self._read_battery_requested = read_battery
        try:
            client = await establish_connection(
                BleakClient,
                device,
                device.address,
                max_attempts=3,
                timeout=CONNECT_TIMEOUT,
            )
            self._client = client
            if not client.is_connected:
                return None
            _LOGGER.debug("Connected to %s", device.name)

            await self._setup_notifications(client)
            await self._authenticate(client)

            # Wait for real-time data notification
            try:
                await asyncio.wait_for(self._ready.wait(), timeout=20.0)
            except asyncio.TimeoutError:
                _LOGGER.warning("Timed out waiting for sensor data from %s", device.name)

            await self._cleanup(client)
        except (BleakError, TimeoutError, asyncio.TimeoutError, OSError) as e:
            _LOGGER.error("BLE error with %s: %s", device.name, e)
            return None
        finally:
            if client and client.is_connected:
                await client.disconnect()
            self._client = None

        return self._sensor_data

    async def _setup_notifications(self, client: BleakClient) -> None:
        """Enable notifications on relevant characteristics."""
        service = client.services.get_service(SERVICE_UUID)
        if not service:
            _LOGGER.error("Service %s not found on %s", SERVICE_UUID, self._device.name)
            return

        notify_chars = [
            OPERATION_NOTIFY_UUID,
            HISTORICAL_DATA_UUID,
            REAL_TIME_DATA_UUID,
        ]

        for char_uuid in notify_chars:
            char = service.get_characteristic(char_uuid)
            if char:
                try:
                    await client.start_notify(char, self._notification_handler)
                    _LOGGER.debug("Notification enabled on %s", char_uuid)
                except Exception as e:
                    _LOGGER.debug("Failed to enable notify on %s: %s", char_uuid, e)

        # Find config char for writing commands
        config_char = service.get_characteristic(CONFIG_CHAR_UUID)
        if config_char:
            self._config_char_handle = config_char

    async def _authenticate(self, client: BleakClient) -> None:
        """Authenticate with the device by reading SN and binding."""
        if not self._config_char_handle:
            _LOGGER.warning("Config char not found, cannot authenticate")
            return

        # Request device SN
        sn_cmd = bytearray([9, 1, 1, 1, 1])
        await client.write_gatt_char(self._config_char_handle, sn_cmd)

    async def _cleanup(self, client: BleakClient) -> None:
        """Disable notifications AND tell device to stop real-time streaming.
        
        If we don't send [11,0,0,0,0] before disconnect, the device may
        remain in high-frequency advertising mode, wasting battery.
        """
        # Step 1: Tell device to stop real-time data streaming
        if self._config_char_handle:
            try:
                # Disable real-time (11,0,...) - reverse of enable [11,1,0,0,0]
                await client.write_gatt_char(
                    self._config_char_handle, bytearray([11, 0, 0, 0, 0])
                )
                # Disable historical data
                await client.write_gatt_char(
                    self._config_char_handle, bytearray([10, 0, 0, 0, 0])
                )
                # Remove notice (may reduce advertising)
                await client.write_gatt_char(
                    self._config_char_handle, bytearray([6, 0, 0, 0, 0])
                )
                _LOGGER.debug("Sent power-saving commands before disconnect")
            except Exception as e:
                _LOGGER.debug("Failed to send power-saving commands: %s", e)

        # Step 2: Stop notification subscriptions
        service = client.services.get_service(SERVICE_UUID)
        if service:
            for char_uuid in [OPERATION_NOTIFY_UUID, HISTORICAL_DATA_UUID, REAL_TIME_DATA_UUID]:
                char = service.get_characteristic(char_uuid)
                if char:
                    try:
                        await client.stop_notify(char)
                    except Exception:
                        pass

    async def _notification_handler(self, char, data: bytearray) -> None:
        """Handle incoming BLE notifications."""
        char_uuid = char.uuid

        if char_uuid == REAL_TIME_DATA_UUID:
            self._handle_realtime_data(data)
        elif char_uuid == HISTORICAL_DATA_UUID:
            self._handle_realtime_data(data)  # Same format
        elif char_uuid == OPERATION_NOTIFY_UUID:
            await self._handle_notify_data(data)

    def _handle_realtime_data(self, data: bytearray) -> None:
        """Parse real-time or historical temperature/humidity data."""
        if len(data) < 8:
            return

        timestamp_sec = struct.unpack("<I", data[:4])[0]
        temp_raw = struct.unpack("<h", data[4:6])[0]
        hum_raw = struct.unpack("<h", data[6:8])[0]

        temperature = temp_raw / 10.0
        humidity = hum_raw / 10.0
        timestamp = datetime.fromtimestamp(
            datetime(2000, 1, 1).timestamp() + timestamp_sec
        )

        _LOGGER.debug(
            "Sensor data: temp=%.1f°C, hum=%.1f%%, time=%s",
            temperature, humidity, timestamp,
        )
        self._sensor_data = SensorData(
            temperature=temperature,
            humidity=humidity,
            timestamp=timestamp,
        )
        self._ready.set()
        # 持续连接模式：回调推送数据
        if hasattr(self, '_on_data_callback') and self._on_data_callback:
            self._on_data_callback(self._sensor_data)

    async def _handle_notify_data(self, data: bytearray) -> None:
        """Handle operation notification data (fff1)."""
        if not data:
            return

        match data[0]:
            case NotifyDataType.DEVICE_SN:
                await self._handle_sn(data)
            case NotifyDataType.DEVICE_VERIFICATION:
                await self._handle_verification()
            case NotifyDataType.CLOCK_SYNC:
                pass  # Clock sync confirmed, nothing more to do
            case NotifyDataType.BATTERY_DATA:
                self._handle_battery(data)

    async def _handle_sn(self, data: bytearray) -> None:
        """Handle incoming device SN - send bind data."""
        if not self._client or not self._device:
            return

        service = self._client.services.get_service(SERVICE_UUID)
        if not service:
            return

        verify_char = service.get_characteristic(USER_BIND_UUID)
        if not verify_char:
            return

        # Build bind packet: [32, 7, 6, 5, 4, 3, 2, 1, sn_bytes...]
        bind_data = bytearray([32, 7, 6, 5, 4, 3, 2, 1])
        bind_data.extend(data[1:8])

        await self._client.write_gatt_char(verify_char, bind_data)
        _LOGGER.debug("Sent bind data to %s", self._device.name)

    async def _handle_verification(self) -> None:
        """Handle verification success - complete initialization.
        
        Auth done → sync time → remove notice → enable real-time briefly.
        """
        if not self._client or not self._config_char_handle:
            return

        # 1. Sync device time (needed for stable connection)
        await self._sync_device_time()
        
        # 2. Remove notice/message
        await self._client.write_gatt_char(
            self._config_char_handle, bytearray([6, 0, 0, 0, 0])
        )
        
        # 3. Enable real-time data (to receive temperature)
        await self._client.write_gatt_char(
            self._config_char_handle, bytearray([11, 1, 0, 0, 0])
        )
        
        # 4. Read battery level (first time + every 5 min)
        if getattr(self, '_read_battery_requested', True):
            await self._client.write_gatt_char(
                self._config_char_handle, bytearray([8, 36, 0, 0, 0])
            )
        
        _LOGGER.debug("Initialization complete for %s", self._device.name)

    async def _read_battery(self) -> None:
        """读取设备电量。"""
        if self._config_char_handle:
            await self._client.write_gatt_char(
                self._config_char_handle, bytearray([8, 36, 0, 0, 0])
            )

    async def _sync_device_time(self) -> None:
        """同步设备时间（从2000-01-01开始的秒数）。"""
        base = datetime(2000, 1, 1)
        seconds = int((datetime.now().timestamp() - base.timestamp()))
        data = bytearray([0x01])
        data.extend(struct.pack("<I", seconds))
        await self._client.write_gatt_char(self._config_char_handle, data)
        _LOGGER.debug("Synced device time")

    def _handle_battery(self, data: bytearray) -> None:
        """Handle battery level data."""
        if len(data) >= 2 and self._sensor_data:
            self._sensor_data.battery = data[1]

    async def connect_persistent(
        self, device: BLEDevice, data_callback
    ) -> None:
        """持续连接设备，每次收到通知都回调。连接断开后自动抛出异常。"""
        self._device = device
        self._on_data_callback = data_callback
        client = await establish_connection(
            BleakClient, device, device.address,
            max_attempts=3, timeout=CONNECT_TIMEOUT,
        )
        self._client = client
        _LOGGER.debug("持续连接已建立: %s", device.name)

        await self._setup_notifications(client)
        await self._authenticate(client)

        # 保持连接，每5分钟读一次电池
        batt_interval = 300  # 秒
        last_batt = 0
        while client.is_connected:
            await asyncio.sleep(1)
            elapsed = time.monotonic() - last_batt
            if elapsed >= batt_interval:
                await self._read_battery()
                last_batt = time.monotonic()

    async def disconnect(self) -> None:
        """断开连接（如果是持续连接模式）。"""
        if self._client and self._client.is_connected:
            try:
                await self._cleanup(self._client)
                await self._client.disconnect()
            except Exception:
                pass
        self._client = None
