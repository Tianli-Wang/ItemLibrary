#include <Arduino.h>
#include <FastLED.h>

// --- LED 配置 ---
#define LED_PIN 18        // WS2812 (NeoPixel) 的数据引脚
#define NUM_LEDS 2      // 你灯带上的LED总数
#define BRIGHTNESS 64    // 默认亮度 (0-255)
#define LED_TYPE WS2812B // LED类型，可能是 WS2812, WS2812B, SK6812 等
#define COLOR_ORDER GRB  // 颜色顺序，GRB 是最常见的，也可能是 RGB
// --- ---

// 定义LED数组
CRGB leds[NUM_LEDS];

// 用于存储串口传入的数据
String serialBuffer = "";

/**
 * @brief 解析并执行来自串口的命令
 * @param cmd 完整的命令字符串, e.g., "5,255,0,0" or "clear"
 */
void executeCommand(String cmd)
{
  cmd.trim(); // 去除首尾空格和换行符
  if (cmd.length() == 0)
    return;

  Serial.print("Executing command: ");
  Serial.println(cmd);

  // 1. "clear" 命令
  if (cmd.equals("clear"))
  {
    FastLED.clear(); // 清除所有LED数据
    FastLED.show();  // 更新显示（关闭所有灯）
    Serial.println("LEDs cleared.");
  }
  // 2. "fill,r,g,b" 命令
  else if (cmd.startsWith("fill,"))
  {
    int r, g, b;
    // sscanf 用于从字符串中解析格式化数据
    // %*[^,] 跳过第一个逗号之前的所有内容 ("fill")
    // %*c 跳过第一个逗号
    int parsed = sscanf(cmd.c_str(), "%*[^,],%d,%d,%d", &r, &g, &b);

    if (parsed == 3)
    { // 必须成功解析出3个数字
      fill_solid(leds, NUM_LEDS, CRGB(r, g, b));
      FastLED.show();
      Serial.printf("Filled all with %d,%d,%d\n", r, g, b);
    }
    else
    {
      Serial.println("Invalid fill command format. Use: fill,r,g,b");
    }
  }
  // 3. "index,r,g,b" 命令
  else
  {
    int index, r, g, b;
    int parsed = sscanf(cmd.c_str(), "%d,%d,%d,%d", &index, &r, &g, &b);

    if (parsed == 4)
    { // 必须成功解析出4个数字
      if (index >= 0 && index < NUM_LEDS)
      {
        leds[index] = CRGB(r, g, b);
        FastLED.show();
        Serial.printf("Set LED %d to %d,%d,%d\n", index, r, g, b);
      }
      else
      {
        Serial.printf("Error: LED index %d out of bounds (0-%d)\n", index, NUM_LEDS - 1);
      }
    }
    else
    {
      Serial.println("Invalid command format. Use: index,r,g,b OR clear OR fill,r,g,b");
    }
  }
}

void setup()
{
  Serial.begin(115200);
  Serial.println("WS2812 Serial Tester Initialized.");

  // FastLED 初始化
  // addLeds的模板参数: <LED型号, 数据引脚, 颜色顺序>
  FastLED.addLeds<LED_TYPE, LED_PIN, COLOR_ORDER>(leds, NUM_LEDS).setCorrection(TypicalLEDStrip);

  // 设置全局亮度
  FastLED.setBrightness(BRIGHTNESS);

  // 启动测试：点亮第一个灯为红色，表示启动成功
  FastLED.clear();
  leds[0] = CRGB::Red;
  FastLED.show();
  Serial.println("Enter commands (e.g., '5,255,0,0', 'fill,0,255,0', or 'clear'):");
}

void loop()
{
  // 检查串口是否有数据
  while (Serial.available() > 0)
  {
    char inChar = (char)Serial.read();

    // 如果收到换行符，说明一条命令结束
    if (inChar == '\n' || inChar == '\r')
    {
      if (serialBuffer.length() > 0)
      {
        executeCommand(serialBuffer);
        serialBuffer = ""; // 清空缓冲区，准备接收下一条命令
      }
    }
    else
    {
      // 将字符添加到缓冲区
      serialBuffer += inChar;
    }
  }
}