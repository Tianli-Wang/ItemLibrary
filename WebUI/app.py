# -*- coding: utf-8 -*-
"""
app.py — 个人元件库 · 统一后端 (Flask)
==================================================================
合并了旧的 DataUI.py / DanymicBomServer.py / InputDataset.py 三个服务，
并去除了 bs4 / requests 依赖 —— 现在仅需 Flask + flask-cors，可直接在
Python 3.14 上运行。

功能：
  • 元件库 CRUD              /api/components /api/add /api/update /api/delete
  • 位置交换                 /api/swap_components
  • 智能点灯搜索             /lightup       (型号/参数/封装 → box_id, pos_id)
  • 数值范围搜索             /search_range
  • 立创商城参数抓取         /api/crawl_lcsc   (见 lcsc.py)
  • 交互式 BOM 服务          /bom_view      (读取一次 + 注入桥接脚本 + 内存缓存)
  • BOM 桥接脚本             /bom_bridge.js
  • BOM 文件上传替换         /api/upload_bom
  • 管理界面 (现代浅色 SPA)  /  与 /manage  → index.html

作者: Tianli-Wang （重构 by Claude）
"""

import os
import re
import json
import sys
import threading

import serial

from flask import (Flask, request, jsonify, send_from_directory, Response)
from flask_cors import CORS

import lcsc

# Windows 控制台默认 GBK，强制 UTF-8 以免打印 emoji/中文时崩溃
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ------------------------------------------------------------------ 配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COMPONENTS_FILE = os.path.join(BASE_DIR, "components.json")
BOM_FILE = os.path.join(BASE_DIR, "InteractiveBOM_v7.html")   # 当前交互式 BOM
UI_FILE = "index.html"
BRIDGE_FILE = "bom_bridge.js"

app = Flask(__name__)
CORS(app)

_save_lock = threading.Lock()

# 点灯主控由 Flask 独占 COM3，避免 Web Serial 权限窗口和 CH343 控制线兼容问题。
MASTER_SERIAL_PORT = os.environ.get("ITEMLIB_MASTER_PORT", "COM3")
MASTER_SERIAL_BAUD = 115200
_master_serial = None
_master_serial_lock = threading.Lock()


def _open_master_serial_locked():
    """调用方持有串口锁时，打开或复用点灯主控串口。"""
    global _master_serial
    if _master_serial is not None and _master_serial.is_open:
        return _master_serial

    port = serial.Serial()
    port.port = MASTER_SERIAL_PORT
    port.baudrate = MASTER_SERIAL_BAUD
    port.bytesize = serial.EIGHTBITS
    port.parity = serial.PARITY_NONE
    port.stopbits = serial.STOPBITS_ONE
    port.timeout = 0
    port.write_timeout = 1
    port.xonxoff = False
    port.rtscts = False
    port.dsrdtr = False
    port.open()
    _master_serial = port
    return port


# ================================================================== 数据层
def load_data():
    """从 components.json 读取全部元件。"""
    if not os.path.exists(COMPONENTS_FILE):
        return {}
    try:
        with open(COMPONENTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_data(data):
    """写回 components.json（加锁，避免并发写坏文件）。"""
    with _save_lock:
        with open(COMPONENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)


# ================================================================== 数值归一化
def parse_electronic_value(val_str):
    """把电子元件数值归一化为浮点数 (e.g. '10k' -> 10000.0, '100n' -> 1e-7)。"""
    if not val_str:
        return None
    s = str(val_str).strip().replace("µ", "u").replace("μ", "u")
    s = re.sub(r'\s+', '', s)
    s = re.sub(r'ohms?', 'Ω', s, flags=re.I)
    s = re.sub(r'farads?', 'F', s, flags=re.I)

    # 参数后面可能附带精度/电压，只取开头带明确单位的主数值。
    prefix = re.match(
        r'^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[rRkKmMgGuUnNpP]\d*)?(?:[ΩFfHhVv])?)'
        r'(?=$|[±+\-@/,;，；（(])',
        s,
    )
    if prefix:
        s = prefix.group(1)

    # 处理 4k7 / 4R7 / 2u2 等单位位于小数点处的标法。
    m = re.fullmatch(r'([+-]?\d+)([rRkKmMgGuUnNpP])(\d+)(?:[ΩFfHhVv])?', s)
    if m:
        p1, p2, p3 = m.groups()
        multiplier = _unit_multiplier(p2)
        return float(f"{p1}.{p3}") * multiplier if multiplier is not None else None

    m = re.fullmatch(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+))([pPnNuUmMkKgGrR]?)(?:[ΩFfHhVv])?', s)
    if m:
        val = float(m.group(1))
        unit = m.group(2)
        multiplier = _unit_multiplier(unit)
        return val * multiplier if multiplier is not None else None
    return None


