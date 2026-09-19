# D-Bus Bluetooth Tracker for Home Assistant

通过 Linux 系统 D-Bus 直接与 BlueZ 通信的高性能 Home Assistant 蓝牙追踪器（`device_tracker`）。支持纯 UI 图形界面配置，完全不依赖 YAML！

---

## 🌟 主要特性

* 界面化添加与设置：**零 YAML 配置**，直接在 Home Assistant **设置 -> 设备与服务** 中一键添加集成。
* 拥有唯一标识符（Unique ID）：**每个追踪设备都有专属 Entity Unique ID**，可以在 Home Assistant UI 中自由重命名、更改图标、指定房间区域。
* 图形化 MAC 管理：点击集成的“选项（Options）”按钮，随时添加、修改或删除要追踪的蓝牙设备 MAC 地址列表（支持逗号分隔或多行粘贴）。
* 一键周边设备发现服务：内置 `dbus_bluetooth_tracker.scan_nearby_devices` 服务，可主动扫描周围蓝牙设备，并在 Home Assistant UI 的通知中心展示设备名称、MAC 地址与 RSSI 信号强度，方便快速复制。
* 优化的在线判定算法：支持自定义 `consider_home`（离线延时），防止手机锁屏或蓝牙休眠时出现频繁跳变。

---

## 🚀 安装方法

### 方式 1：通过 HACS 安装（推荐）
1. 打开 Home Assistant 的 **HACS** 面板。
2. 点击右上角的三个点 `...` -> **自定义存储库 (Custom repositories)**。
3. 输入本仓库地址 `https://github.com/ptbsare/hass-dbus-bluetooth-tracker`，类型选择 **集成 (Integration)** 并添加。
4. 在 HACS 中搜索并下载 **D-Bus Bluetooth Tracker**。
5. 重启 Home Assistant。

### 方式 2：手动安装
把仓库中的 `custom_components/dbus_bluetooth_tracker` 文件夹直接复制到你的 Home Assistant 配置目录下的 `custom_components` 文件夹内，然后重启 Home Assistant。

---

## ⚙️ 配置与使用指南

### 1. 添加集成
1. 进入 Home Assistant **设置 -> 设备与服务**。
2. 点击右下角 **添加集成 (Add Integration)**。
3. 搜索 **D-Bus Bluetooth Tracker** 并点击添加。

### 2. 获取并添加要追踪的蓝牙设备 MAC 地址
#### 方法 A：使用内置扫描服务（最简便）
1. 进入 **开发者工具 -> 服务 (Developer Tools -> Services)**。
2. 选择服务 `dbus_bluetooth_tracker.scan_nearby_devices` 并点击 **调用服务**。
3. 系统会在 Home Assistant 左侧菜单的 **通知** 中推送附近扫描到的蓝牙设备列表（包含名称、MAC 地址和信号强度）。
4. 复制你需要追踪的设备 MAC 地址（例如 `AA:BB:CC:DD:EE:FF`）。

#### 方法 B：在集成选项中填写
1. 进入 **设置 -> 设备与服务 -> D-Bus Bluetooth Tracker**。
2. 点击卡片上的 **选项 (Options)**。
3. 在 **要追踪的蓝牙 MAC 列表** 中粘贴或输入 MAC 地址（支持用逗号分隔或每行一个 MAC 地址）。
4. （可选）调整 **查询扫描时间间隔**（默认 30 秒）与 **离开判定延迟**（默认 180 秒）。
5. 点击 **提交**。集成会自动加载对应的实体，你就可以在 UI 中管理这些设备了！

---

## 🛠️ 前提条件
* Home Assistant 运行在 Linux 环境下，且宿主机运行有 BlueZ（D-Bus 系统总线 `/var/run/dbus/system_bus_socket` 正常挂载）。
* 如需增强未配对设备的响应度，建议在 BlueZ 配置中开启实验性功能（`--experimental` 标志）。
