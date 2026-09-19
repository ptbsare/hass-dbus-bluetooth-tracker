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

from . import scanner as scanner_module
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
from .scanner import DBusBluetoothScanner

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

    # Convert tracked macs string list to upper and clean whitespaces
    import re
    _MAC_RE = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")
    def clean_mac_list(macs: Any) -> list[str]:
        if not macs:
            return []
        if isinstance(macs, str):
            macs = [macs]
        cleaned = []
        for raw in macs:
            matches = _MAC_RE.findall(str(raw))
            if matches:
                cleaned.extend(m.upper() for m in matches)
            elif str(raw).strip():
                cleaned.append(str(raw).strip().upper())
        return cleaned

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
        """Fetch tracking data from D-Bus scanner."""
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
        results: dict[str, dict[str, Any]] = {}
        need_scan: list[str] = []

        for mac in current_tracked:
            if current_seen_interval > 0:
                last = device_last_seen.get(mac)
                if last is not None:
                    elapsed = (now - last).total_seconds()
                    if elapsed < current_seen_interval:
                        # Keep cached result including last known RSSI and name
                        cached = device_last_data.get(
                            mac, {"reachable": True, "rssi": None, "name": None}
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

        if need_scan:
            _LOGGER.debug(
                "Scanning %d device(s) via adapter '%s': %s",
                len(need_scan),
                current_adapter,
                need_scan,
            )
            try:
                polled = await scanner.poll_devices(need_scan, adapter=current_adapter)
                for mac, data in polled.items():
                    results[mac] = data
                    if data.get("reachable"):
                        device_last_seen[mac] = now
                        device_last_data[mac] = data
            except Exception as err:
                raise UpdateFailed(f"Error communicating with D-Bus: {err}") from err

        # Supplement RSSI from HA Bluetooth Integration cache if BlueZ has no valid value
        try:
            ha_by_mac: dict[str, Any] = {}
            for info in async_discovered_service_info(hass, connectable=False):
                ha_by_mac[info.address.upper()] = info
            for info in async_discovered_service_info(hass, connectable=True):
                ha_by_mac.setdefault(info.address.upper(), info)

            for mac in results:
                info = ha_by_mac.get(mac)
                if not info or results[mac].get("rssi") is not None:
                    continue
                if scanner_module._is_valid_rssi(info.rssi):
                    results[mac]["rssi"] = info.rssi
        except HomeAssistantError:
            pass

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

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Forward the setup to the platforms (device_tracker)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register the service to scan nearby devices and notify users
    async def handle_scan_service(call: ServiceCall) -> None:
        """Scan nearby bluetooth devices and show a persistent notification."""
        timeout = int(call.data.get("timeout", 10))
        _LOGGER.info(
            "Starting service-triggered scan for nearby bluetooth devices "
            "(timeout=%ss)...",
            timeout,
        )

        devices_dict: dict[str, dict[str, Any]] = {}
        ha_bt_error: str | None = None

        # 1. Gather from built-in HA bluetooth component (both connectable & non-connectable)
        try:
            discovered_ha = list(async_discovered_service_info(hass, connectable=False))
            discovered_ha_conn = list(async_discovered_service_info(hass, connectable=True))
            for info in discovered_ha + discovered_ha_conn:
                mac = info.address.upper()
                rssi = info.rssi if scanner_module._is_valid_rssi(info.rssi) else None
                devices_dict[mac] = {
                    "name": info.name or "Unknown",
                    "mac": mac,
                    "rssi": rssi,
                    "source": "HA Bluetooth (passive cache)",
                }
            _LOGGER.debug(
                "HA bluetooth passive cache: %d non-connectable + %d connectable devices",
                len(discovered_ha),
                len(discovered_ha_conn),
            )
        except HomeAssistantError as err:
            ha_bt_error = str(err)
            _LOGGER.warning(
                "Cannot read HA bluetooth cache: %s. Is the built-in `bluetooth` integration set up?",
                err,
            )

        # 2. Actively scan via BlueZ D-Bus (respecting the user-provided timeout & adapter)
        current_adapter = entry.options.get(CONF_ADAPTER, DEFAULT_ADAPTER)
        try:
            dbus_devices = await scanner.discover_nearby(
                timeout=timeout, adapter=current_adapter
            )
            _LOGGER.debug(
                "BlueZ active scan returned %d device(s) after %ss on adapter '%s'",
                len(dbus_devices),
                timeout,
                current_adapter,
            )
            for dev in dbus_devices:
                mac = dev["mac"].upper()
                if mac not in devices_dict:
                    devices_dict[mac] = {
                        "name": dev["name"] or "Unknown",
                        "mac": mac,
                        "rssi": dev["rssi"],
                        "source": f"Local BlueZ ({dev['adapter']})",
                    }
                else:
                    # Prefer a real RSSI measurement over None
                    if devices_dict[mac]["rssi"] is None and dev["rssi"] is not None:
                        devices_dict[mac]["rssi"] = dev["rssi"]
                    if devices_dict[mac]["name"] == "Unknown" and dev["name"]:
                        devices_dict[mac]["name"] = dev["name"]
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("BlueZ D-Bus discovery failed: %s", err)

        # 3. Format and send system notification
        if not devices_dict:
            diagnoses = [
                f"- HA 蓝牙集成缓存: {'不可用 (' + ha_bt_error + ')' if ha_bt_error else '为空 (设备可能已停止广播，缓存过期)'}",
                "- BlueZ D-Bus 主动扫描: 未返回设备或异常（见 Home Assistant 日志）",
            ]
            message = (
                "📡 本次扫描没有在附近发现任何蓝牙设备\n\n"
                "诊断提示：\n" + "\n".join(diagnoses) + "\n\n"
                "请确认：\n"
                "  • 要追踪的蓝牙设备正在开机并处于广播范围内\n"
                "  • 如果运行在容器里，请将宿主机 D-Bus socket 挂载到容器（-v /var/run/dbus:/var/run/dbus:ro）\n"
            )
        else:
            sorted_devices = sorted(
                devices_dict.values(),
                key=lambda x: (x["rssi"] if isinstance(x["rssi"], (int, float)) else -1000),
                reverse=True,
            )
            message = (
                "请复制您要追踪的 MAC 地址，并在“设置 -> 设备与服务 -> Bluetooth Tracker -> 选项”中添加：\n\n"
                "| 设备名称 (Name) | 蓝牙 MAC 地址 (Address) | 信号强度 (RSSI) | 扫描源 (Source) |\n"
                "| :--- | :--- | :--- | :--- |\n"
            )
            for dev in sorted_devices:
                rssi_str = f"{dev['rssi']} dBm" if isinstance(dev["rssi"], (int, float)) else "Unknown"
                message += f"| **{dev['name']}** | `{dev['mac']}` | {rssi_str} | {dev['source']} |\n"

        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "D-Bus 蓝牙设备发现结果",
                "message": message,
                "notification_id": "dbus_bluetooth_tracker_discovery_result",
            },
        )
        _LOGGER.info("Discovery complete. Notification sent to Home Assistant front-end.")

    hass.services.async_register(
        DOMAIN, "scan_nearby_devices", handle_scan_service, schema=SCAN_NEARBY_DEVICES_SCHEMA
    )

    # Listen for configuration changes
    entry.async_on_unload(entry.add_update_listener(entry_update_listener))

    return True


async def entry_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options or config update and reload the integration."""
    _LOGGER.info("Configuration updated, reloading dbus_bluetooth_tracker integration...")
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading dbus_bluetooth_tracker config entry...")
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if hass.services.has_service(DOMAIN, "scan_nearby_devices"):
            hass.services.async_remove(DOMAIN, "scan_nearby_devices")
    return unload_ok