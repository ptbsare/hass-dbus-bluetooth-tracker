"""Device tracker platform for the D-Bus Bluetooth Tracker integration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, cast

import homeassistant.util.dt as dt_util
from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import (
    CONF_CONSIDER_HOME,
    CONF_INTERVAL,
    CONF_TRACKED_MACS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the dbus_bluetooth_tracker device tracker platform."""
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    tracked_macs = entry.options.get(
        CONF_TRACKED_MACS, entry.data.get(CONF_TRACKED_MACS, [])
    )
    consider_home = entry.options.get(
        CONF_CONSIDER_HOME, entry.data.get(CONF_CONSIDER_HOME, 180)
    )

    # Clean MACs
    def clean_mac_list(macs: Any) -> list[str]:
        if not macs:
            return []
        if isinstance(macs, str):
            macs = [macs]
        return [mac.strip().upper() for mac in macs if mac and mac.strip()]

    tracked_macs = clean_mac_list(tracked_macs)

    entities = [
        DBusBluetoothTrackerEntity(
            coordinator=coordinator,
            mac=mac,
            consider_home=consider_home,
            entry_id=entry.entry_id,
        )
        for mac in tracked_macs
    ]
    async_add_entities(entities)


class DBusBluetoothTrackerEntity(CoordinatorEntity, RestoreEntity, ScannerEntity):
    """Representation of a D-Bus Bluetooth tracked device."""

    _attr_should_poll = False
    _attr_entity_category = None

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        mac: str,
        consider_home: int,
        entry_id: str,
    ) -> None:
        """Initialize the tracked device entity."""
        super().__init__(coordinator)

        self._mac = mac.lower()
        self._consider_home = consider_home
        self._entry_id = entry_id
        self._attr_unique_id = self._mac
        self._attr_name = self._mac
        self._last_seen: datetime | None = None

        # Set initial state from coordinator data
        if self._mac in (coordinator.data or {}):
            device_info = coordinator.data.get(self._mac, {})
            if isinstance(device_info, dict) and device_info.get("reachable", False):
                self._last_seen = dt_util.utcnow()

    async def async_added_to_hass(self) -> None:
        """Restore previous state on startup."""
        await super().async_added_to_hass()

        # Restore state if it exists
        if (last_state := await self.async_get_last_state()) is not None:
            # If the entity had a previous state of home, consider it seen
            if last_state.state == "home":
                self._last_seen = dt_util.utcnow()

    @property
    def available(self) -> bool:
        """Return True, device trackers are always available."""
        return True

    @property
    def source_type(self) -> SourceType:
        """Return the source type of the device tracker."""
        return SourceType.BLUETOOTH

    @property
    def is_connected(self) -> bool:
        """Return True if the device is connected/present."""
        data = self.coordinator.data or {}
        device_info = data.get(self._mac, {})
        reachable = False
        if isinstance(device_info, dict):
            reachable = device_info.get("reachable", False)
        elif isinstance(device_info, bool):
            reachable = device_info

        if reachable:
            return True

        # Check consider_home window
        if self._last_seen is not None:
            elapsed = dt_util.utcnow() - self._last_seen
            if elapsed.total_seconds() <= self._consider_home:
                return True

        return False

    @property
    def mac_address(self) -> str:
        """Return the MAC address of the device."""
        return self._mac

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {
            "mac_address": self._mac,
        }
        data = self.coordinator.data or {}
        device_info = data.get(self._mac, {})
        if isinstance(device_info, dict):
            rssi = device_info.get("rssi")
            if rssi is not None:
                attrs["signal_strength"] = rssi
        if self._last_seen is not None:
            attrs["last_seen"] = self._last_seen.isoformat()
        return attrs

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        data = self.coordinator.data or {}
        device_info = data.get(self._mac, {})
        reachable = False
        if isinstance(device_info, dict):
            reachable = device_info.get("reachable", False)
        elif isinstance(device_info, bool):
            reachable = device_info

        if reachable:
            self._last_seen = dt_util.utcnow()
        self.async_write_ha_state()
