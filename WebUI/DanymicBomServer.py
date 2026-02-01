import json
from flask import Flask, request, jsonify, Response
from bs4 import BeautifulSoup
import re

import os
# --- 配置 ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COMPONENTS_FILE = os.path.join(BASE_DIR, 'components.json')
BOM_FILE_NAME = os.path.join(BASE_DIR, 'InteractiveBOM_v7.html')
# --- ---

app = Flask(__name__)
components_db = {}

def load_database():
    """从JSON文件加载最新数据"""
    global components_db
    try:
        if os.path.exists(COMPONENTS_FILE):
            with open(COMPONENTS_FILE, 'r', encoding='utf-8') as f:
                components_db = json.load(f)
            return True
    except Exception as e:
        print(f"警告: 刷新数据库失败: {e}")
    return False

# 初始加载
load_database()


#
#
#
# --- vvv 这里是更新后的函数 vvv ---
#
#
def parse_electronic_value(val_str):
    """
    将电子元件数值归一化为浮点数 (e.g., '10k' -> 10000.0, '100n' -> 1e-07)
    """
    if not val_str: return None
    s = str(val_str).lower().strip()
    
    # 清理常用后缀和特殊字符
    s = s.replace('ω', '').replace('ohm', '').replace('farad', '').replace('f', '').replace('h', '').replace('v', '')
    
    # 处理 4k7 这种格式 -> 4.7k
    match_mid = re.match(r'^(\d+)([rkmunpf])(\d*)$', s)
    if match_mid:
        p1, p2, p3 = match_mid.groups()
        if p2 == 'r':
            s = f"{p1}.{p3 or '0'}"
        else:
            s = f"{p1}.{p3 or '0'}{p2}"

    # 单位倍率
    units = {
        'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'μ': 1e-6, 'm': 1e-3,
        'k': 1e3, 'M': 1e6, 'g': 1e9, 'r': 1
    }
    
    # 提取数值和单位
    match = re.match(r'^([\d\.]+)\s*([a-zμ]*)$', s)
    if match:
        val_val = float(match.group(1))
        unit_str = match.group(2)
        if unit_str and unit_str[0] in units:
            return val_val * units[unit_str[0]]
        return val_val
    return None

def search_component(part_number, parameter=None, footprint=None):
    """
    优化后的智能搜索逻辑
    """
    load_database()
    
    # 1. 绝对精确匹配 (Key 匹配)
    if part_number in components_db:
        return part_number, components_db[part_number]

    if not (part_number or parameter or footprint):
        return None, None

    print(f"  开始增强型匹配 - 型号:{part_number}, 参数:{parameter}, 封装:{footprint}")

    # 预处理搜索参数
    search_val = parse_electronic_value(parameter)
    search_fp = (footprint or "").upper()
    
    # 尝试从参数中提取电压 (例如 "10uF 25V" 提取出 25)
    search_volt = None
    if parameter:
        volt_match = re.search(r'(\d+)V', str(parameter), re.I)
        if volt_match:
            search_volt = volt_match.group(1)

    matches = []
    for pn, data in components_db.items():
        score = 0
        reasons = []

        db_param_str = data.get('parameter', '')
        db_fp_str = data.get('footprint', '')
        db_volt_str = str(data.get('voltage', '')).replace('V', '').replace('v', '')

        # A. 型号完全匹配检查 (BOM型号 vs 数据库Key/参数)
        if part_number:
            target_pn = part_number.upper()
            if target_pn == pn.upper():
                score += 100
                reasons.append("ID完美匹配")
            elif target_pn in db_param_str.upper() or db_param_str.upper() in target_pn:
                score += 40
                reasons.append(f"型号/参数文本包含({db_param_str})")

        # B. 数值逻辑匹配 (10k == 10000)
        db_val = parse_electronic_value(db_param_str)
        if search_val is not None and db_val is not None:
            diff = abs(search_val - db_val)
            rel_diff = diff / max(search_val, db_val) if max(search_val, db_val) > 0 else 0
            if rel_diff < 0.001:
                score += 50
                reasons.append("数值逻辑一致")
        elif parameter and db_param_str and parameter.upper() == db_param_str.upper():
            score += 30
            reasons.append("参数文本一致")

        # C. 封装匹配
        if search_fp and db_fp_str:
            db_fp_upper = db_fp_str.upper()
            if search_fp == db_fp_upper:
                score += 25
                reasons.append("封装完美匹配")
            elif search_fp in db_fp_upper or db_fp_upper in search_fp:
                score += 10
                reasons.append("封装模糊匹配")

        # D. 耐压值匹配
        if search_volt and db_volt_str:
            if search_volt == db_volt_str:
                score += 20
                reasons.append("耐压值匹配")

        if score >= 40:
            matches.append({
                'part_number': pn,
                'data': data,
                'score': score,
                'reasons': reasons
            })

    matches.sort(key=lambda x: x['score'], reverse=True)

    if matches:
        best = matches[0]
        print(f"  ✅ 最佳匹配: {best['part_number']} (得分:{best['score']}) 原因: {', '.join(best['reasons'])}")
        return best['part_number'], best['data']

    return None, None
