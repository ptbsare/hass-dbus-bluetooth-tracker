# D-Bus Bluetooth Tracker for Home Assistant

**English** | [中文说明](README_zh.md)

通过 Linux 系统 D-Bus 直接与 BlueZ 通信的高性能 Home Assistant 蓝牙追踪器（`device_tracker`）。支持纯 UI 图形界面配置，完全不依赖 YAML，内置精确的双层混合在线判定算法！

---

## 🌟 主要特性

* **零 YAML 配置**：直接在 Home Assistant **设置 -> 设备与服务** 中一键添加与管理。
* **拥有唯一标识符 (Unique ID)**：每个追踪设备都有专属 Entity Unique ID，可以在 Home Assistant UI 中自由重命名、更改图标、指定房间区域。
* **精确的双层混合在线判定算法（杜绝离而不退/死状态）**：
  1. **Layer 1 (HA 蓝牙缓存)**：零 D-Bus 开销，匹配 Home Assistant 内置蓝牙捕捉到的广播。
  2. **Layer 3 (BlueZ ConnectDevice 实时链路探针)**：针对休眠或不主动广播的手机/经典蓝牙，发起底层无感链路探针。探针超时即判定为离家。
  3. **自动节点清理**：每次探针后都会执行 `Disconnect` + `RemoveDevice`，确保 BlueZ 中不会残留失效 D-Bus 节点，防止设备离家后依然被误判为"在家"。
* **在线冷却优化 (`seen_interval_seconds`)**：已确认在线的设备在冷却期内自动跳过频繁的硬件扫描，大幅降低 CPU、D-Bus 总线开销与手机电量消耗。
* **多适配器支持**：自动识别本地系统的所有蓝牙控制器（如 `hci0`、`hci1` 等），支持自动全部或定点指定。
* **一键周边设备发现服务**：内置 `dbus_bluetooth_tracker.scan_nearby_devices` 服务，可在 Home Assistant 通知面板展示附近发现的设备列表，方便复制 MAC 地址。

---

## 🚀 安装方法

### 方式 1：通过 HACS 安装（推荐）
1. 打开 Home Assistant 的 **HACS** 面板。
2. 点击右上角 `...` -> **自定义存储库 (Custom repositories)**。
3. 输入本仓库地址 `https://github.com/ptbsare/hass-dbus-bluetooth-tracker`，类型选择 **集成 (Integration)** 并添加。
4. 搜索并下载 **D-Bus Bluetooth Tracker**。
5. 重启 Home Assistant。

### 方式 2：手动安装
把仓库中的 `custom_components/dbus_bluetooth_tracker` 文件夹复制到 Home Assistant 配置目录下的 `custom_components` 文件夹内，然后重启 Home Assistant。

---

## ⚙️ 配置与使用指南

### 1. 添加集成
1. 进入 Home Assistant **设置 -> 设备与服务**。
2. 点击右下角 **添加集成 (Add Integration)**。
3. 搜索 **D-Bus Bluetooth Tracker** 并添加。

### 2. 获取并添加要追踪的蓝牙 MAC 地址
1. 进入 **开发者工具 -> 服务**，调用 `dbus_bluetooth_tracker.scan_nearby_devices` 服务（可自定义超时时间）。
2. 在 Home Assistant **通知** 面板中查看扫描到的设备列表。
3. 复制要追踪的 MAC 地址。
4. 进入 **设置 -> 设备与服务 -> D-Bus Bluetooth Tracker**，点击 **选项 (Options)**，粘贴 MAC 地址列表并提交。

---

## 🛠️ Docker 容器环境准备

如果 Home Assistant 运行在 Docker 容器中，必须将宿主机的 D-Bus Socket 挂载进容器：

```yaml
# docker-compose.yml 示例
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
      - /var/run/dbus:/var/run/dbus:ro    # 必须：挂载宿主机 D-Bus 套接字
    restart: unless-stopped
```