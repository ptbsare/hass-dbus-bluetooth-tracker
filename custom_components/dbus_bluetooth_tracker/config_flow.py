"""Config flow for dbus_bluetooth_tracker integration."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_ADAPTER,
    CONF_CONSIDER_HOME,
    CONF_INTERVAL,
    CONF_SEEN_INTERVAL,
    CONF_TRACKED_MACS,
    DEFAULT_ADAPTER,
    DEFAULT_CONSIDER_HOME,
    DEFAULT_INTERVAL,
    DEFAULT_SEEN_INTERVAL,
    DOMAIN,
)
from .scanner import DBusBluetoothScanner, clean_mac_list

_LOGGER = logging.getLogger(__name__)

# Regex to extract MAC addresses from any text (handles glued/split formats)
_MAC_RE = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")


def _parse_macs(raw: str) -> list[str]:
    """Parse a raw string into a list of uppercase MAC addresses."""
    # First, split on common separators
    parts = raw.replace("\n", ",").split(",")
    result: list[str] = []
    for part in parts:
        matches = _MAC_RE.findall(part)
        if matches:
            result.extend(m.upper() for m in matches)
        elif part.strip():
            # Might be a malformed entry, keep it as-is (clean_mac_list will filter)
            result.append(part.strip().upper())
    return result


class DBusBluetoothTrackerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for D-Bus Bluetooth Tracker."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(
                title="D-Bus Bluetooth Tracker",
                data={},
                options={
                    CONF_TRACKED_MACS: [],
                    CONF_INTERVAL: DEFAULT_INTERVAL,
                    CONF_SEEN_INTERVAL: DEFAULT_SEEN_INTERVAL,
                    CONF_CONSIDER_HOME: DEFAULT_CONSIDER_HOME,
                    CONF_ADAPTER: DEFAULT_ADAPTER,
                },
            )

        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> DBusBluetoothTrackerOptionsFlowHandler:
        """Get the options flow for this handler."""
        return DBusBluetoothTrackerOptionsFlowHandler()


class DBusBluetoothTrackerOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for dbus_bluetooth_tracker."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            macs_raw = user_input.get(CONF_TRACKED_MACS, "")
            macs_list = clean_mac_list(macs_raw)

            return self.async_create_entry(
                title="",
                data={
                    CONF_TRACKED_MACS: macs_list,
                    CONF_INTERVAL: user_input[CONF_INTERVAL],
                    CONF_SEEN_INTERVAL: user_input[CONF_SEEN_INTERVAL],
                    CONF_CONSIDER_HOME: user_input[CONF_CONSIDER_HOME],
                    CONF_ADAPTER: user_input[CONF_ADAPTER],
                },
            )

        current_macs = self.config_entry.options.get(
            CONF_TRACKED_MACS, self.config_entry.data.get(CONF_TRACKED_MACS, [])
        )
        # Robustly parse: handle both list-of-strings and glued strings
        all_text = "\n".join(str(m) for m in current_macs)
        display_macs = clean_mac_list(all_text)

        current_interval = self.config_entry.options.get(
            CONF_INTERVAL, self.config_entry.data.get(CONF_INTERVAL, DEFAULT_INTERVAL)
        )
        current_seen_interval = self.config_entry.options.get(
            CONF_SEEN_INTERVAL,
            self.config_entry.data.get(CONF_SEEN_INTERVAL, DEFAULT_SEEN_INTERVAL),
        )
        current_consider_home = self.config_entry.options.get(
            CONF_CONSIDER_HOME,
            self.config_entry.data.get(CONF_CONSIDER_HOME, DEFAULT_CONSIDER_HOME),
        )
        current_adapter = self.config_entry.options.get(
            CONF_ADAPTER, self.config_entry.data.get(CONF_ADAPTER, DEFAULT_ADAPTER)
        )

        macs_str = "\n".join(display_macs)

        scanner = DBusBluetoothScanner(self.hass)
        system_adapters = await scanner.async_list_system_adapters()

        adapter_options = [{"value": DEFAULT_ADAPTER, "label": f"auto ({DEFAULT_ADAPTER})"}]
        for adp in system_adapters:
            if adp != DEFAULT_ADAPTER:
                adapter_options.append({"value": adp, "label": adp})

        options_schema = vol.Schema(
            {
                vol.Required(CONF_TRACKED_MACS, default=macs_str): str,
                vol.Required(
                    CONF_ADAPTER, default=current_adapter
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=adapter_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_INTERVAL, default=current_interval): vol.All(
                    vol.Coerce(int), vol.Range(min=5)
                ),
                vol.Required(CONF_SEEN_INTERVAL, default=current_seen_interval): vol.All(
                    vol.Coerce(int), vol.Range(min=0)
                ),
                vol.Required(
                    CONF_CONSIDER_HOME, default=current_consider_home
                ): vol.All(vol.Coerce(int), vol.Range(min=10)),
            }
        )

        return self.async_show_form(step_id="init", data_schema=options_schema)
