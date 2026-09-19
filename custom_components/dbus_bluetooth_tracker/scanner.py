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
                bus = await MessageBus(bus_type=BusType.SYSTEM, bus_address=address).connect()
                return bus
            except Exception as err:
                _LOGGER.error("Failed to connect to system D-Bus at '%s': %s", address, err)
                raise
        try:
            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            return bus
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

    async def poll_devices(self, macs: list[str], adapter: str = DEFAULT_ADAPTER) -> dict[str, dict[str, Any]]:
        """
        Hybrid Polling Method:
        Polls the live BlueZ ObjectManager cache. When a device is within BLE range
        and detected by the adapter, BlueZ will report it here with a live RSSI value.
        """
        results: dict[str, dict[str, Any]] = {mac.lower(): {"reachable": False, "rssi": None} for mac in macs}
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
                # Ensure adapter is actively scanning so BlueZ caches devices
                try:
                    await bus.call(Message(destination=BLUEZ_SERVICE, interface=ADAPTER_INTERFACE, path=adapter_path, member="StartDiscovery"))
                    _LOGGER.debug("Ensuring discovery is active on %s", adp)
                except Exception as err:
                    _LOGGER.debug("Could not start discovery on %s (may be active already): %s", adp, err)

            # Fetch the full object tree from BlueZ (it automatically caches devices as they broadcast)
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
                
                # Read the live state
                connected_var = props.get("Connected")
                connected = connected_var.value if isinstance(connected_var, Variant) else connected_var
                
                rssi_var = props.get("RSSI")
                rssi = rssi_var.value if isinstance(rssi_var, Variant) else rssi_var
                
                address_var = props.get("Address", "")
                address = address_var.value if isinstance(address_var, Variant) else address_var
                
                alias_var = props.get("Alias", "")
                alias = alias_var.value if isinstance(alias_var, Variant) else alias_var

                name_var = props.get("Name", "")
                name = name_var.value if isinstance(name_var, Variant) else name_var

                if not isinstance(address, str) or not address:
                    continue
                
                # If the BlueZ object exists and is being tracked, the device is physically in range!
                # We don't even need it to be actively "Connected" in the Bluetooth sense to be "Home"
                if address.lower() in results:
                    results[address.lower()]["reachable"] = True
                    results[address.lower()]["rssi"] = rssi
                    results[address.lower()]["name"] = name or alias or address
                    _LOGGER.debug("Device %s found in cache via %s (RSSI: %s, Connected: %s)", address, adp, rssi, connected)
        finally:
            bus.disconnect()
            await bus.wait_for_disconnect()

        return results

    async def discover_nearby(self, timeout: int = 10) -> list[dict[str, Any]]:
        """Discover nearby Bluetooth devices via BlueZ D-Bus."""
        devices: list[dict[str, Any]] = []
        try:
            bus = await self._connect()
        except Exception:
            return devices

        try:
            adapters = await self._get_adapters(bus)
            if not adapters:
                return devices

            for adp in adapters:
                adapter_path = f"{BLUEZ_PATH}/{adp}"
                try:
                    await bus.call(Message(destination=BLUEZ_SERVICE, interface=ADAPTER_INTERFACE, path=adapter_path, member="StartDiscovery"))
                except Exception:
                    pass

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
                    address_var = props.get("Address", "")
                    address = address_var.value if isinstance(address_var, Variant) else address_var
                    if not isinstance(address, str) or not address:
                        continue

                    name_var = props.get("Name", "")
                    name = name_var.value if isinstance(name_var, Variant) else name_var

                    alias_var = props.get("Alias", "")
                    alias = alias_var.value if isinstance(alias_var, Variant) else alias_var

                    rssi_var = props.get("RSSI")
                    rssi = rssi_var.value if isinstance(rssi_var, Variant) else rssi_var

                    devices.append({
                        "name": name or alias or address,
                        "mac": address,
                        "rssi": rssi,
                        "adapter": adp,
                    })
        finally:
            bus.disconnect()
            await bus.wait_for_disconnect()

        return devices
