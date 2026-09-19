"""The D-Bus Bluetooth Tracker integration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.bluetooth import async_discovered_service_info
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_ADAPTER,
    CONF_CONSIDER_HOME,
    CONF_DBUS_ADDRESS,
    CONF_INTERVAL,
    CONF_SEEN_INTERVAL,
    CONF_TRACKED_MACS,
    DEFAULT_ADAPTER,
    DEFAULT_CONSIDER_HOME,
    DEFAULT_INTERVAL,
    DEFAULT_SEEN_INTERVAL,
    DOMAIN,
    PLATFORMS,
    SCAN_NEARBY_DEVICES_SCHEMA,
)
from .scanner import DBusBluetoothScanner, clean_mac_list

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the dbus_bluetooth_tracker component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up dbus_bluetooth_tracker from a config entry."""
    dbus_address = entry.options.get(
        CONF_DBUS_ADDRESS, entry.data.get(CONF_DBUS_ADDRESS, None)
    )
    scanner = DBusBluetoothScanner(hass, dbus_address=dbus_address)

    tracked_macs = clean_mac_list(
        entry.options.get(CONF_TRACKED_MACS, entry.data.get(CONF_TRACKED_MACS, []))
    )
    selected_adapter = entry.options.get(
        CONF_ADAPTER, entry.data.get(CONF_ADAPTER, DEFAULT_ADAPTER)
    )
    interval_seconds = entry.options.get(
        CONF_INTERVAL, entry.data.get(CONF_INTERVAL, DEFAULT_INTERVAL)
    )
    consider_home_seconds = entry.options.get(
        CONF_CONSIDER_HOME, entry.data.get(CONF_CONSIDER_HOME, DEFAULT_CONSIDER_HOME)
    )
    seen_interval_seconds = entry.options.get(
        CONF_SEEN_INTERVAL,
        entry.data.get(CONF_SEEN_INTERVAL, DEFAULT_SEEN_INTERVAL),
    )

    _LOGGER.info(
        "Setting up dbus_bluetooth_tracker entry with %d MACs to track. Interval: %ds, "
        "Seen interval: %ds, Consider home: %ds",
        len(tracked_macs),
        interval_seconds,
        seen_interval_seconds,
        consider_home_seconds,
    )

    # Store configurations in options so options flow can pre-fill
    if not entry.options:
        hass.config_entries.async_update_entry(
            entry,
            options={
                CONF_TRACKED_MACS: tracked_macs,
                CONF_INTERVAL: interval_seconds,
                CONF_CONSIDER_HOME: consider_home_seconds,
                CONF_ADAPTER: selected_adapter,
                CONF_SEEN_INTERVAL: seen_interval_seconds,
            },
        )

    # Keep track of when each device was last successfully seen
    device_last_seen: dict[str, datetime] = {}
    device_last_data: dict[str, dict[str, Any]] = {}

    async def async_update_data() -> dict[str, Any]:
        """Fetch tracking data from D-Bus scanner with a 3-layer hybrid detection.

        Layer 1: HA Bluetooth cache (zero D-Bus cost, matches passive advertising)
        Layer 2: BlueZ ObjectManager cache (D-Bus lookup, matches active BLE)
        Layer 3: BlueZ ConnectDevice probe (D-Bus actively connects, matches classic BT/phones)
        """
        current_tracked = clean_mac_list(
            entry.options.get(CONF_TRACKED_MACS, [])
        )
        if not current_tracked:
            _LOGGER.debug("No tracked MACs configured.")
            return {}

        current_adapter = entry.options.get(CONF_ADAPTER, DEFAULT_ADAPTER)
        current_seen_interval = entry.options.get(
            CONF_SEEN_INTERVAL, DEFAULT_SEEN_INTERVAL
        )

        now = datetime.now()
        results: dict[str, dict[str, Any]] = {
            mac: {"reachable": False, "name": None, "source": None}
            for mac in current_tracked
        }

        # ── Step 0: Check `seen_interval` optimization ──
        need_scan: list[str] = []
        for mac in current_tracked:
            if current_seen_interval > 0:
                last = device_last_seen.get(mac)
                if last is not None:
                    elapsed = (now - last).total_seconds()
                    if elapsed < current_seen_interval:
                        cached = device_last_data.get(
                            mac, {"reachable": True, "name": None, "source": "seen_interval cache"}
                        )
                        results[mac] = dict(cached)
                        _LOGGER.debug(
                            "Device %s seen %0.0fs ago (< seen_interval %ds), using cached data",
                            mac,
                            elapsed,
                            current_seen_interval,
                        )
                        continue
            need_scan.append(mac)

        if not need_scan:
            return results

        # ── Layer 1: HA passive Bluetooth integration cache ──
        _LOGGER.debug("Layer 1: Querying HA Bluetooth integration cache for: %s", need_scan)
        try:
            ha_devices: dict[str, Any] = {}
            for info in async_discovered_service_info(hass, connectable=False):
                ha_devices[info.address.upper()] = info
            for info in async_discovered_service_info(hass, connectable=True):
                ha_devices.setdefault(info.address.upper(), info)

            for mac in list(need_scan):
                info = ha_devices.get(mac)
                if info:
                    results[mac] = {
                        "reachable": True,
                        "name": info.name or mac,
                        "source": "HA Bluetooth Integration",
                    }
                    device_last_seen[mac] = now
                    device_last_data[mac] = results[mac]
                    need_scan.remove(mac)
                    _LOGGER.info(
                        "Layer 1 SUCCESS: Device %s found in HA Bluetooth cache (%s)",
                        mac,
                        info.name or "unnamed",
                    )
        except HomeAssistantError as err:
            _LOGGER.debug("Could not query HA Bluetooth cache: %s", err)

        # ── Layer 2 & 3: BlueZ D-Bus ObjectManager + ConnectDevice Active Probe ──
        if need_scan:
            _LOGGER.debug("Layer 2+3: Querying BlueZ D-Bus for remaining: %s", need_scan)
            try:
                polled = await scanner.poll_devices(need_scan, adapter=current_adapter)
                for mac, data in polled.items():
                    if data.get("reachable"):
                        results[mac] = data
                        device_last_seen[mac] = now
                        device_last_data[mac] = data
            except Exception as err:
                raise UpdateFailed(f"Error communicating with D-Bus: {err}") from err

        return results

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_coordinator",
        update_method=async_update_data,
        update_interval=timedelta(seconds=interval_seconds),
    )

    hass.data[DOMAIN][entry.entry_id] = {
        "scanner": scanner,
        "coordinator": coordinator,
        "device_last_seen": device_last_seen,
        "tracked_macs": tracked_macs,
        "adapter": selected_adapter,
        "seen_interval": seen_interval_seconds,
    }

    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # ── scan_nearby_devices service ──
    async def handle_scan_service(call: ServiceCall) -> None:
        """Scan nearby devices."""
        timeout = int(call.data.get("timeout", 10))
        _LOGGER.info("Starting scan for nearby devices (timeout=%ss)...", timeout)

        devices_dict: dict[str, dict[str, Any]] = {}
        try:
            discovered_ha = list(async_discovered_service_info(hass, connectable=False))
            discovered_ha_conn = list(async_discovered_service_info(hass, connectable=True))
            for info in discovered_ha + discovered_ha_conn:
                mac = info.address.upper()
                devices_dict[mac] = {
                    "name": info.name or "Unknown",
                    "mac": mac,
                    "source": "HA Bluetooth (passive cache)",
                }
        except HomeAssistantError as err:
            _LOGGER.warning("HA Bluetooth cache read failed: %s", err)

        current_adapter = entry.options.get(CONF_ADAPTER, DEFAULT_ADAPTER)
        try:
            dbus_devices = await scanner.discover_nearby(
                timeout=timeout, adapter=current_adapter
            )
            for dev in dbus_devices:
                mac = dev["mac"].upper()
                if mac not in devices_dict:
                    devices_dict[mac] = {
                        "name": dev["name"] or "Unknown",
                        "mac": mac,
                        "source": f"Local BlueZ ({dev['adapter']})",
                    }
                else:
                    if devices_dict[mac]["name"] == "Unknown" and dev["name"]:
                        devices_dict[mac]["name"] = dev["name"]
        except Exception as err:
            _LOGGER.warning("BlueZ D-Bus scan failed: %s", err)

        if not devices_dict:
            message = "📡 本次扫描没有在附近发现任何活动的蓝牙设备。"
        else:
            sorted_devices = sorted(devices_dict.values(), key=lambda x: x["name"])
            message = (
                "请复制您要追踪的 MAC 地址并添加到集成“选项”中：\n\n"
                "| 设备名称 (Name) | 蓝牙 MAC 地址 (Address) | 扫描源 (Source) |\n"
                "| :--- | :--- | :--- |\n"
            )
            for dev in sorted_devices:
                message += f"| **{dev['name']}** | `{dev['mac']}` | {dev['source']} |\n"

        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "D-Bus 蓝牙设备发现结果",
                "message": message,
                "notification_id": "dbus_bluetooth_tracker_discovery_result",
            },
        )
        _LOGGER.info("Discovery complete. Notification sent.")

    hass.services.async_register(
        DOMAIN, "scan_nearby_devices", handle_scan_service, schema=SCAN_NEARBY_DEVICES_SCHEMA
    )
    entry.async_on_unload(entry.add_update_listener(entry_update_listener))
    return True


async def entry_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if hass.services.has_service(DOMAIN, "scan_nearby_devices"):
            hass.services.async_remove(DOMAIN, "scan_nearby_devices")
    return unload_ok
