"""D-Bus Bluetooth scanner for Home Assistant."""

from __future__ import annotations

import asyncio
import logging
import os
import re
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
CONNECT_TIMEOUT = 3

DBUS_SOCKET_CANDIDATES = [
    "/var/run/dbus/system_bus_socket",
    "/run/dbus/system_bus_socket",
]

_MAC_RE = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")


def resolve_dbus_address(custom_address: str | None = None) -> str | None:
    if custom_address and custom_address.strip():
        return custom_address.strip()
    for candidate in DBUS_SOCKET_CANDIDATES:
        if os.path.exists(candidate):
            return f"unix:path={candidate}"
    return None


def clean_mac_list(macs: Any) -> list[str]:
    """Extract valid MAC addresses from various input formats."""
    if not macs:
        return []
    if isinstance(macs, str):
        macs = [macs]
    cleaned: list[str] = []
    for raw in macs:
        matches = _MAC_RE.findall(str(raw))
        if matches:
            cleaned.extend(m.upper() for m in matches)
        elif str(raw).strip():
            cleaned.append(str(raw).strip().upper())
    return cleaned


class DBusBluetoothScanner:
    """Manages D-Bus communication with BlueZ for tracking devices."""

    def __init__(self, hass: HomeAssistant, dbus_address: str | None = None) -> None:
        self._hass = hass
        self._dbus_address = dbus_address

    async def _connect(self) -> MessageBus:
        address = resolve_dbus_address(self._dbus_address)
        if address:
            try:
                return await MessageBus(bus_type=BusType.SYSTEM, bus_address=address).connect()
            except Exception as err:
                _LOGGER.error("Failed to connect to D-Bus at '%s': %s", address, err)
                raise
        try:
            return await MessageBus(bus_type=BusType.SYSTEM).connect()
        except Exception as err:
            _LOGGER.error("Failed to connect to system D-Bus: %s", err)
            raise

    async def _get_adapters(self, bus: MessageBus, selected_adapter: str = DEFAULT_ADAPTER) -> list[str]:
        adapters: list[str] = []
        try:
            res = await bus.call(Message(destination=BLUEZ_SERVICE, path="/", interface="org.freedesktop.DBus.ObjectManager", member="GetManagedObjects"))
        except Exception as err:
            _LOGGER.error("GetManagedObjects failed: %s", err)
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
            _LOGGER.warning("Adapter '%s' not found; using all: %s", target, adapters)
        return adapters

    async def async_list_system_adapters(self) -> list[str]:
        try:
            bus = await self._connect()
        except Exception:
            return []
        try:
            return await self._get_adapters(bus, selected_adapter="auto")
        finally:
            bus.disconnect()
            await bus.wait_for_disconnect()

    # ── ConnectDevice probe (for phones / classic Bluetooth) ──────────

    async def _connect_device(self, bus: MessageBus, adapter_path: str, mac: str) -> bool:
        device_path = f"{adapter_path}/dev_{mac.replace(':', '_')}"
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                res = await bus.call(Message(
                    destination=BLUEZ_SERVICE, interface=ADAPTER_INTERFACE,
                    path=adapter_path, member="ConnectDevice",
                    signature="a{sv}", body=[{"Address": Variant("s", mac)}],
                ))
        except asyncio.TimeoutError:
            return False
        except Exception as err:
            _LOGGER.debug("ConnectDevice error for %s: %s", mac, err)
            return False

        if res.message_type == MessageType.METHOD_RETURN:
            return True
        if res.message_type == MessageType.ERROR:
            if res.error_name == f"{BLUEZ_SERVICE}.Error.AlreadyExists":
                return True
        return False

    async def _disconnect_device(self, bus: MessageBus, adapter_path: str, mac: str) -> None:
        device_path = f"{adapter_path}/dev_{mac.replace(':', '_')}"
        try:
            await bus.call(Message(destination=BLUEZ_SERVICE, interface=DEVICE_INTERFACE, path=device_path, member="Disconnect"))
        except Exception:
            pass
        try:
            await bus.call(Message(destination=BLUEZ_SERVICE, interface=ADAPTER_INTERFACE, path=adapter_path, member="RemoveDevice", signature="o", body=[device_path]))
        except Exception:
            pass

    # ── BlueZ ObjectManager + ConnectDevice hybrid ────────────────────

    async def poll_devices(self, macs: list[str], adapter: str = DEFAULT_ADAPTER) -> dict[str, dict[str, Any]]:
        """BlueZ Layer 2+3: ObjectManager cache + ConnectDevice probe.

        Layer 1 (HA bluetooth cache) is handled by __init__.py.
        """
        results: dict[str, dict[str, Any]] = {
            mac.lower(): {"reachable": False, "name": None, "source": None} for mac in macs
        }
        if not macs:
            return results

        try:
            bus = await self._connect()
        except Exception as err:
            _LOGGER.error("D-Bus connection failed: %s", err)
            return results

        try:
            adapters = await self._get_adapters(bus, selected_adapter=adapter)
            if not adapters:
                return results

            # ── Layer 2: BlueZ ObjectManager cache ──
            try:
                res = await bus.call(Message(destination=BLUEZ_SERVICE, path="/", interface="org.freedesktop.DBus.ObjectManager", member="GetManagedObjects"))
                if res.message_type == MessageType.METHOD_RETURN and res.body:
                    for path, interfaces in res.body[0].items():
                        if DEVICE_INTERFACE not in interfaces:
                            continue
                        props = interfaces[DEVICE_INTERFACE]
                        address_val = props.get("Address", "")
                        address = address_val.value if isinstance(address_val, Variant) else address_val
                        if not isinstance(address, str) or not address:
                            continue
                        key = address.lower()
                        if key not in results:
                            continue
                        name_val = props.get("Name") or props.get("Alias", "")
                        name = name_val.value if isinstance(name_val, Variant) else name_val
                        results[key]["reachable"] = True
                        results[key]["name"] = name or address
                        results[key]["source"] = "BlueZ ObjectManager"
                        _LOGGER.debug("BlueZ cache hit: %s (%s)", address, name or "unnamed")
            except Exception as err:
                _LOGGER.warning("GetManagedObjects failed: %s", err)

            # ── Layer 3: ConnectDevice probe for unseen MACs ──
            missing = [mac for mac in results if not results[mac]["reachable"]]
            if missing:
                _LOGGER.debug("Probing via ConnectDevice: %s", [m.upper() for m in missing])

            for adp in adapters:
                adapter_path = f"{BLUEZ_PATH}/{adp}"
                for mac_lower in list(missing):
                    if results[mac_lower]["reachable"]:
                        continue
                    mac_upper = mac_lower.upper()
                    if await self._connect_device(bus, adapter_path, mac_upper):
                        results[mac_lower]["reachable"] = True
                        results[mac_lower]["name"] = mac_upper
                        results[mac_lower]["source"] = "BlueZ ConnectDevice"
                        _LOGGER.info("ConnectDevice SUCCESS: %s on %s", mac_upper, adp)
                        await self._disconnect_device(bus, adapter_path, mac_upper)
                    else:
                        _LOGGER.debug("ConnectDevice FAILED: %s on %s", mac_upper, adp)

        finally:
            bus.disconnect()
            await bus.wait_for_disconnect()

        for mac, data in results.items():
            _LOGGER.debug("poll %s: reachable=%s source=%s", mac, data["reachable"], data["source"])
        return results

    # ── Active discovery scan (for the nearby-device service) ─────────

    async def discover_nearby(self, timeout: int = 10, adapter: str = DEFAULT_ADAPTER) -> list[dict[str, Any]]:
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
                except Exception as err:
                    _LOGGER.debug("StartDiscovery on %s failed: %s", adp, err)
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
                    address_val = props.get("Address", "")
                    address = address_val.value if isinstance(address_val, Variant) else address_val
                    if not isinstance(address, str) or not address:
                        continue
                    name_val = props.get("Name") or props.get("Alias", "")
                    name = name_val.value if isinstance(name_val, Variant) else name_val
                    devices.append({"name": name or address, "mac": address, "adapter": adp})
        finally:
            bus.disconnect()
            await bus.wait_for_disconnect()
        return devices
