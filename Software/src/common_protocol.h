/*
 * @Author: Tianli-Wang 3190100325@zju.edu.cn
 * @Date: 2025-11-03 00:33:21
 * @LastEditors: Tianli-Wang 3190100325@zju.edu.cn
 * @LastEditTime: 2025-11-03 00:33:34
 * @FilePath: \Software\src\common_protocol.h
 * @Description: 这是默认设置,请设置`customMade`, 打开koroFileHeader查看配置 进行设置: https://github.com/OBKoro1/koro1FileHeader/wiki/%E9%85%8D%E7%BD%AE
 */
#ifndef COMMON_PROTOCOL_H
#define COMMON_PROTOCOL_H

#include <stdint.h>

// 定义ESP-NOW发送的数据结构
typedef struct struct_message
{

    // 要操作的LED索引 (0 到 80)
    // 特殊规则：
    // led_id = -1, 表示熄灭这个元件库的所有灯
    int led_id;

    // 目标颜色 (R, G, B)
    uint8_t r;
    uint8_t g;
    uint8_t b;

} struct_message;

#endif