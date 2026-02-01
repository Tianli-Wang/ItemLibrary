import json
from flask import Flask, request, jsonify, Response
from bs4 import BeautifulSoup
import re

# --- 配置 ---
COMPONENTS_FILE = 'components.json'
BOM_FILE_NAME = 'InteractiveBOM_v7.html'
# --- ---

app = Flask(__name__)
components_db = {}

# --- 数据库加载 ---
try:
    with open(COMPONENTS_FILE, 'r', encoding='utf-8') as f:
        components_db = json.load(f)
    print(f"成功加载 {len(components_db)} 条元件数据。")
except Exception as e:
    print(f"警告: 加载 {COMPONENTS_FILE} 失败: {e}")


#
#
#
# --- vvv 这里是更新后的函数 vvv ---
#
#
def search_component(part_number, parameter=None, footprint=None):
    """
    智能搜索元件
    1. 优先精确匹配 part_number (数据库的key, e.g., 'C29DF')
    2. 如果没找到，进行模糊搜索，包括：
        a. 传入的 'parameter' vs 数据库的 'parameter'
        b. 传入的 'footprint' vs 数据库的 'footprint'
        c. [新] 传入的 'part_number' vs 数据库的 'parameter' (用于匹配 'SPX3819M5-3.3' 和 'SPX3819')
    """
    # 1. 首先尝试精确匹配 part_number (匹配 "C29DF" 这样的ID)
    if part_number in components_db:
        return part_number, components_db[part_number]

    # 2. 【修改点】
    #    因为我们现在要用 part_number 进行模糊搜索，所以只要提供了任意一个信息，都应该启动模糊搜索
    if part_number or parameter or footprint:
        print(f"  未找到精确匹配，开始模糊搜索...")
        print(f"  搜索条件 - 型号: {part_number}, 参数: {parameter}, 封装: {footprint}")
        
        # --- 
        # --- 1. 封装 (Footprint) 归一化 (R0402 -> 0402) ---
        # --- 
        normalized_input_footprint = None
        if footprint:
            fp_upper = footprint.upper()
            if (len(fp_upper) == 5 and 
                fp_upper[0] in ('R', 'C') and 
                fp_upper[1:].isdigit()):
                normalized_input_footprint = fp_upper[1:]
                print(f"  (封装归一化: {footprint} -> {normalized_input_footprint})")
            else:
                # 保留 SOT-23-5... 这样的长封装名称
                normalized_input_footprint = fp_upper
        
        # --- 
        # --- 2. 参数 (Parameter) 归一化 (kΩ -> K, Ω -> R) ---
        # --- 
        normalized_input_parameter_str = None
        if parameter:
            # 拷贝一份，准备开始替换
            normalized_param = parameter
            
            # --- 
            # 规则1: 替换 Kilo-Ohms (kΩ/KΩ/kΩ/KΩ) 为 K
            # ---
            normalized_param = normalized_param.replace('kΩ', 'K')
            normalized_param = normalized_param.replace('KΩ', 'K')
            normalized_param = normalized_param.replace('kΩ', 'K') 
            normalized_param = normalized_param.replace('KΩ', 'K') 
            
            # --- 
            # 规则2: 替换 Ohms (Ω/Ω) 为 R
            # ---
            normalized_param = normalized_param.replace('Ω', 'R')
            normalized_param = normalized_param.replace('Ω', 'R')
            
            # (可选) 规则3: 替换 Mega-Ohms (MΩ/MΩ) 为 M
            normalized_param = normalized_param.replace('MΩ', 'M')
            normalized_param = normalized_param.replace('mΩ', 'M') # 兼容小写 m
            normalized_param = normalized_param.replace('MΩ', 'M')
            normalized_param = normalized_param.replace('mΩ', 'M')

            normalized_input_parameter_str = normalized_param
            
            if normalized_input_parameter_str != parameter:
                print(f"  (参数归一化: {parameter} -> {normalized_input_parameter_str})")
        # --- 
        # --- 归一化结束 ---
        # --- 

        matches = []
        for pn, data in components_db.items():
            score = 0
            reasons = []

            # --- 
            # --- 3a. 参数匹配 (传入的 'parameter' vs 数据库的 'parameter') ---
            # --- 
            if normalized_input_parameter_str and 'parameter' in data and data['parameter']:
                db_parameter_upper = data['parameter'].upper() 
                
                input_parameter_upper = normalized_input_parameter_str.upper()
                
                if input_parameter_upper == db_parameter_upper:
                    score += 10
                    reasons.append(f"参数完全匹配({data['parameter']})")
                elif input_parameter_upper in db_parameter_upper or db_parameter_upper in input_parameter_upper:
                    score += 5
                    reasons.append(f"参数部分匹配({data['parameter']})")

            # --- 
            # --- 3b. 【新功能】型号-参数 交叉匹配 ---
            #     (比较 传入的 'part_number' 和 数据库的 'parameter')
            # --- 
            if part_number and 'parameter' in data and data['parameter']:
                db_param_short_upper = data['parameter'].upper()
                incoming_pn_long_upper = part_number.upper()
                
                if db_param_short_upper == incoming_pn_long_upper:
                    # 例如: 传入 'SPX3819', 数据库 'SPX3819'
                    score += 20 # 这是一个高分匹配
                    reasons.append(f"型号-参数完全匹配({data['parameter']})")
                elif db_param_short_upper in incoming_pn_long_upper:
                    # 例如: 传入 'SPX3819M5-3.3', 数据库 'SPX3819'
                    # "SPX3819" in "SPX3819M5-3.3" -> True
                    score += 20 # 这也是一个高分匹配 (您的情况)
                    reasons.append(f"型号-参数包含匹配({data['parameter']})")
                elif incoming_pn_long_upper in db_param_short_upper:
                    # 例如: 传入 'SPX3819', 数据库 'SPX3819-L' (不太可能)
                    score += 5 # 这是一个低分匹配
                    reasons.append(f"参数-型号包含匹配({data['parameter']})")


            # --- 
            # --- 4. 封装匹配 (使用归一化后的 'normalized_input_footprint') ---
            # --- 
            if normalized_input_footprint and 'footprint' in data and data['footprint']:
                db_footprint_upper = data['footprint'].upper() 
                
                if normalized_input_footprint == db_footprint_upper:
                    score += 10
                    reasons.append(f"封装完全匹配({data['footprint']})")
                elif (normalized_input_footprint in db_footprint_upper or 
                      db_footprint_upper in normalized_input_footprint): # 【修正点】修正了原代码中的一个拼写错误
                    score += 5
                    reasons.append(f"封装部分匹配({data['footprint']})")
            # --- 
            # --- 匹配结束 ---
            # --- 

            # 【修改点】
            # 您的案例 (SPX3819) 中, 封装库是 ""，封装参数是 "SOT-23-5..."
            # 匹配 3a (参数) 不会运行 (传入参数为空)
            # 匹配 3b (型号-参数) 会运行，得到 20 分
            # 匹配 4 (封装) 会运行，但 db_footprint_upper 是 "", 
            #   ( 'SOT...' in '' or '' in 'SOT...' ) -> 第二个为True，得到 5 分
            # 总分 25 分。
            
            # 如果我们将阈值保持在 19，25 分 > 19 分，匹配成功。
            # 如果是 0402 匹配 0402 (10分) + 10K 匹配 10K (10分)，总分 20 分。
            # 如果是 'RES-10K' vs '10K' (20分) + '0402' vs '0402' (10分) = 30分。
            # 阈值 19 看起来是合理的。
            
            if score > 19:
                matches.append({
                    'part_number': pn,
                    'data': data,
                    'score': score,
                    'reasons': reasons
                })

        # 按匹配分数排序
        matches.sort(key=lambda x: x['score'], reverse=True)

        if matches:
            best_match = matches[0]
            print(f"  找到 {len(matches)} 个匹配项，最佳匹配:")
            print(f"    型号: {best_match['part_number']}")
            print(f"    匹配度: {best_match['score']} 分")
            print(f"    原因: {', '.join(best_match['reasons'])}")

            if len(matches) > 1:
                print(f"  其他可能匹配:")
                for match in matches[1:4]:  # 最多显示3个
                    print(f"    - {match['part_number']} (分数:{match['score']}) - {', '.join(match['reasons'])}")

            return best_match['part_number'], best_match['data']

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


    # --- 注入包含 Web Serial API 的新脚本 ---
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
    </style>

    <div id="serial-control">
        <h4>Web 串口控制</h4>
        <button id="connectButton">连接串口</button>
        <p id="serial-status">状态：未连接</p>
    </div>

    
    <script>
        console.log('🚀 BOM智能搜索 & 串口脚本已加载！');

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
                    // 波特率 9600，您可以根据硬件修改
                    await port.open({ baudRate: 115200 }); 
                    
                    statusDisplay.textContent = '状态：串口已打开';
                    connectButton.textContent = '已连接';
                    connectButton.disabled = true;
                    
                    serial_port = port;
                    // 获取写入器，以便后续发送数据
                    serial_writer = port.writable.getWriter();

                    originalConsoleLog('串口已打开:', port);

                    // (可选) 监听串口断开
                    port.addEventListener('disconnect', () => {
                        originalConsoleLog('⚠️ 串口已断开');
                        statusDisplay.textContent = '状态：串口已断开';
                        connectButton.textContent = '连接串口';
                        connectButton.disabled = false;
                        if (serial_writer) {
                            serial_writer.releaseLock();
                        }
                        serial_writer = null;
                        serial_port = null;
                    });

                } catch (err) {
                    if (err.name === 'NotFoundError') {
                        statusDisplay.textContent = '状态：用户未选择串口。';
                    } else if (err.name === 'InvalidStateError') {
                        statusDisplay.textContent = '状态：串口已被占用。';
                    } else {
                        statusDisplay.textContent = `状态：发生错误: ${err.message}`;
                    }
                    originalConsoleLog('打开串口时出错:', err);
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
                statusDisplay.textContent = '状态：请先连接串口！';
                return;
            }
            
            // --- 
            // 假设的通信协议： "B<boxId>,L<ledId>\\n"
            // 例如: "B5,L10\\n" (B代表Box, L代表LED, \\n是换行符)
            // 
            // ！！！您需要根据您的 ESP32/Arduino 代码修改这个格式！！！
            // ---
            const dataString = `box_id:${boxId},led_id:${ledId}\\n`; 
            
            try {
                const dataUint8 = textEncoder.encode(dataString); // 编码为 Uint8Array
                await serial_writer.write(dataUint8);
                originalConsoleLog(`✅ 串口发送: ${dataString.trim()}`);
                statusDisplay.textContent = `状态：已发送 (B:${boxId}, L:${ledId})`;
            } catch (err) {
                originalConsoleLog(`⚠️ 串口发送错误: ${err.message}`);
                statusDisplay.textContent = `状态：发送错误: ${err.message}`;
                // 尝试处理写入器错误
                serial_writer.releaseLock();
                serial_writer = null;
                // 你可能需要在这里触发重新连接
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
                    originalConsoleLog('🔍 原始消息:', message);
                   
                    // 提取器件型号
                    let match = message.match(/器件型号[::\\s]*([^,]+)/i);
                    if (match) extracted.part_number = match[1].trim();

                    // 提取值/参数
                    match = message.match(/值[::\\s]*([^\s,，;；\\)]+)/i);
                    if (match) {
                        extracted.parameter = match[1].trim();
                    } else {
                        match = message.match(/器件编号:[^,]*,\s*([^,]+)/i);
                        if (match) extracted.parameter = match[1].trim();
                    }

                    // 提取封装 (保留了您原来的所有正则)
                    const footprintPatterns = [
                        /封装[::\\s]*([^,]+)/i,
                        /器件封装[::\\s]*([RCL]?\d{4})/i,
                        /器件编号:[^,]*,[^,]*,\s*([RCL]?\d{4})/i,
                        /footprint[::\\s]*([RCL]?\d{4})/i,
                        /package[::\\s]*([RCL]?\d{4})/i,
                        /,\s*([RCL]?\d{4})\s*[,，]/i,
                    ];
                   
                    for (let pattern of footprintPatterns) {
                        match = message.match(pattern);
                        if (match && match[1]) {
                            extracted.footprint = match[1].trim();
                            originalConsoleLog('📐 提取到封装:', extracted.footprint);
                            break;
                        }
                    }

                    // 如果至少提取到一个信息，就发送请求
                    if (extracted.part_number || extracted.parameter || extracted.footprint) {
                        originalConsoleLog('📦 捕获到元件信息:', extracted);
                       
                        const params = new URLSearchParams();
                        if (extracted.part_number) params.append('part_number', extracted.part_number);
                        if (extracted.parameter) params.append('parameter', extracted.parameter);
                        if (extracted.footprint) params.append('footprint', extracted.footprint);
                       
                        fetch(`/lightup?${params.toString()}`)
                            .then(response => response.json())
                            .then(data => {
                                if (data.status === 'success') {
                                    originalConsoleLog('✅ 找到元件:', data.matched_part_number,
                                                        '位置:', data.location);
                                    
                                    // 
                                    // *************************************
                                    //           --- 修改点 ---
                                    //  不再只是打印，而是调用串口发送函数
                                    // *************************************
                                    //
                                    sendSerialData(data.location.box_id, data.location.led_id);
                                    
                                } else {
                                    originalConsoleLog('❌ 未找到匹配:', data.message);
                                    statusDisplay.textContent = `状态：未在库中找到 (${extracted.part_number})`;
                                }
                            })
                            .catch(err => {
                                originalConsoleLog('⚠️ 请求错误:', err);
                                statusDisplay.textContent = `状态：后端请求失败`;
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