#
#
#
# --- ^^^ 这里是更新后的函数 ^^^ ---
#
#
#


# 1. 【核心】点灯 API (支持多参数搜索)
@app.route('/lightup')
def light_up():
    part_number = request.args.get('part_number', '')
    parameter = request.args.get('parameter', '')
    footprint = request.args.get('footprint', '')

    if not part_number and not parameter and not footprint:
        return jsonify({"status": "error", "message": "需要提供至少一个搜索条件"}), 400

    print(f"=========================================")
    print(f"  BOM 点击事件")
    print(f"  器件型号: {part_number}")
    if parameter:
        print(f"  参数: {parameter}")
    if footprint:
        print(f"  封装: {footprint}")
    print(f"=========================================")

    # 使用智能搜索 (现在这个函数更智能了)
    matched_pn, item_data = search_component(part_number, parameter, footprint)

    if not item_data:
        print(f"  ❌未找到匹配的元件")
        return jsonify({
            "status": "not_found",
            "message": "未找到匹配的元件",
            "searched": {
                "part_number": part_number,
                "parameter": parameter,
                "footprint": footprint
            }
        })
    else:
        box_id = item_data.get('box_id')
        led_id = item_data.get('led_id')
        print(f"  ✅ 找到元件: {matched_pn}")
        print(f"  ✓ 位置 -> 盒子: {box_id}, LED: {led_id}")

        return jsonify({
            "status": "success",
            "message": "找到元件",
            "matched_part_number": matched_pn,
            "location": {
                "box_id": box_id,
                "led_id": led_id
            },
            "details": item_data
        })


