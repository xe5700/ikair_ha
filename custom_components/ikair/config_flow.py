"""Config flow for iKair BLE Thermometer integration."""

from __future__ import annotations

from typing import Any, Optional

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
    async_request_active_scan,
)
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS, CONF_NAME

from .const import CONF_KEEP_CONNECTED, DOMAIN
from .ikair_ble import IKairProtocol

CONF_ADAPTER = "adapter"


class IKairConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for iKair BLE Thermometer."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, str] = {}
        self._protocol = IKairProtocol()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle the Bluetooth discovery step."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        if not discovery_info.name or not discovery_info.name.startswith("iKair H&T"):
            return self.async_abort(reason="not_supported")

        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery."""
        if user_input is not None:
            assert self._discovery_info is not None
            return self.async_create_entry(
                title=self._discovery_info.name,
                data={},
            )

        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "name": (self._discovery_info.name if self._discovery_info else "iKair device"),
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a manual user-initiated setup."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=self._discovered_devices[address],
                data={},
            )

        await async_request_active_scan(self.hass)
        current_addresses = self._async_current_ids(include_ignore=False)

        for discovery_info in async_discovered_service_info(self.hass, connectable=True):
            address = discovery_info.address
            if address in current_addresses or address in self._discovered_devices:
                continue
            if discovery_info.name and discovery_info.name.startswith("iKair H&T"):
                self._discovered_devices[address] = discovery_info.name

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ADDRESS): vol.In({
                    addr: f"{name} ({addr})"
                    for addr, name in self._discovered_devices.items()
                }),
            }),
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlow:
        """Create the options flow."""
        return IKairOptionsFlow(config_entry)


class IKairOptionsFlow(OptionsFlow):
    """Handle options for iKair BLE Thermometer."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._config_entry.options.get(CONF_KEEP_CONNECTED, False)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(CONF_KEEP_CONNECTED, default=current): bool,
            }),
        )
