

// ===================================================================
// =================== 从设备 (SLAVE) 代码 ============================
// ===================================================================
#if defined(FIRMWARE_IS_SLAVE)
#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include "common_protocol.h" // 引入我们的协议
#include <esp_wifi.h>
#include <FastLED.h>

// --- LED 配置 ---
#define DATA_PIN 18 // WS2812 数据引脚 (你可以修改)
#define LED_TYPE WS2812B
#define COLOR_ORDER GRB
#define NUM_LEDS 81   // 81个元件
#define BRIGHTNESS 96 // 注意供电！
// --- ---

CRGB leds[NUM_LEDS];
struct_message incomingMessage;

// 核心：ESP-NOW 接收回调
void OnDataRecv(const uint8_t *mac_addr, const uint8_t *incomingData, int len)
{
  memcpy(&incomingMessage, incomingData, sizeof(incomingMessage));

  Serial.printf("收到命令: LED %d, Color (%d, %d, %d)\n",
                incomingMessage.led_id, incomingMessage.r, incomingMessage.g, incomingMessage.b);

  // 规则 -1: 熄灭所有
  if (incomingMessage.led_id == -1)
  {
    FastLED.clear();
  }
  // 规则 0-80: 点亮指定灯
  else if (incomingMessage.led_id >= 0 && incomingMessage.led_id < NUM_LEDS)
  {
    FastLED.clear(); // 先熄灭所有
    leds[incomingMessage.led_id] = CRGB(incomingMessage.r,
                                        incomingMessage.g,
                                        incomingMessage.b);
  }

  FastLED.show(); // 更新灯带
}

void setup()
{
  Serial.begin(115200);
  Serial.println("==================================");
  Serial.println("  从设备 (Slave) 启动...");
  Serial.println("==================================");

  // 1. 初始化 FastLED
  FastLED.addLeds<LED_TYPE, DATA_PIN, COLOR_ORDER>(leds, NUM_LEDS);
  FastLED.setBrightness(BRIGHTNESS);
  FastLED.clear();
  FastLED.show();
  Serial.println("FastLED (81灯) 初始化完毕。");

  // 2. 初始化 ESP-NOW
  WiFi.mode(WIFI_STA);

  esp_wifi_set_channel(1, WIFI_SECOND_CHAN_NONE);
  Serial.println("已强制设置 Wi-Fi 在信道 1");
  

  // ！！！【最关键的一步】！！！
  // 打印本设备的MAC地址
  Serial.print("\n[关键信息] 本设备 MAC 地址: ");
  Serial.println(WiFi.macAddress());
  Serial.println("请将此 MAC 地址添加到主控的路由表中！\n");

  if (esp_now_init() != ESP_OK)
  {
    Serial.println("错误: ESP-NOW 初始化失败。");
    return;
  }

  // 3. 注册接收回调函数
  esp_now_register_recv_cb(OnDataRecv);
  Serial.println("系统就绪，等待主控命令...");
}

void loop()
{
  // // 无事可做，全靠回调
  delay(10000);
  printf("I'm still alive.\r\n");
  delay(5000);
}

#endif // FIRMWARE_IS_SLAVE

// ===================================================================
// =================== 主设备 (MASTER) 代码 ===========================
// ===================================================================
#if defined(FIRMWARE_IS_MASTER)

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include "common_protocol.h" // 引入我们的协议
#include <esp_wifi.h>
#include <FastLED.h>

// -------------------------------------------------------------
// ！！！【你需要修改这里】！！！
// 这是你的"路由表"。把从控的MAC地址抄到这里。
// -------------------------------------------------------------

// 格式: { 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF }
// 示例：我有2个元件库
uint8_t mac_box_1[] = {0x44, 0x17, 0x93, 0x3B, 0x69, 0x6F}; // 盒子1: 电阻库
// uint8_t mac_box_2[] = {0xAA, 0xBB, 0xCC, 0x11, 0x22, 0x02}; // 盒子2: 电容库
// uint8_t mac_box_3[] = { ... }; // 你可以继续添加

// 路由表数组
uint8_t *mac_address_table[] = {
    mac_box_1, // 索引 0 (对应 box_id 1)
               // mac_box_2  // 索引 1 (对应 box_id 2)
               // mac_box_3  // 索引 2 (对应 box_id 3)
};
const int num_boxes = sizeof(mac_address_table) / sizeof(mac_address_table[0]);
// -------------------------------------------------------------

struct_message commandMessage; // 用来发送命令的结构体

// ESP-NOW 发送回调 (用于调试)
void OnDataSent(const uint8_t *mac_addr, esp_now_send_status_t status)
{
  Serial.print("  ESP-NOW 发送状态: ");
  Serial.println(status == ESP_NOW_SEND_SUCCESS ? "成功" : "失败");
}