def _unit_multiplier(unit):
    """电子单位倍率；大小写 M/m 必须保留，分别表示兆和毫。"""
    units = {
        '': 1, 'r': 1, 'R': 1, 'p': 1e-12, 'P': 1e-12,
        'n': 1e-9, 'N': 1e-9, 'u': 1e-6, 'U': 1e-6,
        'm': 1e-3, 'k': 1e3, 'K': 1e3, 'M': 1e6, 'g': 1e9, 'G': 1e9,
    }
    return units.get(unit)


def normalize_part_number(value):
    """去掉型号中的空格和连接符，保留用于比对的字母数字。"""
    return re.sub(r'[^A-Z0-9]', '', str(value or '').upper())


def equivalent_part_number(left, right):
    """按稳定型号前缀判断同系列订购型号，允许末尾多位包装码不同。"""
    a = normalize_part_number(left)
    b = normalize_part_number(right)
    if not a or not b:
        return False
    if a == b:
        return True

    prefix_length = 0
    shorter_length = min(len(a), len(b))
    while prefix_length < shorter_length and a[prefix_length] == b[prefix_length]:
        prefix_length += 1

    # 首个差异若同为数字，通常表示主型号本身发生变化，不应当成包装码忽略。
    if (prefix_length < shorter_length and a[prefix_length].isdigit()
            and b[prefix_length].isdigit()):
        return False

    # 前缀既要有最低信息量，也要覆盖较短型号的大部分，避免宽泛误匹配。
    return prefix_length >= 6 and prefix_length / shorter_length >= 2 / 3