# 2. 【核心】主页路由 (注入增强版脚本)
@app.route('/')
def serve_bom():
    try:
        with open(BOM_FILE_NAME, 'r', encoding='utf-8') as f:
            html_content = f.read()
    except FileNotFoundError:
        return f"错误: 找不到 {BOM_FILE_NAME}。请确保它和 app.py 在同一文件夹中。", 404

    # --- 自动修改BOM的 console.log ---
    find_string_block = r"Se=H.dataId[1],X=H.dataEle[1],ze=H.value;console.log(`\u5668\u4EF6\u7F16\u53F7:${Se}, \u5668\u4EF6\u578B\u53F7:${X}, \u503C:${ze}`)"
    replace_string_block = r"Se=H.dataId[1],X=H.dataEle[1],ze=H.value,Oe=H.package[1];console.log(`\u5668\u4EF6\u7F16\u53F7:${Se}, \u5668\u4EF6\u578B\u53F7:${X}, \u503C:${ze}, \u5C01\u88C5:${Oe}`)"

    if find_string_block in html_content:
        html_content = html_content.replace(find_string_block, replace_string_block)
        if not hasattr(serve_bom, 'patch_applied'):
            print("=========================================")
            print("  ✓ 自动BOM脚本修改成功！")
            print("  ✓ 已添加 '封装' (Oe) 并更新 console.log。")
            print("=========================================")
            serve_bom.patch_applied = True
    else:
        if not hasattr(serve_bom, 'patch_failed'):
            print("=========================================")
            print("  ⚠️ 警告: 未能自动修改BOM脚本。")
            print("    (未找到的完整代码块):")
            print(f"    {find_string_block}")
            print("=========================================")
            serve_bom.patch_failed = True
    # --- 自动修改结束 ---


    # --- 注入包含 Web Serial API 和 悬浮通知 的新脚本 ---
    injected_script = """
    <style>
        #serial-control {
            position: fixed;
            bottom: 10px;
            right: 10px;
            background: #fff;
            padding: 1rem;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.2);
            z-index: 9999;
            font-family: Arial, sans-serif;
            text-align: center;
        }
        #serial-control button {
            font-size: 1rem;
            padding: 0.5rem 1rem;
            color: #fff;
            background-color: #007bff;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            transition: background-color 0.3s;
        }
        #serial-control button:hover {
            background-color: #0056b3;
        }
        #serial-control button:disabled {
            background-color: #ccc;
            cursor: not-allowed;
        }
        #serial-status {
            margin-top: 0.5rem;
            font-weight: bold;
            font-size: 0.9rem;
        }
        /* 悬浮通知样式 */
        #match-notification {
            position: fixed;
            top: 20px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(0, 122, 255, 0.9);
            color: white;
            padding: 15px 30px;
            border-radius: 50px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.3);
            z-index: 10000;
            display: none;
            font-family: Arial, sans-serif;
            font-size: 1.2rem;
            font-weight: bold;
            text-align: center;
            animation: slideDown 0.3s ease-out;
        }
        @keyframes slideDown {
            from { top: -100px; opacity: 0; }
            to { top: 20px; opacity: 1; }
        }
    </style>

    <div id="serial-control">
        <h4>Web 串口控制</h4>
        <button id="connectButton">连接串口</button>
        <p id="serial-status">状态：未连接</p>
    </div>

    <div id="match-notification"></div>

    
    <script>
        console.log('🚀 BOM智能搜索 & 串口脚本已加载！');

        const notification = document.getElementById('match-notification');
        let notificationTimeout;

        function showNotification(message, duration = 3000) {
            notification.textContent = message;
            notification.style.display = 'block';
            clearTimeout(notificationTimeout);
            notificationTimeout = setTimeout(() => {
                notification.style.display = 'none';
            }, duration);
        }

        // --- 串口全局变量 ---
        let serial_port = null;
        let serial_writer = null;
        const textEncoder = new TextEncoder(); // 用于将字符串编码为 Uint8Array

        // --- 1. 获取注入的UI元素 ---
        const connectButton = document.getElementById('connectButton');
        const statusDisplay = document.getElementById('serial-status');

        // --- 2. 串口连接逻辑 (来自您的示例) ---
        connectButton.addEventListener('click', async () => {
            if ('serial' in navigator) {
                try {
                    const port = await navigator.serial.requestPort();
                    // 波特率 115200，您可以根据硬件修改
                    await port.open({ baudRate: 115200 }); 
                    
                    statusDisplay.textContent = '状态：串口已打开';
                    connectButton.textContent = '已连接';
                    connectButton.disabled = true;
                    
                    serial_port = port;
                    // 获取写入器，以便后续发送数据
                    serial_writer = port.writable.getWriter();

                    originalConsoleLog('串口已打开:', port);

                } catch (err) {
                    if (err.name === 'NotFoundError') {
                        statusDisplay.textContent = '状态：用户未选择串口。';
                    } else if (err.name === 'InvalidStateError') {
                        statusDisplay.textContent = '状态：串口已被占用。';
                    } else {
                        statusDisplay.textContent = `状态：发生错误: ${err.message}`;
                    }
                }
            } else {
                statusDisplay.textContent = '状态：浏览器不支持 Web Serial。';
                alert('您的浏览器不支持 Web Serial API。请尝试使用最新版的 Chrome、Edge 或 Opera 浏览器。');
            }
        });

        // --- 3. 新增：串口发送函数 ---
        async function sendSerialData(boxId, ledId) {
            if (!serial_writer) {
                originalConsoleLog('⚠️ 串口未连接，无法发送点灯命令。');
                return;
            }
            const dataString = `box_id:${boxId},led_id:${ledId}\\n`; 
            try {
                const dataUint8 = textEncoder.encode(dataString); 
                await serial_writer.write(dataUint8);
                originalConsoleLog(`✅ 串口发送: ${dataString.trim()}`);
            } catch (err) {
                originalConsoleLog(`⚠️ 串口发送错误: ${err.message}`);
                serial_writer.releaseLock();
                serial_writer = null;
            }
        }


        // --- 4. 原始的 console.log 拦截器 (已修改) ---
        const originalConsoleLog = console.log;

        console.log = function(message) {
            originalConsoleLog.apply(console, arguments);

            if (typeof message === 'string') {
                const extracted = {
                    part_number: '',
                    parameter: '',
                    footprint: ''
                };

                // 检查是否包含器件信息
                if (message.includes('器件型号') || message.includes('器件编号')) {
                    // 提取逻辑...
                    let match = message.match(/器件型号[::\\s]*([^,]+)/i);
                    if (match) extracted.part_number = match[1].trim();

                    match = message.match(/值[::\\s]*([^\s,，;；\\)]+)/i);
                    if (match) {
                        extracted.parameter = match[1].trim();
                    } else {
                        match = message.match(/器件编号:[^,]*,\s*([^,]+)/i);
                        if (match) extracted.parameter = match[1].trim();
                    }

                    const footprintPatterns = [
                        /封装[::\\s]*([^,]+)/i,
                        /器件封装[::\\s]*([RCL]?\d{4})/i,
                        /footprint[::\\s]*([RCL]?\d{4})/i,
                        /package[::\\s]*([RCL]?\d{4})/i,
                    ];
                   
                    for (let pattern of footprintPatterns) {
                        match = message.match(pattern);
                        if (match && match[1]) {
                            extracted.footprint = match[1].trim();
                            break;
                        }
                    }

                    if (extracted.part_number || extracted.parameter || extracted.footprint) {
                        const params = new URLSearchParams();
                        if (extracted.part_number) params.append('part_number', extracted.part_number);
                        if (extracted.parameter) params.append('parameter', extracted.parameter);
                        if (extracted.footprint) params.append('footprint', extracted.footprint);
                       
                        fetch(`/lightup?${params.toString()}`)
                            .then(response => response.json())
                            .then(data => {
                                if (data.status === 'success') {
                                    // 显示全屏悬浮通知
                                    showNotification(`📍 找到元件：Box ${data.location.box_id} | LED ${data.location.led_id}`);
                                    
                                    sendSerialData(data.location.box_id, data.location.led_id);
                                } else {
                                    showNotification(`❌ 未找到匹配: ${extracted.part_number || extracted.parameter}`, 2000);
                                }
                            })
                            .catch(err => {
                                originalConsoleLog('⚠️ 请求错误:', err);
                            });
                    }
                }
            }
        };
    </script>
    """

    soup = BeautifulSoup(html_content, 'html.parser')
    if soup.body:
        soup.body.append(BeautifulSoup(injected_script, 'html.parser'))
    else:
        html_content += injected_script
        return Response(html_content, mimetype='text/html')

    return Response(str(soup), mimetype='text/html')


if __name__ == '__main__':
    print(f"🚀 启动BOM智能搜索服务器...")
    print(f"📄 BOM文件: {BOM_FILE_NAME}")
    print(f"📊 数据库: {COMPONENTS_FILE}")
    print(f"🌐 访问地址: http://127.0.0.1:5000")
    print(f"\n搜索策略:")
    print(f"  1. 优先精确匹配器件型号")
    print(f"  2. 如未找到，使用参数和封装进行模糊搜索")
    print(f"  3. [新] 模糊搜索现在包含 'BOM型号' vs '数据库参数' 的匹配")
    print(f"  4. 显示匹配度最高的结果\n")
    print(f"⚡ 新功能: 已集成 Web Serial API (网页串口)！")
    print(f"  请在打开的网页中点击 '连接串口' 按钮。")
    app.run(debug=True, port=5000, host='127.0.0.1')