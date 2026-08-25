#pragma once

#include <stddef.h>
#include <stdint.h>

#define ITEMLIB_ESPNOW_MAGIC 0x494C4544U /* "ILED" */
#define ITEMLIB_ESPNOW_VERSION 1U

typedef enum {
    ITEMLIB_COMMAND_LIGHT = 1,
} itemlib_command_type_t;

typedef struct {
    uint32_t magic;
    uint8_t version;
    uint8_t command;
    uint16_t box_id;
    uint16_t led_id;
    uint16_t reserved;
    uint32_t sequence;
    uint32_t checksum;
} itemlib_espnow_message_t;

#ifdef __cplusplus
static_assert(sizeof(itemlib_espnow_message_t) == 20,
              "ESP-NOW message layout must stay identical on both chips");
#else
_Static_assert(sizeof(itemlib_espnow_message_t) == 20,
               "ESP-NOW message layout must stay identical on both chips");
#endif

/* ESP-NOW 自带链路层校验；这里再校验应用数据，避免错误长度或旧协议数据被误执行。 */
static inline uint32_t itemlib_message_checksum(const itemlib_espnow_message_t *message)
{
    const uint8_t *bytes = (const uint8_t *)message;
    uint32_t hash = 2166136261U;

    for (size_t i = 0; i < offsetof(itemlib_espnow_message_t, checksum); ++i) {
        hash ^= bytes[i];
        hash *= 16777619U;
    }
    return hash;
}

static inline int itemlib_message_is_valid(const itemlib_espnow_message_t *message)
{
    return message->magic == ITEMLIB_ESPNOW_MAGIC &&
           message->version == ITEMLIB_ESPNOW_VERSION &&
           message->command == ITEMLIB_COMMAND_LIGHT &&
           message->checksum == itemlib_message_checksum(message);
}