# ================================================================== 智能搜索
def search_component(part_number, parameter=None, footprint=None):
    """智能匹配：型号/参数/封装 → 最佳元件。返回 (key, data) 或 (None, None)。"""
    db = load_data()

    # 1. 绝对精确匹配 (Key)
    if part_number and part_number in db:
        return part_number, db[part_number]
    if not (part_number or parameter or footprint):
        return None, None

    search_val = parse_electronic_value(parameter)
    search_fp = (footprint or "").upper()
    fp_code_m = re.search(r'(\d{4,5})', search_fp)
    search_fp_code = fp_code_m.group(1) if fp_code_m else None

    search_volt = None
    if parameter:
        vm = re.search(r'(\d+)V', str(parameter), re.I)
        if vm:
            search_volt = vm.group(1)

    matches = []
    for pn, data in db.items():
        score = 0
        reasons = []
        strong_pn_match = False

        db_param_str = data.get("parameter", "")
        db_fp_str = data.get("footprint", "")
        db_volt_str = str(data.get("voltage", "")).replace("V", "").replace("v", "")
        db_val = parse_electronic_value(db_param_str)

        # A. 型号匹配
        if part_number:
            target_pn = part_number.upper()
            db_param_upper = db_param_str.upper()
            pn_upper = pn.upper()
            if equivalent_part_number(target_pn, pn_upper) or equivalent_part_number(target_pn, db_param_upper):
                score += 100
                reasons.append("ID/型号等价匹配")
            else:
                is_rc = (search_val is not None) or (db_val is not None)
                if is_rc:
                    pat = r'\b' + re.escape(target_pn) + r'\b'
                    if (re.search(pat, db_param_upper) or
                            (db_param_upper and re.search(
                                r'\b' + re.escape(db_param_upper) + r'\b', target_pn))):
                        score += 40
                        reasons.append("RC规格文本匹配")
                else:
                    if target_pn in db_param_upper or (db_param_upper and db_param_upper in target_pn):
                        score += 80
                        reasons.append("芯片型号匹配")
                        strong_pn_match = True
                    elif target_pn in pn_upper or pn_upper in target_pn:
                        score += 80
                        reasons.append("芯片ID匹配")
                        strong_pn_match = True

        # B. 数值逻辑匹配
        if search_val is not None and db_val is not None:
            mx = max(search_val, db_val)
            if mx > 0 and abs(search_val - db_val) / mx < 0.001:
                score += 50
                reasons.append("数值逻辑一致")
        elif parameter and db_param_str and parameter.upper() == db_param_str.upper():
            score += 30
            reasons.append("参数文本一致")

        # C. 封装匹配
        fp_matched = False
        if search_fp and db_fp_str:
            db_fp_upper = db_fp_str.upper()
            db_fp_clean = re.sub(r'[^A-Z0-9]', '', db_fp_upper)
            search_fp_clean = re.sub(r'[^A-Z0-9]', '', search_fp)
            if search_fp == db_fp_upper:
                score += 25; reasons.append("封装完美匹配"); fp_matched = True
            elif search_fp_clean == db_fp_clean:
                score += 20; reasons.append("封装去干扰匹配"); fp_matched = True
            elif (db_fp_clean and db_fp_clean in search_fp) or (search_fp in db_fp_upper):
                score += 15; reasons.append("封装包含匹配"); fp_matched = True
            else:
                dbm = re.search(r'(\d{4,5})', db_fp_upper)
                db_fp_code = dbm.group(1) if dbm else None
                if search_fp_code and db_fp_code and search_fp_code == db_fp_code:
                    score += 20; reasons.append("封装标准规格匹配"); fp_matched = True
                elif search_fp in db_fp_upper or db_fp_upper in search_fp:
                    score += 10; reasons.append("封装模糊匹配"); fp_matched = True

        if search_fp and db_fp_str and not fp_matched:
            if not (strong_pn_match or (part_number and part_number.upper() in search_fp)):
                score -= 30
                reasons.append("封装不匹配(惩罚)")

        # D. 耐压值
        if search_volt and db_volt_str and search_volt == db_volt_str:
            score += 20
            reasons.append("耐压值匹配")

        # E. 备注
        db_note = data.get("note", "").upper()
        if part_number:
            if re.search(r'\b' + re.escape(part_number.upper()) + r'\b', db_note):
                score += 15; reasons.append("备注包含型号")
            elif part_number.upper() in db_note:
                score += 5; reasons.append("备注模糊包含型号")
        if parameter and parameter.upper() != (part_number or "").upper():
            if re.search(r'\b' + re.escape(parameter.upper()) + r'\b', db_note):
                score += 10; reasons.append("备注包含参数")

        if score >= 40:
            matches.append({"part_number": pn, "data": data,
                            "score": score, "reasons": reasons})

    matches.sort(key=lambda x: x["score"], reverse=True)
    if matches:
        best = matches[0]
        print(f"  ✅ 最佳匹配: {best['part_number']} "
              f"(得分:{best['score']}) {', '.join(best['reasons'])}")
        return best["part_number"], best["data"]
    return None, None


# ================================================================== BOM 缓存
_bom_cache = {"mtime": None, "html": None}


def get_bom_html():
    """读取交互式 BOM，注入桥接脚本，并按 mtime 缓存 (避免每次请求重解析 10MB)。"""
    if not os.path.exists(BOM_FILE):
        return None
    mtime = os.path.getmtime(BOM_FILE)
    if _bom_cache["html"] is None or _bom_cache["mtime"] != mtime:
        with open(BOM_FILE, "r", encoding="utf-8") as f:
            html = f.read()
        tag = '\n<script src="/bom_bridge.js"></script>\n'
        idx = html.lower().rfind("</body>")
        if idx != -1:
            html = html[:idx] + tag + html[idx:]
        else:
            html += tag
        _bom_cache["html"] = html
        _bom_cache["mtime"] = mtime
        print(f"  ✓ BOM 已加载并注入桥接脚本 ({len(html)//1024} KB, 已缓存)")
    return _bom_cache["html"]


