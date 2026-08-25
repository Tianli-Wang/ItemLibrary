#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "driver/uart.h"
#include "driver/usb_serial_jtag.h"
#include "esp_check.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_now.h"
#include "esp_wifi.h"
#include "nvs_flash.h"

#include "espnow_protocol.h"

#define WEBUI_UART UART_NUM_0
#define UART_RX_BUFFER_SIZE 512
#define COMMAND_LINE_SIZE 96

static const char *TAG = "itemlib_master";
static const uint8_t BROADCAST_ADDRESS[ESP_NOW_ETH_ALEN] = {
    0xff, 0xff, 0xff, 0xff, 0xff, 0xff
};
static uint32_t s_sequence;

typedef struct {
    char data[COMMAND_LINE_SIZE];
    size_t length;
} command_line_buffer_t;

static void usb_monitor_printf(const char *format, ...)
{
    char output[192];
    va_list arguments;

    va_start(arguments, format);
    int length = vsnprintf(output, sizeof(output) - 3, format, arguments);
    va_end(arguments);

    if (length < 0) {
        return;
    }
    if (length > (int)sizeof(output) - 3) {
        length = sizeof(output) - 3;
    }
    output[length++] = '\r';
    output[length++] = '\n';

    /* 调试信息固定走原生 USB，避免与 WebUI 使用的 UART0 输入混在一起。 */
    usb_serial_jtag_write_bytes(output, length, pdMS_TO_TICKS(100));
}

static void wifi_espnow_init(void)
{
    wifi_init_config_t wifi_config = WIFI_INIT_CONFIG_DEFAULT();
    esp_now_peer_info_t peer = {
        .channel = CONFIG_ITEMLIB_ESPNOW_CHANNEL,
        .ifidx = WIFI_IF_STA,
        .encrypt = false,
    };

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(esp_wifi_init(&wifi_config));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    /* 点灯强调即时响应，关闭 modem sleep，避免 ESP-NOW 发送前的唤醒延迟。 */
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_ITEMLIB_ESPNOW_CHANNEL,
                                         WIFI_SECOND_CHAN_NONE));
    ESP_ERROR_CHECK(esp_now_init());

    memcpy(peer.peer_addr, BROADCAST_ADDRESS, ESP_NOW_ETH_ALEN);
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));
    ESP_LOGI(TAG, "ESP-NOW 广播已启动，信道=%d", CONFIG_ITEMLIB_ESPNOW_CHANNEL);
}

static bool parse_webui_command(const char *line, uint16_t *box_id, uint16_t *led_id)
{
    uint32_t box = 0;
    uint32_t led = 0;
    char extra = '\0';
    int matched;

    /* 兼容现有 Web Serial 文本，同时接受便于调试的 JSON 文本。 */
    matched = sscanf(line, "box_id:%" SCNu32 ",led_id:%" SCNu32 " %c",
                     &box, &led, &extra);
    if (matched != 2) {
        matched = sscanf(line, "{\"box_id\":%" SCNu32 ",\"led_id\":%" SCNu32 "} %c",
                         &box, &led, &extra);
    }
    if (matched != 2 || box == 0 || box > UINT16_MAX || led > UINT16_MAX) {
        return false;
    }

    *box_id = (uint16_t)box;
    *led_id = (uint16_t)led;
    return true;
}

static esp_err_t broadcast_light_command(uint16_t box_id, uint16_t led_id)
{
    itemlib_espnow_message_t message = {
        .magic = ITEMLIB_ESPNOW_MAGIC,
        .version = ITEMLIB_ESPNOW_VERSION,
        .command = ITEMLIB_COMMAND_LIGHT,
        .box_id = box_id,
        .led_id = led_id,
        .sequence = ++s_sequence,
    };

    message.checksum = itemlib_message_checksum(&message);
    return esp_now_send(BROADCAST_ADDRESS, (const uint8_t *)&message, sizeof(message));
}

static void consume_webui_bytes(command_line_buffer_t *line_buffer,
                                const uint8_t *input, size_t input_length)
{
    for (size_t i = 0; i < input_length; ++i) {
        char ch = (char)input[i];

        if (ch == '\n' || ch == '\r') {
            if (line_buffer->length == 0) {
                continue;
            }
            line_buffer->data[line_buffer->length] = '\0';
            usb_monitor_printf("[UART RX] %s", line_buffer->data);

            uint16_t box_id;
            uint16_t led_id;
            if (parse_webui_command(line_buffer->data, &box_id, &led_id)) {
                usb_monitor_printf("[PARSED] box_id=%u, led_id=%u", box_id, led_id);
                esp_err_t error = broadcast_light_command(box_id, led_id);
                if (error == ESP_OK) {
                    usb_monitor_printf("[ESP-NOW] broadcast queued, sequence=%lu",
                                       (unsigned long)s_sequence);
                } else {
                    usb_monitor_printf("[ESP-NOW] send failed: %s",
                                       esp_err_to_name(error));
                }
            } else {
                usb_monitor_printf("[PARSE ERROR] invalid WebUI command");
            }
            line_buffer->length = 0;
        } else if (line_buffer->length < sizeof(line_buffer->data) - 1) {
            line_buffer->data[line_buffer->length++] = ch;
        } else {
            /* 超长行直接丢弃，避免半条命令被解析成合法位置。 */
            line_buffer->length = 0;
            usb_monitor_printf("[UART RX] line too long, discarded");
        }
    }
}

static void webui_uart_task(void *argument)
{
    uint8_t input[64];
    command_line_buffer_t uart_line = {0};
    (void)argument;

    while (true) {
        int received = uart_read_bytes(WEBUI_UART, input, sizeof(input),
                                       pdMS_TO_TICKS(1000));
        if (received > 0) {
            consume_webui_bytes(&uart_line, input, (size_t)received);
        }
    }
}

void app_main(void)
{
    esp_err_t nvs_error = nvs_flash_init();
    if (nvs_error == ESP_ERR_NVS_NO_FREE_PAGES ||
        nvs_error == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        nvs_error = nvs_flash_init();
    }
    ESP_ERROR_CHECK(nvs_error);

    const uart_config_t uart_config = {
        .baud_rate = 115200,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    ESP_ERROR_CHECK(uart_param_config(WEBUI_UART, &uart_config));
    ESP_ERROR_CHECK(uart_driver_install(WEBUI_UART, UART_RX_BUFFER_SIZE, 0,
                                        0, NULL, 0));
    usb_serial_jtag_driver_config_t usb_config = {
        .tx_buffer_size = 256,
        .rx_buffer_size = 256,
    };
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&usb_config));

    wifi_espnow_init();
    usb_monitor_printf("[READY] UART0 RX -> USB monitor, channel=%d",
                       CONFIG_ITEMLIB_ESPNOW_CHANNEL);
    usb_monitor_printf("[READY] expected: box_id:<n>,led_id:<n>");
    xTaskCreate(webui_uart_task, "webui_uart", 4096, NULL, 5, NULL);
}
