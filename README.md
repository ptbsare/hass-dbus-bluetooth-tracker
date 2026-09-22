# D-Bus Bluetooth Tracker for Home Assistant

[![GitHub Release](https://img.shields.github.io/github/v/release/ptbsare/hass-dbus-bluetooth-tracker)](https://github.com/ptbsare/hass-dbus-bluetooth-tracker/releases)
[![HACS Custom](https://img.shields.github.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/default)

[中文文档](README_zh.md) | **English**

A high-performance Home Assistant `device_tracker` integration that communicates directly with BlueZ via Linux System D-Bus. Fully configurable via UI (Config Flow & Options Flow) with Unique Entity IDs and hybrid live presence detection—no YAML required!

---

## 🌟 Key Features

* **Zero YAML Configuration**: Set up and manage tracked devices entirely through **Settings -> Devices & Services** in Home Assistant.
* **Unique Entity IDs**: Every tracked device generates a proper `unique_id`, allowing you to rename, assign areas, customize icons, or group entities in the UI without warnings.
* **Accurate Presence Detection (no stale-cache bugs)**:
  * **Live ConnectDevice Probe**: Every poll actively sends a low-level BlueZ link probe to each tracked device (phones, tablets, laptops). If the probe times out, the device is immediately marked `not_home`.
  * **No reliance on stale caches**: Home Assistant's Bluetooth integration cache and BlueZ's ObjectManager history are **never** used to decide presence, because they never expire. This guarantees a device that leaves is correctly reported as away.
  * **Automatic Node Cleanup**: Every probe is followed by `Disconnect` + `RemoveDevice`, so BlueZ D-Bus nodes created by probing never linger and cause false "home" states.
* **Seen Cooldown (`seen_interval_seconds`)**: After a device is confirmed home, further probes are skipped for the cooldown window, saving CPU, Bluetooth bandwidth, and phone battery—while still guaranteeing accurate away detection.
* **Multi-Adapter Support**: Automatically discovers all local Bluetooth controllers (`hci0`, `hci1`, etc.) and lets you select a specific adapter or auto-select all.
* **Nearby Scan Service**: Includes the `dbus_bluetooth_tracker.scan_nearby_devices` service to scan nearby devices and present a Markdown table in system notifications for easy MAC address copying.

---

## 🚀 Installation

### Method 1: HACS (Recommended)
1. Open **HACS** in your Home Assistant UI.
2. Click the three dots `...` in the top right corner -> **Custom repositories**.
3. Add repository URL: `https://github.com/ptbsare/hass-dbus-bluetooth-tracker` with Category **Integration**.
4. Search for **D-Bus Bluetooth Tracker** and click **Download**.
5. Restart Home Assistant.

### Method 2: Manual Installation
Copy the `custom_components/dbus_bluetooth_tracker` directory into your Home Assistant `/config/custom_components/` folder, then restart Home Assistant.

---

## ⚙️ Configuration & Usage

### 1. Add the Integration
1. Go to **Settings -> Devices & Services**.
2. Click **Add Integration** in the bottom right corner.
3. Search for **D-Bus Bluetooth Tracker** and follow the setup wizard.

### 2. Discover Nearby Bluetooth MAC Addresses
1. Go to **Developer Tools -> Services**.
2. Select `dbus_bluetooth_tracker.scan_nearby_devices` and click **Call Service** (you can optionally specify a `timeout` in seconds).
3. Open the **Notifications** panel in Home Assistant.
4. Copy the MAC address of the device you want to track (e.g., `AA:BB:CC:DD:EE:FF`).

### 3. Configure Tracked MACs & Options
1. Go to **Settings -> Devices & Services -> D-Bus Bluetooth Tracker**.
2. Click **Options**.
3. Paste target MAC addresses into **Tracked MAC List** (supports comma or newline separation; auto-formats glued MAC strings).
4. Choose your preferred Bluetooth Adapter (`auto`, `hci0`, `hci1`, etc.).
5. Set **Scan Interval**, **Seen Cooldown Interval**, and **Consider Home Delay**.
6. Click **Submit**. Entities with Unique IDs will be created automatically.

---

## 🛠️ Requirements & Container Environment

* Home Assistant running on Linux with BlueZ (`bluetooth.service`) active.
* **Docker / Container Users**: You MUST mount the host D-Bus system bus socket into the container:

```yaml
# docker-compose.yml example
services:
  homeassistant:
    image: ghcr.io/home-assistant/home-assistant:stable
    network_mode: host
    privileged: true
    cap_add:
      - NET_ADMIN
      - NET_RAW
    volumes:
      - /config:/config
      - /var/run/dbus:/var/run/dbus:ro    # Required: Host D-Bus socket
    restart: unless-stopped
```

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for details.