# ================================================================== 路由：界面
@app.route("/")
@app.route("/manage")
def serve_ui():
    return send_from_directory(BASE_DIR, UI_FILE)


@app.route("/bom_bridge.js")
def serve_bridge():
    resp = send_from_directory(BASE_DIR, BRIDGE_FILE, mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/bom_view")
def serve_bom_view():
    html = get_bom_html()
    if html is None:
        return (f"错误: 找不到 BOM 文件 {os.path.basename(BOM_FILE)}。"
                f"请在管理界面『导入 BOM』上传。", 404)
    return Response(html, mimetype="text/html")


# ================================================================== 路由：CRUD
@app.route("/api/components", methods=["GET"])
def get_components():
    return jsonify(load_data())


@app.route("/api/add", methods=["POST"])
def add_component():
    body = request.json or {}
    name = body.get("component_name")
    details = body.get("details")
    if not name or not details:
        return jsonify({"success": False, "error": "数据不完整"}), 400
    data = load_data()
    if name in data:
        return jsonify({"success": False, "error": "该元件名称已存在"}), 400
    data[name] = details
    save_data(data)
    return jsonify({"success": True, "component_name": name})


@app.route("/api/update", methods=["POST"])
def update_component():
    body = request.json or {}
    name = body.get("component_name")
    details = body.get("details")
    if not name or not details:
        return jsonify({"success": False, "error": "数据不完整"}), 400
    data = load_data()
    if name not in data:
        return jsonify({"success": False, "error": "元件不存在"}), 404
    data[name].update(details)
    save_data(data)
    return jsonify({"success": True, "component_name": name})


@app.route("/api/delete", methods=["POST"])
def delete_component():
    body = request.json or {}
    name = body.get("component_name")
    if not name:
        return jsonify({"success": False, "error": "未提供元件名称"}), 400
    data = load_data()
    if name not in data:
        return jsonify({"success": False, "error": "元件未找到"}), 404
    del data[name]
    save_data(data)
    return jsonify({"success": True, "component_name": name})


@app.route("/api/swap_components", methods=["POST"])
def swap_components():
    body = request.json or {}
    name1, name2 = body.get("name1"), body.get("name2")
    if not name1 or not name2:
        return jsonify({"success": False, "error": "请提供两个元件名称"}), 400
    data = load_data()
    if name1 not in data or name2 not in data:
        return jsonify({"success": False, "error": "元件未找到"}), 404
    b1, l1 = data[name1].get("box_id"), data[name1].get("led_id")
    b2, l2 = data[name2].get("box_id"), data[name2].get("led_id")
    data[name1]["box_id"], data[name1]["led_id"] = b2, l2
    data[name2]["box_id"], data[name2]["led_id"] = b1, l1
    save_data(data)
    return jsonify({"success": True})


# ================================================================== 路由：搜索
@app.route("/lightup")
def light_up():
    part_number = request.args.get("part_number", "")
    parameter = request.args.get("parameter", "")
    footprint = request.args.get("footprint", "")
    if not (part_number or parameter or footprint):
        return jsonify({"status": "error", "message": "需要提供至少一个搜索条件"}), 400

    matched_pn, item = search_component(part_number, parameter, footprint)
    if not item:
        return jsonify({
            "status": "not_found",
            "message": "未找到匹配的元件",
            "searched": {"part_number": part_number,
                         "parameter": parameter, "footprint": footprint},
        })

    box_id = item.get("box_id")
    led_id = item.get("led_id")
    return jsonify({
        "status": "success",
        "message": "找到元件",
        "matched_part_number": matched_pn,
        "location": {"box_id": box_id, "led_id": led_id, "pos_id": led_id},
        "details": item,
    })


@app.route("/search_range")
def search_range():
    min_str = request.args.get("min", "0")
    max_str = request.args.get("max", "inf")
    min_val = parse_electronic_value(min_str) or 0.0
    if max_str.lower() == "inf":
        max_val = float("inf")
    else:
        max_val = parse_electronic_value(max_str) or float("inf")

    results = []
    for pn, data in load_data().items():
        v = parse_electronic_value(data.get("parameter", ""))
        if v is not None and min_val <= v <= max_val:
            results.append({"part_number": pn, "data": data, "value": v})
    results.sort(key=lambda x: x["value"])
    return jsonify({"status": "success", "count": len(results),
                    "results": results,
                    "query": {"min": min_val, "max": max_val}})


# ================================================================== 路由：立创
@app.route("/api/crawl_lcsc")
def crawl_lcsc():
    keyword = request.args.get("keyword")
    if not keyword:
        return jsonify({"success": False, "error": "未提供搜索关键字"}), 400
    return jsonify(lcsc.get_lcsc_product_data(keyword))


# ================================================================== 路由：BOM 上传
@app.route("/api/upload_bom", methods=["POST"])
def upload_bom():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "没有文件部件"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"success": False, "error": "没有选择文件"}), 400
    if not file.filename.lower().endswith(".html"):
        return jsonify({"success": False, "error": "仅支持 .html 文件"}), 400
    file.save(BOM_FILE)
    _bom_cache["mtime"] = None      # 让缓存失效
    print(f"  ✓ BOM 文件已更新: {file.filename}")
    return jsonify({"success": True, "message": f"成功更新 BOM: {file.filename}"})


