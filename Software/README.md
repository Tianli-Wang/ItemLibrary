# ESP-NOW 点灯固件

系统链路：WebUI 通过 115200 baud Web Serial 向 ESP32-S3 master 发送位置，master
使用 ESP-NOW 广播，匹配 `box_id` 的 ESP8266 slave 点亮对应的可寻址 LED。

串口命令格式：

```text
box_id:1,led_id:15
```

## 工程与配置

- `master`：ESP32-S3 + ESP-IDF v6；
- `slave`：ESP8266 + Arduino Core + PlatformIO；
- 两端复用 `common/espnow_protocol.h` 中固定为 20 字节的广播协议。

烧录不同 slave 前，修改 `slave/platformio.ini`：

- `ITEMLIB_BOX_ID`：这块从机负责的料盒编号；
- `ITEMLIB_LED_DATA_PIN`：位置灯带数据引脚，默认 ESP8266 GPIO4；
- `ITEMLIB_BOX_LED_PIN`：盒体灯带数据引脚，默认 ESP8266 GPIO5；
- `ITEMLIB_LED_COUNT`：位置灯数量，默认 81；
- `ITEMLIB_BOX_LED_COUNT`：盒体灯数量，默认 12；
- `ITEMLIB_LED_BRIGHTNESS`：FastLED 总亮度，默认 96。

master 与所有 slave 的 `ITEMLIB_ESPNOW_CHANNEL` 必须相同，默认信道 1。slave 的常用命令：

```bash
pio run
pio run -t upload
pio device monitor
```

WebUI 中点击右下角“连接点灯主控”，选择 master 的 USB 串口。之后在 BOM 中选中元件，
WebUI 会自动发送其 `box_id` 和 `led_id`。`led_id` 从 1 开始；发送 0 可熄灭当前灯。

master 的 WebUI 数据从 UART0（115200 baud）进入，接收到的原始命令、解析结果和
ESP-NOW 发送结果会从 ESP32-S3 原生 USB Serial/JTAG 口输出到 monitor。使用带两个
USB 接口的开发板时，WebUI 选择 USB-UART 接口，monitor 选择原生 USB 接口。

## 接线提示

灯带与 ESP8266 必须共地。LED 数量较多时请使用独立 5 V 电源，不要由 ESP8266 模块的
3.3 V 引脚供电；如数据传输不稳定，建议增加 3.3 V 到 5 V 数据电平转换。