void setup()
{
  Serial.begin(115200);
  Serial.println("==================================");
  Serial.println("  主设备 (Master) 启动...");
  Serial.println("==================================");

  // 1. 初始化 ESP-NOW
  WiFi.mode(WIFI_STA);

  esp_wifi_set_promiscuous(true); // (可选,但有帮助)
  esp_wifi_set_channel(1, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false); // (可选,但有帮助)
  Serial.println("已强制设置 Wi-Fi 在信道 1");

  if (esp_now_init() != ESP_OK)
  {
    Serial.println("错误: ESP-NOW 初始化失败。");
    return;
  }

  // 2. 注册发送回调
  esp_now_register_send_cb(OnDataSent);

  // 3. 【关键】注册所有从控为通信"伙伴"(Peer)
  esp_now_peer_info_t peerInfo;
  memset(&peerInfo, 0, sizeof(peerInfo)); // 清零结构体
  peerInfo.channel = 1;
  peerInfo.encrypt = false;
  peerInfo.ifidx = WIFI_IF_STA; // 指定接口为 STA 模式

  for (int i = 0; i < num_boxes; i++)
  {
    memcpy(peerInfo.peer_addr, mac_address_table[i], 6);
    if (esp_now_add_peer(&peerInfo) != ESP_OK)
    {
      Serial.printf("错误: 添加 Peer (盒子 %d) 失败。\n", i + 1);
    }
    else
    {
      Serial.printf("成功添加 Peer: 盒子 %d\n", i + 1);
    }
  }

  Serial.println("\n系统就绪,等待来自电脑的串口命令...");
  Serial.println("命令格式: box_id:1,led_id:66 (PC会自动添加RGB值)");
}

void loop()
{
  if (Serial.available() > 0)
  {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();

    Serial.printf("收到串口命令: %s\n", cmd.c_str());

    // 4. 解析新格式的串口命令
    // 格式: "box_id:1,led_id:66"
    int box_id = -1;
    int led_id = -1;

    // 查找 "box_id:" 和 "led_id:"
    int box_id_pos = cmd.indexOf("box_id:");
    int led_id_pos = cmd.indexOf("led_id:");

    if (box_id_pos != -1 && led_id_pos != -1)
    {
      // 提取 box_id 的值
      int box_id_start = box_id_pos + 7; // "box_id:" 长度为7
      int box_id_end = cmd.indexOf(',', box_id_start);
      if (box_id_end == -1)
        box_id_end = cmd.length();
      String box_id_str = cmd.substring(box_id_start, box_id_end);
      box_id = box_id_str.toInt();

      // 提取 led_id 的值
      int led_id_start = led_id_pos + 7; // "led_id:" 长度为7
      int led_id_end = cmd.indexOf(',', led_id_start);
      if (led_id_end == -1)
        led_id_end = cmd.length();
      String led_id_str = cmd.substring(led_id_start, led_id_end);
      led_id = led_id_str.toInt();
    }

    if (box_id > 0 && led_id >= 0)
    {
      // 5. 查找路由表
      // 注意：我们的 box_id 是 1-based (从1开始), 数组索引是 0-based
      int box_index = box_id - 1;

      if (box_index >= 0 && box_index < num_boxes)
      {
        // 准备消息 - 使用默认RGB值或从其他来源获取
        // 这里可以根据需要修改RGB值
        commandMessage.led_id = led_id;
        commandMessage.r = 255; // 默认红色,可根据需要修改
        commandMessage.g = 255; // 默认绿色,可根据需要修改
        commandMessage.b = 255; // 默认蓝色,可根据需要修改

        // 6. 发送 ESP-NOW 消息
        uint8_t *targetMac = mac_address_table[box_index];
        Serial.printf("  转发命令给 盒子 %d (MAC: %02X:%02X:%02X:%02X:%02X:%02X)\n",
                      box_id, targetMac[0], targetMac[1], targetMac[2],
                      targetMac[3], targetMac[4], targetMac[5]);
        Serial.printf("  LED ID: %d, RGB: (%d,%d,%d)\n",
                      led_id, commandMessage.r, commandMessage.g, commandMessage.b);

        esp_err_t result = esp_now_send(targetMac, (uint8_t *)&commandMessage, sizeof(commandMessage));

        if (result != ESP_OK)
        {
          Serial.println("  ESP-NOW 发送错误。");
        }
      }
      else
      {
        Serial.printf("  错误: 盒子 ID %d 无效 (范围: 1 到 %d)。\n", box_id, num_boxes);
      }
    }
    else
    {
      Serial.println("  错误: 命令格式不正确。应为: box_id:1,led_id:66");
    }
  }
}