# ================================================================== 路由：点灯主控串口
@app.route("/api/master_serial/status")
def master_serial_status():
    with _master_serial_lock:
        connected = _master_serial is not None and _master_serial.is_open
    return jsonify({
        "success": True,
        "connected": connected,
        "port": MASTER_SERIAL_PORT,
        "baud_rate": MASTER_SERIAL_BAUD,
    })


@app.route("/api/master_serial/connect", methods=["POST"])
def master_serial_connect():
    try:
        with _master_serial_lock:
            _open_master_serial_locked()
        print(f"  点灯主控已连接: {MASTER_SERIAL_PORT} @ {MASTER_SERIAL_BAUD}")
        return jsonify({"success": True, "port": MASTER_SERIAL_PORT})
    except (serial.SerialException, OSError) as error:
        return jsonify({
            "success": False,
            "error": f"无法打开 {MASTER_SERIAL_PORT}: {error}",
        }), 503


@app.route("/api/master_serial/disconnect", methods=["POST"])
def master_serial_disconnect():
    global _master_serial
    with _master_serial_lock:
        if _master_serial is not None:
            try:
                _master_serial.close()
            finally:
                _master_serial = None
    return jsonify({"success": True})


@app.route("/api/master_serial/send", methods=["POST"])
def master_serial_send():
    payload = request.get_json(silent=True) or {}
    try:
        box_id = int(payload.get("box_id"))
        led_id = int(payload.get("led_id"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "box_id 和 led_id 必须是整数"}), 400

    if not (0 <= box_id <= 65535 and 0 <= led_id <= 65535):
        return jsonify({"success": False, "error": "box_id 或 led_id 超出范围"}), 400

    command = f"box_id:{box_id},led_id:{led_id}\n".encode("ascii")
    try:
        with _master_serial_lock:
            port = _open_master_serial_locked()
            port.write(command)
            port.flush()
        print(f"  UART TX -> {MASTER_SERIAL_PORT}: {command.decode('ascii').strip()}")
        return jsonify({"success": True, "command": command.decode("ascii").strip()})
    except (serial.SerialException, serial.SerialTimeoutException, OSError) as error:
        return jsonify({"success": False, "error": f"串口发送失败: {error}"}), 503


# ================================================================== 启动
if __name__ == "__main__":
    print("=" * 52)
    print(" 🚀 个人元件库 · 统一服务已启动")
    print(f" 📂 数据文件 : {os.path.basename(COMPONENTS_FILE)} "
          f"({len(load_data())} 个元件)")
    print(f" 📄 BOM 文件 : {os.path.basename(BOM_FILE)}")
    print(" 🌍 管理界面 : http://127.0.0.1:5000/")
    print(" 🔍 BOM 交互 : http://127.0.0.1:5000/bom_view")
    print("=" * 52)
    # 串口需要由单一进程持续持有，禁止调试重载器重复创建进程和释放 COM3。
    app.run(debug=False, host="0.0.0.0", port=5000)
