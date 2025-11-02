#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include "common_protocol.h" // 引入我们的协议

// ===================================================================
// =================== 从设备 (SLAVE) 代码 ============================
// ===================================================================
#if defined(FIRMWARE_IS_SLAVE)

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
  // delay(10000);
  printf(".");
  delay(1000);
}

#endif // FIRMWARE_IS_SLAVE

// ===================================================================
// =================== 主设备 (MASTER) 代码 ===========================
// ===================================================================
#if defined(FIRMWARE_IS_MASTER)

// -------------------------------------------------------------
// ！！！【你需要修改这里】！！！
// 这是你的“路由表”。把从控的MAC地址抄到这里。
// -------------------------------------------------------------

// 格式: { 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF }
// 示例：我有2个元件库
uint8_t mac_box_1[] = {0x34, 0x94, 0x54, 0x19, 0x31, 0x80}; // 盒子1: 电阻库
uint8_t mac_box_2[] = {0xAA, 0xBB, 0xCC, 0x11, 0x22, 0x02}; // 盒子2: 电容库
// uint8_t mac_box_3[] = { ... }; // 你可以继续添加

// 路由表数组
uint8_t *mac_address_table[] = {
    mac_box_1, // 索引 0 (对应 box_id 1)
    mac_box_2  // 索引 1 (对应 box_id 2)
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
  if (esp_now_init() != ESP_OK)
  {
    Serial.println("错误: ESP-NOW 初始化失败。");
    return;
  }

  // 2. 注册发送回调
  esp_now_register_send_cb(OnDataSent);

  // 3. 【关键】注册所有从控为通信"伙伴"(Peer)
  esp_now_peer_info_t peerInfo;
  peerInfo.channel = 0;
  peerInfo.encrypt = false;

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

  Serial.println("\n系统就绪，等待来自电脑的串口命令...");
  Serial.println("命令格式: box_id,led_id,r,g,b (例如: 1,5,255,0,0)");
}

void loop()
{
  if (Serial.available() > 0)
  {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();

    Serial.printf("收到串口命令: %s\n", cmd.c_str());

    int box_id, led_id, r, g, b;

    // 4. 解析串口命令
    // 格式: "box_id,led_id,r,g,b"
    int parsed = sscanf(cmd.c_str(), "%d,%d,%d,%d,%d", &box_id, &led_id, &r, &g, &b);

    if (parsed == 5)
    {
      // 5. 查找路由表
      // 注意：我们的 box_id 是 1-based (从1开始), 数组索引是 0-based
      int box_index = box_id - 1;

      if (box_index >= 0 && box_index < num_boxes)
      {
        // 准备消息
        commandMessage.led_id = led_id;
        commandMessage.r = (uint8_t)r;
        commandMessage.g = (uint8_t)g;
        commandMessage.b = (uint8_t)b;

        // 6. 发送 ESP-NOW 消息
        uint8_t *targetMac = mac_address_table[box_index];
        Serial.printf("  转发命令给 盒子 %d (MAC: %02X:%02X:%02X:%02X:%02X:%02X)\n",
                      box_id, targetMac[0], targetMac[1], targetMac[2], targetMac[3], targetMac[4], targetMac[5]);

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
      Serial.println("  错误: 命令格式不正确。应为: box_id,led_id,r,g,b");
    }
  }
}

#endif // FIRMWARE_IS_MASTER