#endif // FIRMWARE_IS_MASTER

#if defined(FIRMWARE_IS_SLAVE_8266)

#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <espnow.h>          // ESP8266 的 ESP-NOW 库
#include "common_protocol.h" // 引入我们的协议
#include <FastLED.h>

// ===================================================================
// =================== ESP8266 从设备 (SLAVE) 代码 ====================
// ===================================================================

// --- LED 配置 ---
#define DATA_PIN 2 // ESP8266 推荐使用 GPIO2 (D4)
#define LED_TYPE WS2812B
#define COLOR_ORDER GRB
#define NUM_LEDS 81   // 81个元件
#define BRIGHTNESS 96 // 注意供电!ESP8266 供电能力有限
// --- ---

CRGB leds[NUM_LEDS];
struct_message incomingMessage;

// 核心:ESP-NOW 接收回调
// 注意:ESP8266 的回调函数签名不同(没有 const)
void OnDataRecv(uint8_t *mac_addr, uint8_t *incomingData, uint8_t len)
{
  memcpy(&incomingMessage, incomingData, sizeof(incomingMessage));

  Serial.printf("收到命令: LED %d, Color (%d, %d, %d)\n",
                incomingMessage.led_id, incomingMessage.r,
                incomingMessage.g, incomingMessage.b);

  // 规则 -1: 熄灭所有
  if (incomingMessage.led_id == -1)
  {
    FastLED.clear();
  }
  // 规则 1-81: 点亮指定灯 (修正索引范围)
  else if (incomingMessage.led_id >= 1 && incomingMessage.led_id <= NUM_LEDS)
  {
    FastLED.clear(); // 先熄灭所有
    leds[incomingMessage.led_id - 1] = CRGB(incomingMessage.r,
                                            incomingMessage.g,
                                            incomingMessage.b);
  }
  else
  {
    // 无效的 led_id，不做任何操作
    Serial.printf("警告: 无效的 LED ID: %d (有效范围: 1-%d)\n",
                  incomingMessage.led_id, NUM_LEDS);
    return; // 提前返回，不更新LED
  }

  FastLED.show(); // 更新灯带
}

void setup()
{
  Serial.begin(115200);
  Serial.println("\n==================================");
  Serial.println("  ESP8266 从设备 (Slave) 启动...");
  Serial.println("==================================");

  // ⭐ 关键修复 1: 初始化结构体为全0，防止随机数据触发LED
  memset(&incomingMessage, 0, sizeof(incomingMessage));
  incomingMessage.led_id = -1; // 设置为无效值

  // 1. 初始化 FastLED
  FastLED.addLeds<LED_TYPE, DATA_PIN, COLOR_ORDER>(leds, NUM_LEDS);
  FastLED.setBrightness(BRIGHTNESS);

  // ⭐ 关键修复 2: 多次清空并强制刷新
  for (int i = 0; i < NUM_LEDS; i++)
  {
    leds[i] = CRGB::Black;
  }
  FastLED.clear();
  FastLED.show();
  delay(100); // 给LED一点稳定时间

  // 再次确保清空
  FastLED.clear();
  FastLED.show();

  Serial.println("FastLED (81灯) 初始化完毕。");

  // 2. 设置 WiFi 为 Station 模式
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(); // 断开任何现有连接

  // 3. 设置 WiFi 信道(ESP8266 方式)
  wifi_set_channel(1);
  Serial.println("已强制设置 Wi-Fi 在信道 1");

  // 4. 打印本设备的MAC地址
  Serial.print("\n[关键信息] 本设备 MAC 地址: ");
  Serial.println(WiFi.macAddress());
  Serial.println("请将此 MAC 地址添加到主控的路由表中!\n");

  // 5. 初始化 ESP-NOW
  if (esp_now_init() != 0)
  {
    Serial.println("错误: ESP-NOW 初始化失败。");
    return;
  }
  Serial.println("ESP-NOW 初始化成功。");

  // 6. 设置 ESP-NOW 角色为 SLAVE
  esp_now_set_self_role(ESP_NOW_ROLE_SLAVE);
  Serial.println("角色设置为: SLAVE");

  // 7. 注册接收回调函数
  esp_now_register_recv_cb(OnDataRecv);

  // ⭐ 关键修复 3: 最后再次清空LED，确保不会有任何残留
  FastLED.clear();
  FastLED.show();

  Serial.println("系统就绪,等待主控命令...\n");
}

void loop()
{
  // 无事可做,全靠回调
  delay(10000);
  // Serial.println("I'm still alive.");
}

#endif