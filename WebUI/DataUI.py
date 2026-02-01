import os
import json
import re
from flask import Flask, request, jsonify, send_from_directory, Response
from bs4 import BeautifulSoup
from LCSCCopy import get_lcsc_product_data
import DanymicBomServer

'''
Combined Server: DataUI.py
Merges functionality from:
1. InputDataset.py (Data Management)
2. DanymicBomServer.py (BOM View & Smart Search)

Author: Tianli-Wang (Merged by Assistant)
'''

app = Flask(__name__)

# --- 配置 ---
# 获取当前脚本所在的绝对目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

JSON_FILE = os.path.join(BASE_DIR, 'components.json')
BOM_FILE_NAME = os.path.join(BASE_DIR, 'InteractiveBOM_v7.html')
MANAGEMENT_UI_FILE = os.path.join(BASE_DIR, 'InputWebUI.html')

# ==========================================
#        数据管理部分 (来自 InputDataset.py)
# ==========================================

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

# --- API 路由 (数据增删查改) ---

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

@app.route('/api/update', methods=['POST'])
def update_component():
    """更新一个元件的数据"""
    update_data = request.json
    component_name = update_data.get('component_name')
    details = update_data.get('details')

    if not component_name or not details:
        return jsonify({"success": False, "error": "数据不完整"}), 400

    data = load_data()
    
    if component_name not in data:
        return jsonify({"success": False, "error": "元件不存在"}), 404
    
    # 更新详情，保留原有的详情并覆盖新提供的字段
    data[component_name].update(details)
    save_data(data)
    
    return jsonify({"success": True, "component_name": component_name})

@app.route('/api/swap_components', methods=['POST'])
def swap_components():
    """交换两个元件的位置 (box_id 和 led_id)"""
    req_data = request.json
    name1 = req_data.get('name1')
    name2 = req_data.get('name2')
    
    if not name1 or not name2:
        return jsonify({"success": False, "error": "请提供两个元件名称"}), 400
        
    data = load_data()
    if name1 not in data or name2 not in data:
        return jsonify({"success": False, "error": "元件未找到"}), 404
        
    # 交换位置
    b1, l1 = data[name1].get('box_id'), data[name1].get('led_id')
    b2, l2 = data[name2].get('box_id'), data[name2].get('led_id')
    
    data[name1]['box_id'], data[name1]['led_id'] = b2, l2
    data[name2]['box_id'], data[name2]['led_id'] = b1, l1
    
    save_data(data)
    return jsonify({"success": True})

@app.route('/api/crawl_lcsc', methods=['GET'])
def crawl_lcsc():
    """从立创商城爬取元件信息"""
    keyword = request.args.get('keyword')
    if not keyword:
        return jsonify({"success": False, "error": "未提供搜索关键字"}), 400
    
    result = get_lcsc_product_data(keyword)
    return jsonify(result)

# --- 管理界面路由 ---

@app.route('/')
@app.route('/manage')
def serve_main_app():
    """提供主应用界面 (原 InputWebUI.html, 现包含侧边栏)"""
    return send_from_directory(BASE_DIR, os.path.basename(MANAGEMENT_UI_FILE))

@app.route('/api/upload_bom', methods=['POST'])
def upload_bom():
    """上传并覆盖原有的BOM HTML文件"""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "没有文件部件"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "error": "没有选择文件"}), 400
    
    if file and file.filename.endswith('.html'):
        # 强制保存为配置的文件名
        save_path = BOM_FILE_NAME
        file.save(save_path)
        print(f"BOM文件已更新: {save_path}")
        return jsonify({"success": True, "message": f"成功更新BOM文件: {file.filename}"})
    
    return jsonify({"success": False, "error": "仅支持 .html 文件"}), 400


# ==========================================
#        BOM 服务部分 (来自 DanymicBomServer.py)
# ==========================================

# --- BOM 服务部分 ---
# 直接调用 DanymicBomServer 中的逻辑，保持同步

@app.route('/lightup')
def light_up():
    """调用 DanymicBomServer 的点灯 API"""
    return DanymicBomServer.light_up()

@app.route('/bom_view')
def serve_bom_view():
    """调用 DanymicBomServer 的 BOM 渲染 API"""
    return DanymicBomServer.serve_bom()


# 2. 【核心】主页路由 (注入增强版脚本)
    # 逻辑现已外迁至 DanymicBomServer.py
    pass

# --- 启动服务器 ---
if __name__ == '__main__':
    print("=========================================")
    print(" 🚀 元件与BOM管理器 (DataUI) 已启动！")
    print(" 📂 数据文件: components.json")
    print(" 🌍 访问主页 (BOM View): http://127.0.0.1:5000")
    print(" ⚙️ 访问管理 (Manage UI): http://127.0.0.1:5000/manage")
    print("=========================================")
    # 使用 host='0.0.0.0' 可以让局域网内的其他设备也访问
    app.run(debug=True, host='0.0.0.0', port=5000)
