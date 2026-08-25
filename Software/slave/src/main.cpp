#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <FastLED.h>
#include <espnow.h>
extern "C" {
#include <user_interface.h>
}

#include "espnow_protocol.h"

#define LED_TYPE WS2812B
#define LED_COLOR_ORDER GRB
#define LIGHT_ON_DURATION_MS 5000UL

static CRGB s_position_leds[ITEMLIB_LED_COUNT];
static CRGB s_box_leds[ITEMLIB_BOX_LED_COUNT];
static itemlib_espnow_message_t s_pending_message;
static volatile bool s_command_pending;

enum receive_result_t : uint8_t {
    RECEIVE_NONE = 0,
    RECEIVE_BAD_LENGTH,
    RECEIVE_BAD_MESSAGE,
    RECEIVE_WRONG_BOX,
    RECEIVE_BAD_LED,
};

static itemlib_espnow_message_t s_rejected_message;
static volatile receive_result_t s_receive_result = RECEIVE_NONE;
static volatile uint8_t s_received_length;
static bool s_light_active;
static uint32_t s_light_off_time;

static void show_position(uint16_t led_id)
{
    FastLED.clear();

    if (led_id != 0) {
        s_position_leds[led_id - 1] = CRGB::White;

        /* 第二路盒体灯用于提示当前料盒已被选中，保留实际硬件的双灯带设计。 */
        fill_solid(s_box_leds, ITEMLIB_BOX_LED_COUNT, CRGB::White);
        s_light_active = true;
        s_light_off_time = millis() + LIGHT_ON_DURATION_MS;
    } else {
        s_light_active = false;
    }
    FastLED.show();
}

static void on_data_received(uint8_t *mac_address, uint8_t *data, uint8_t length)
{
    (void)mac_address;
    itemlib_espnow_message_t message;

    if (length != sizeof(message)) {
        s_received_length = length;
        s_receive_result = RECEIVE_BAD_LENGTH;
        return;
    }
    memcpy(&message, data, sizeof(message));
    if (!itemlib_message_is_valid(&message)) {
        memcpy(&s_rejected_message, &message, sizeof(message));
        s_receive_result = RECEIVE_BAD_MESSAGE;
        return;
    }
    if (message.box_id != ITEMLIB_BOX_ID) {
        memcpy(&s_rejected_message, &message, sizeof(message));
        s_receive_result = RECEIVE_WRONG_BOX;
        return;
    }
    if (message.led_id == 0 || message.led_id > ITEMLIB_LED_COUNT) {
        memcpy(&s_rejected_message, &message, sizeof(message));
        s_receive_result = RECEIVE_BAD_LED;
        return;
    }

    /* Wi-Fi 回调只保存最新命令，FastLED.show() 留给 loop 执行，避免阻塞无线协议栈。 */
    memcpy(&s_pending_message, &message, sizeof(message));
    s_command_pending = true;
}

void setup()
{
    Serial.begin(115200);
    Serial.println();
    Serial.println("ItemLibrary ESP8266 slave 启动");

    FastLED.addLeds<LED_TYPE, ITEMLIB_LED_DATA_PIN, LED_COLOR_ORDER>(
        s_position_leds, ITEMLIB_LED_COUNT);
    FastLED.addLeds<LED_TYPE, ITEMLIB_BOX_LED_PIN, LED_COLOR_ORDER>(
        s_box_leds, ITEMLIB_BOX_LED_COUNT);
    FastLED.setBrightness(ITEMLIB_LED_BRIGHTNESS);
    show_position(0);

    /* 上电自检只点亮两路首灯，便于区分灯带接线故障与 ESP-NOW 接收故障。 */
    s_position_leds[0] = CRGB::Blue;
    s_box_leds[0] = CRGB::Blue;
    FastLED.show();
    delay(500);
    show_position(0);

    WiFi.persistent(false);
    WiFi.mode(WIFI_STA);
    /* 接收端保持 Wi-Fi 唤醒，消除 modem sleep 带来的数百毫秒接收延迟。 */
    WiFi.setSleepMode(WIFI_NONE_SLEEP);
    WiFi.disconnect();
    wifi_set_channel(ITEMLIB_ESPNOW_CHANNEL);

    Serial.printf("MAC: %s, box_id=%u, channel=%u\n",
                  WiFi.macAddress().c_str(), ITEMLIB_BOX_ID,
                  ITEMLIB_ESPNOW_CHANNEL);

    if (esp_now_init() != 0) {
        Serial.println("ESP-NOW 初始化失败");
        return;
    }
    esp_now_set_self_role(ESP_NOW_ROLE_SLAVE);
    esp_now_register_recv_cb(on_data_received);
    Serial.println("ESP-NOW 接收已就绪");
}

void loop()
{
    static uint32_t last_status_time;

    /* 使用非阻塞定时器，亮灯期间继续正常接收新的 ESP-NOW 命令。 */
    if (s_light_active &&
        static_cast<int32_t>(millis() - s_light_off_time) >= 0) {
        show_position(0);
        Serial.println("点灯超时，已自动熄灭");
    }

    if (millis() - last_status_time >= 2000) {
        last_status_time = millis();
        Serial.printf("运行中: box_id=%u, channel=%u, heap=%u\n",
                      ITEMLIB_BOX_ID, wifi_get_channel(), ESP.getFreeHeap());
    }

    if (s_receive_result != RECEIVE_NONE) {
        receive_result_t result;
        uint8_t received_length;
        itemlib_espnow_message_t rejected_message;

        /* 回调只记录结果，串口打印放到 loop，避免阻塞 ESP-NOW 的 Wi-Fi 回调。 */
        noInterrupts();
        result = s_receive_result;
        received_length = s_received_length;
        memcpy(&rejected_message, &s_rejected_message, sizeof(rejected_message));
        s_receive_result = RECEIVE_NONE;
        interrupts();

        if (result == RECEIVE_BAD_LENGTH) {
            Serial.printf("ESP-NOW 丢弃: 长度=%u, 期望=%u\n",
                          received_length, sizeof(itemlib_espnow_message_t));
        } else if (result == RECEIVE_BAD_MESSAGE) {
            Serial.printf("ESP-NOW 丢弃: 协议或校验错误, magic=0x%08lX\n",
                          static_cast<unsigned long>(rejected_message.magic));
        } else if (result == RECEIVE_WRONG_BOX) {
            Serial.printf("ESP-NOW 已收到但料盒不匹配: 收到=%u, 本机=%u\n",
                          rejected_message.box_id, ITEMLIB_BOX_ID);
        } else if (result == RECEIVE_BAD_LED) {
            Serial.printf("ESP-NOW 已收到但灯号越界: led_id=%u, 范围=1..%u\n",
                          rejected_message.led_id, ITEMLIB_LED_COUNT);
        }
    }

    if (s_command_pending) {
        itemlib_espnow_message_t message;

        /* 复制期间短暂关中断，防止新广播覆盖到一半形成混合命令。 */
        noInterrupts();
        memcpy(&message, &s_pending_message, sizeof(message));
        s_command_pending = false;
        interrupts();

        show_position(message.led_id);
        Serial.printf("已执行: box_id=%u, led_id=%u, seq=%lu\n",
                      message.box_id, message.led_id,
                      static_cast<unsigned long>(message.sequence));
    }
    delay(1);
}
