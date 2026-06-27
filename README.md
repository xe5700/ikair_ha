# iKair BLE Thermometer for Home Assistant

Home Assistant 自定义组件，用于读取 iKair 婴儿温湿度计（CC2541 + SHT20）的温度和湿度数据。

## 安装

### HACS（推荐）

1. 在 HACS 中添加自定义仓库：`https://github.com/xe5700/ikair_ha`
2. 选择类型：`Integration`
3. 搜索 "iKair BLE Thermometer" 并安装
4. 重启 Home Assistant

### 手动安装

将 `custom_components/ikair/` 目录复制到 Home Assistant 的 `config/custom_components/` 目录下，然后重启。

## 配置

1. 重启后，HA 会自动发现附近的 iKair 设备
2. 前往 `配置 → 设备与服务 → 添加集成 → iKair BLE Thermometer`
3. 选择你的 iKair 设备
4. 完成

## 工作原理

通过 BLE GATT 连接读取 iKair 温度计的实时数据：

1. 扫描并连接设备
2. 读取设备序列号，完成绑定验证
3. 开启实时数据通知
4. 每 30 秒轮询一次温度/湿度
5. 断开连接（省电）

## 传感器

- **温度** — SHT20，精度 ±0.3°C
- **湿度** — SHT20，精度 ±3% RH
- **电量** — 电池剩余电量

## 适配器兼容性

| 适配器 | 兼容性 | 备注 |
|--------|--------|------|
| ESP32 BT Proxy | ✅ 推荐 | ESPHome 设置 `esp32_ble_bt_4_2_compatibility: true` |

## 协议

基于 iKair Android App 逆向工程。硬件方案：CC2541 + SHT20 + HT9B92。

## 许可

GNU General Public License v3.0
