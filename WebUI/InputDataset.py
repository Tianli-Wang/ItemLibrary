'''
Author: Tianli-Wang 3190100325@zju.edu.cn
Date: 2025-11-08 01:05:00
LastEditors: Tianli-Wang 3190100325@zju.edu.cn
LastEditTime: 2025-11-10 00:35:58
FilePath: \WebUI\Input.py
Description: 这是默认设置,请设置`customMade`, 打开koroFileHeader查看配置 进行设置: https://github.com/OBKoro1/koro1FileHeader/wiki/%E9%85%8D%E7%BD%AE
'''
import os
import json
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)
JSON_FILE = 'components.json'

def load_data():
    """从JSON文件加载数据"""
    if not os.path.exists(JSON_FILE):
        return {}
    try:
        with open(JSON_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {} # 如果文件是空的或损坏的，返回空字典

def save_data(data):
    """将数据保存回JSON文件"""
    with open(JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# --- API 路由 ---

@app.route('/api/components', methods=['GET'])
def get_components():
    """获取所有元件的列表"""
    data = load_data()
    return jsonify(data)

@app.route('/api/add', methods=['POST'])
def add_component():
    """添加一个新元件"""
    new_comp_data = request.json
    
    # "component_name" 是我们从前端JS发送的顶层key
    component_name = new_comp_data.get('component_name')
    # "details" 是那个包含 box_id, led_id 等的嵌套对象
    details = new_comp_data.get('details')

    if not component_name or not details:
        return jsonify({"success": False, "error": "数据不完整"}), 400

    data = load_data()
    
    if component_name in data:
        return jsonify({"success": False, "error": "该元件名称已存在"}), 400
    
    data[component_name] = details
    save_data(data)
    
    return jsonify({"success": True, "component_name": component_name})

@app.route('/api/delete', methods=['POST'])
def delete_component():
    """删除一个元件"""
    data_to_delete = request.json
    component_name = data_to_delete.get('component_name')

    if not component_name:
        return jsonify({"success": False, "error": "未提供元件名称"}), 400

    data = load_data()
    
    if component_name in data:
        del data[component_name]
        save_data(data)
        return jsonify({"success": True, "component_name": component_name})
    else:
        return jsonify({"success": False, "error": "元件未找到"}), 404

# --- UI 路由 ---

@app.route('/')
def serve_index():
    """提供 index.html UI 界面"""
    return send_from_directory('.', 'InputWebUI.html')

# --- 启动服务器 ---
if __name__ == '__main__':
    print("=========================================")
    print(" 🚀 元件管理器已启动！")
    print(" 🌍 请在浏览器中打开: http://127.0.0.1:5000")
    print("=========================================")
    # 使用 host='0.0.0.0' 可以让局域网内的其他设备也访问
    app.run(debug=True, host='0.0.0.0', port=5000)