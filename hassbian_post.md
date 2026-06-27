分享一个自制的 iKair 婴儿温湿度计 Home Assistant 插件

## 背景

捡垃圾的可能知道 iKair 这个婴儿温湿度计，通过蓝牙连接手机 app 查看温度和湿度。但原厂 app 比较简陋，也没有 HA 集成，想着能不能把它接到 Home Assistant 里。

硬件方案：CC2541 (BLE SoC) + SHT20 (温湿度传感器) + HT9B92 (LCD 驱动)

通过逆向 Android app 的 Java 代码搞清楚了蓝牙通信协议，写了个 HA 自定义组件。

## 功能

- 温度、湿度传感器，自动发现
- 支持蓝牙 4.2 兼容模式（解决老设备连接问题）
- 多国语言（中文/英文）
- 支持 HACS 安装
- 使用 bleak-retry-connector 自动重连，连接稳定

## 安装方法

### HACS 安装（推荐）

1. HACS → 自定义存储库 → 添加：
   - 仓库地址：`https://github.com/xe5700/ikair_ha`
   - 类型：Integration
2. 搜索 "iKair BLE Thermometer" 安装
3. 重启 HA
4. 配置 → 设备与服务 → 添加集成 → 搜索 iKair

### 手动安装

把 `custom_components/ikair/` 复制到 HA 的 `custom_components/` 目录，重启即可。

## 使用说明

- 首次添加后约 30 秒内获取第一次数据
- 之后每 30 秒自动更新温度/湿度
- 设备断开后自动重试连接
- 支持 HA 蓝牙网关（手机 app、ESP32 蓝牙代理等）

## 适配说明

因为这个温度计是蓝牙 4.0 的老设备（CC2541 芯片），我之前用简单的获取脚本测试，可能是Linux驱动配合新的蓝牙版本的会出问题，Windows和Android测试（iKair官方app）似乎是没有问题的，建议用esp32蓝牙代理做蓝牙网关，或者使用一些老一点的蓝牙适配器插在hass主机上。


## GitHub

https://github.com/xe5700/ikair_ha

欢迎 star、issue、PR。
