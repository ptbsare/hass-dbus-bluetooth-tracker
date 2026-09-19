"""D-Bus Bluetooth scanner for Home Assistant."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from dbus_fast import BusType, Message, MessageType, Variant
from dbus_fast.aio import MessageBus

from homeassistant.core import HomeAssistant

from .const import DEFAULT_ADAPTER

_LOGGER = logging.getLogger(__name__)

BLUEZ_SERVICE = "org.bluez"
BLUEZ_PATH = "/org/bluez"
ADAPTER_INTERFACE = f"{BLUEZ_SERVICE}.Adapter1"
DEVICE_INTERFACE = f"{BLUEZ_SERVICE}.Device1"

DBUS_SOCKET_CANDIDATES = [
    "/var/run/dbus/system_bus_socket",
    "/run/dbus/system_bus_socket",
]

# RSSI values that BlueZ reports as "unknown / not measurable"
_INVALID_RSSI = (None, 0x7FFF, 32767, -127, 127)


def _is_valid_rssi(value: Any) -> bool:
    """Return True if the RSSI value looks like a real measurement."""
    if value in _INVALID_RSSI:
        return False
    return isinstance(value, (int, float)) and -120 <= value <= -10


def resolve_dbus_address(custom_address: str | None = None) -> str | None:
    """Resolve a usable D-Bus system bus address."""
    if custom_address and custom_address.strip():
        return custom_address.strip()
    for candidate in DBUS_SOCKET_CANDIDATES:
        if os.path.exists(candidate):
            return f"unix:path={candidate}"
    return None


class DBusBluetoothScanner:
    """Manages D-Bus communication with BlueZ for tracking devices."""

    def __init__(self, hass: HomeAssistant, dbus_address: str | None = None) -> None:
        """Initialize the scanner."""
        self._hass = hass
        self._dbus_address = dbus_address

    async def _connect(self) -> MessageBus:
        """Connect to the system bus."""
        address = resolve_dbus_address(self._dbus_address)
        if address:
            try:
                return await MessageBus(bus_type=BusType.SYSTEM, bus_address=address).connect()
            except Exception as err:
                _LOGGER.error("Failed to connect to system D-Bus at '%s': %s", address, err)
                raise
        try:
            return await MessageBus(bus_type=BusType.SYSTEM).connect()
        except Exception as err:
            _LOGGER.error("Failed to connect to system D-Bus: %s", err)
            raise

    async def _get_adapters(self, bus: MessageBus, selected_adapter: str = DEFAULT_ADAPTER) -> list[str]:
        """List available Bluetooth adapters via the D-Bus ObjectManager."""
        adapters: list[str] = []
        try:
            res = await bus.call(Message(destination=BLUEZ_SERVICE, path="/", interface="org.freedesktop.DBus.ObjectManager", member="GetManagedObjects"))
        except Exception as err:
            _LOGGER.error("Failed to call GetManagedObjects: %s", err)
            return adapters
        if res.message_type != MessageType.METHOD_RETURN or not res.body:
            return adapters
        for path, interfaces in res.body[0].items():
            if ADAPTER_INTERFACE in interfaces:
                adapters.append(path.rsplit("/", 1)[-1])

        target = (selected_adapter or DEFAULT_ADAPTER).strip().lower()
        if target and target != DEFAULT_ADAPTER:
            if target in adapters:
                return [target]
            _LOGGER.warning("Configured adapter '%s' not found; using all.", target)
        return adapters

    async def async_list_system_adapters(self) -> list[str]:
        """Utility method to get all system adapters for UI options list."""
        try:
            bus = await self._connect()
        except Exception:
            return []
        try:
            return await self._get_adapters(bus, selected_adapter="auto")
        finally:
            bus.disconnect()
            await bus.wait_for_disconnect()

    async def poll_devices(
        self, macs: list[str], adapter: str = DEFAULT_ADAPTER
    ) -> dict[str, dict[str, Any]]:
        """Poll the live BlueZ ObjectManager cache for the given MACs.

        Returns: {mac: {"reachable": bool, "rssi": int|None, "name": str|None}}
        """
        results: dict[str, dict[str, Any]] = {
            mac.lower(): {"reachable": False, "rssi": None, "name": None}
            for mac in macs
        }
        if not macs:
            return results

        try:
            bus = await self._connect()
        except Exception as err:
            _LOGGER.error("Cannot poll devices, D-Bus connection failed: %s", err)
            return results

        try:
            adapters = await self._get_adapters(bus, selected_adapter=adapter)
            if not adapters:
                return results

            for adp in adapters:
                adapter_path = f"{BLUEZ_PATH}/{adp}"
                try:
                    await bus.call(Message(destination=BLUEZ_SERVICE, interface=ADAPTER_INTERFACE, path=adapter_path, member="StartDiscovery"))
                except Exception as err:
                    _LOGGER.debug("StartDiscovery on %s failed (may already be active): %s", adp, err)

            # Give BlueZ a moment to refresh cached devices / RSSI values
            await asyncio.sleep(1.5)

            try:
                res = await bus.call(Message(destination=BLUEZ_SERVICE, path="/", interface="org.freedesktop.DBus.ObjectManager", member="GetManagedObjects"))
            except Exception as err:
                _LOGGER.error("GetManagedObjects failed during poll: %s", err)
                return results

            if res.message_type != MessageType.METHOD_RETURN or not res.body:
                return results

            objects = res.body[0]
            for path, interfaces in objects.items():
                if DEVICE_INTERFACE not in interfaces:
                    continue

                props = interfaces[DEVICE_INTERFACE]

                def _get(name: str) -> Any:
                    val = props.get(name)
                    return val.value if isinstance(val, Variant) else val

                address = _get("Address")
                rssi = _get("RSSI")
                alias = _get("Alias")
                name = _get("Name")

                if not isinstance(address, str) or not address:
                    continue

                key = address.lower()
                if key not in results:
                    continue

                results[key]["reachable"] = True
                results[key]["name"] = name or alias or address
                if _is_valid_rssi(rssi):
                    results[key]["rssi"] = rssi
                _LOGGER.debug(
                    "BlueZ cache: %s found (rssi=%s, connected_name=%s)",
                    address, rssi, name or alias,
                )
        finally:
            bus.disconnect()
            await bus.wait_for_disconnect()

        for mac, data in results.items():
            _LOGGER.debug(
                "poll_devices result %s: reachable=%s rssi=%s name=%s",
                mac, data["reachable"], data["rssi"], data["name"],
            )
        return results

    async def discover_nearby(
        self, timeout: int = 10, adapter: str = DEFAULT_ADAPTER
    ) -> list[dict[str, Any]]:
        """Actively scan nearby Bluetooth devices via BlueZ D-Bus.

        The `timeout` parameter controls how many seconds the adapter is put
        into discovery mode before the results are read back.
        """
        devices: list[dict[str, Any]] = []
        try:
            bus = await self._connect()
        except Exception:
            return devices

        try:
            adapters = await self._get_adapters(bus, selected_adapter=adapter)
            _LOGGER.debug("discover_nearby on adapters %s for %ss", adapters, timeout)
            if not adapters:
                return devices

            for adp in adapters:
                adapter_path = f"{BLUEZ_PATH}/{adp}"
                try:
                    await bus.call(Message(destination=BLUEZ_SERVICE, interface=ADAPTER_INTERFACE, path=adapter_path, member="StartDiscovery"))
                    _LOGGER.debug("StartDiscovery on %s", adp)
                except Exception as err:
                    _LOGGER.debug("StartDiscovery on %s failed: %s", adp, err)

                _LOGGER.debug("Scanning %s for %s seconds...", adp, timeout)
                await asyncio.sleep(timeout)

                try:
                    await bus.call(Message(destination=BLUEZ_SERVICE, interface=ADAPTER_INTERFACE, path=adapter_path, member="StopDiscovery"))
                except Exception:
                    pass

                try:
                    res = await bus.call(Message(destination=BLUEZ_SERVICE, path="/", interface="org.freedesktop.DBus.ObjectManager", member="GetManagedObjects"))
                except Exception:
                    continue

                if res.message_type != MessageType.METHOD_RETURN or not res.body:
                    continue

                for path, interfaces in res.body[0].items():
                    if DEVICE_INTERFACE not in interfaces:
                        continue
                    props = interfaces[DEVICE_INTERFACE]

                    def _get(name: str) -> Any:
                        val = props.get(name)
                        return val.value if isinstance(val, Variant) else val

                    address = _get("Address")
                    if not isinstance(address, str) or not address:
                        continue

                    name = _get("Name")
                    alias = _get("Alias")
                    rssi = _get("RSSI")

                    devices.append({
                        "name": name or alias or address,
                        "mac": address,
                        "rssi": rssi if _is_valid_rssi(rssi) else None,
                        "adapter": adp,
                    })
        finally:
            bus.disconnect()
            await bus.wait_for_disconnect()

        return devices
