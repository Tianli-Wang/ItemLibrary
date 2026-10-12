# -*- coding: utf-8 -*-
"""
app.py — 个人元件库 · 统一后端 (Flask)
==================================================================
合并了历史版本中的 DataUI / DanymicBomServer / InputDataset 三个旧服务，
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
import shutil
import random
import threading
from datetime import datetime, timezone

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
COMPONENTS_FILE = os.path.join(BASE_DIR, "components.json")  # 旧路径,迁移后仅作备份
DATA_DIR = os.path.join(BASE_DIR, "data")
REPOS_DIR = os.path.join(DATA_DIR, "repos")
INDEX_FILE = os.path.join(REPOS_DIR, "_index.json")
DEFAULT_REPO_ID = "electronics"
DEFAULT_REPO_NAME = "电子元件"
BOM_FILE = os.path.join(BASE_DIR, "InteractiveBOM_v7.html")   # 当前交互式 BOM
UI_FILE = "index.html"
BRIDGE_FILE = "bom_bridge.js"

app = Flask(__name__)
CORS(app)

_save_lock = threading.Lock()
_index_lock = threading.Lock()
_REPO_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}[a-z0-9]")

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
def repo_dir(repo_id):
    """返回仓库目录 (data/repos/<repo_id>),不保证目录已存在。"""
    return os.path.join(REPOS_DIR, repo_id)


def repo_components_path(repo_id):
    """返回仓库元件库 JSON 路径。"""
    return os.path.join(repo_dir(repo_id), "components.json")


def _is_safe_repo_id(repo_id):
    """仓库 id 仅允许小写字母数字与连字符,防止路径穿越。"""
    return bool(repo_id) and bool(_REPO_ID_RE.fullmatch(repo_id))


def _slugify_repo_id(name, suffix=None):
    """把仓库名转成合法的 id:小写字母数字 + 连字符 + 5 位随机后缀。"""
    base = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    base = base[:50] or "repo"
    suffix = suffix or "".join(
        random.choice("0123456789abcdefghijklmnopqrstuvwxyz") for _ in range(5)
    )
    return f"{base}-{suffix}"


def _load_index():
    """读取仓库索引;文件缺失或损坏时返回空列表(由调用方负责修复)。"""
    if not os.path.exists(INDEX_FILE):
        return []
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_index(repos):
    """原子写仓库索引:写临时文件再 rename,防止崩溃产生半截文件。"""
    tmp_path = INDEX_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(repos, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, INDEX_FILE)


def list_repos():
    """对外暴露:返回 _index.json 的副本。"""
    return list(_load_index())


def _find_repo(repo_id):
    """返回索引中匹配 id 的条目 (dict),找不到返回 None。"""
    for item in _load_index():
        if isinstance(item, dict) and item.get("id") == repo_id:
            return item
    return None


def load_repo_data(repo_id):
    """读取指定仓库的元件库。文件缺失或损坏返回空 dict。"""
    if not _is_safe_repo_id(repo_id):
        return {}
    path = repo_components_path(repo_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_repo_data(repo_id, data):
    """写回指定仓库的元件库 (原子写 + 锁)。"""
    if not _is_safe_repo_id(repo_id):
        raise ValueError(f"非法的仓库 id: {repo_id!r}")
    os.makedirs(repo_dir(repo_id), exist_ok=True)
    path = repo_components_path(repo_id)
    tmp_path = path + ".tmp"
    with _save_lock:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(tmp_path, path)


# 兼容旧调用方的薄壳,默认指向电子元件仓库。
def load_data():
    """[已废弃] 读取默认仓库,保留仅为避免遗漏的内部调用。"""
    return load_repo_data(DEFAULT_REPO_ID)


def save_data(data):
    """[已废弃] 写回默认仓库,保留仅为避免遗漏的内部调用。"""
    save_repo_data(DEFAULT_REPO_ID, data)


def ensure_repo_structure():
    """首次启动时把根目录 components.json 迁移为默认 electronics 仓库。

    后续启动检测到 REPOS_DIR 已存在就直接返回,确保迁移只执行一次。
    """
    if os.path.exists(REPOS_DIR):
        return
    os.makedirs(repo_dir(DEFAULT_REPO_ID), exist_ok=True)
    if os.path.exists(COMPONENTS_FILE):
        shutil.copy2(COMPONENTS_FILE, repo_components_path(DEFAULT_REPO_ID))
        print(f"  ✓ 已将 {os.path.basename(COMPONENTS_FILE)} 迁移到默认仓库 "
              f"{os.path.relpath(repo_components_path(DEFAULT_REPO_ID), BASE_DIR)}")
    else:
        # 没有旧数据文件时也写入一个空 dict,便于后续 save_repo_data 直接更新 mtime。
        with open(repo_components_path(DEFAULT_REPO_ID), "w", encoding="utf-8") as f:
            json.dump({}, f)
    _save_index([{
        "id": DEFAULT_REPO_ID,
        "name": DEFAULT_REPO_NAME,
        "note": "默认仓库,首次启动自动迁移。",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }])


def _set_substitutes(data, name, substitutes):
    """保存对称的一对多替换料关系，并过滤不存在或重复的元件。"""
    selected = []
    for candidate in substitutes or []:
        candidate = str(candidate).strip()
        if candidate != name and candidate in data and candidate not in selected:
            selected.append(candidate)

    # 先移除旧的反向关系，随后按本次选择重建，确保两侧始终一致。
    for component_name, component in data.items():
        existing = component.get("substitutes", [])
        if not isinstance(existing, list):
            existing = []
        cleaned = [
            item for item in existing
            if item != name and item in data and item != component_name
        ]
        if cleaned:
            component["substitutes"] = cleaned
        else:
            component.pop("substitutes", None)

    data[name]["substitutes"] = selected
    for candidate in selected:
        reverse = data[candidate].setdefault("substitutes", [])
        if name not in reverse:
            reverse.append(name)


def _lcsc_product_details(product):
    """把立创搜索结果转换为可安全批量写入元件库的字段。"""
    params = product.get("params") if isinstance(product.get("params"), dict) else {}
    note_lines = [
        f"【{product.get('lcsc_code', '')} {product.get('product_name', '')}】"
        f"{' · ' + product.get('brand', '') if product.get('brand') else ''}",
        f"【类目】{product.get('类目') or product.get('catalog') or ''}",
    ]
    if params:
        note_lines.append("【参数】")
        note_lines.extend(f"• {key}: {value}" for key, value in list(params.items())[:12])
    description = product.get("description")
    if description and description != "N/A":
        note_lines.append(f"【描述】{description}")

    details = {
        "image": product.get("image") or "",
        "lcsc_code": product.get("lcsc_code") or "",
        "product_url": product.get("product_url") or "",
        "datasheet": product.get("datasheet") or "",
        "brand": product.get("brand") or "",
        "category": product.get("类目") or product.get("catalog") or "N/A",
        "type": product.get("功能类型") or "N/A",
        "work_voltage": product.get("工作电压") or "N/A",
        "out_voltage": product.get("输出电压") or "N/A",
        "out_current": product.get("输出电流") or "N/A",
        "note": "\n".join(note_lines).strip(),
    }
    if product.get("footprint"):
        details["footprint"] = product["footprint"]
    if product.get("工作电压") and product["工作电压"] != "N/A":
        details["voltage"] = product["工作电压"]
    return details


def _normalize_lcsc_parameter(details):
    """立创搜索结果保存时，确保 parameter 使用原厂型号而不是立创编号。"""
    normalized = dict(details)
    product_name = str(normalized.get("product_name") or "").strip()
    lcsc_code = str(normalized.get("lcsc_code") or "").strip()

    if not product_name and lcsc_code:
        # 兼容已经打开的旧页面：旧请求未单独提交 product_name，但备注首行含
        # “【C357836 AON6382】”，后端仍可据此恢复原厂型号。
        note = str(normalized.get("note") or "")
        match = re.search(
            rf"【\s*{re.escape(lcsc_code)}\s+([^】]+?)\s*】",
            note,
            re.IGNORECASE,
        )
        if match:
            product_name = match.group(1).strip()

    if product_name:
        normalized["product_name"] = product_name
        normalized["parameter"] = product_name
    return normalized


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
def search_component(part_number, parameter=None, footprint=None, repo_id=None):
    """智能匹配：型号/参数/封装 → 最佳元件。返回 (key, data) 或 (None, None)。"""
    db = load_repo_data(repo_id or DEFAULT_REPO_ID)

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
def _resolve_repo_id():
    """从 query 参数取出仓库 id,非法或缺失时回退到默认仓库。"""
    repo_id = request.args.get("repo", DEFAULT_REPO_ID)
    if not _is_safe_repo_id(repo_id) or _find_repo(repo_id) is None:
        return DEFAULT_REPO_ID
    return repo_id


@app.route("/api/components", methods=["GET"])
def get_components():
    return jsonify(load_repo_data(_resolve_repo_id()))


@app.route("/api/add", methods=["POST"])
def add_component():
    body = request.json or {}
    name = body.get("component_name")
    details = body.get("details")
    if not name or not details:
        return jsonify({"success": False, "error": "数据不完整"}), 400
    repo_id = _resolve_repo_id()
    data = load_repo_data(repo_id)
    if name in data:
        return jsonify({"success": False, "error": "该元件名称已存在"}), 400
    data[name] = _normalize_lcsc_parameter(details)
    _set_substitutes(data, name, data[name].get("substitutes", []))
    save_repo_data(repo_id, data)
    return jsonify({"success": True, "component_name": name, "repo": repo_id})


@app.route("/api/update", methods=["POST"])
def update_component():
    body = request.json or {}
    name = body.get("component_name")
    details = body.get("details")
    if not name or not details:
        return jsonify({"success": False, "error": "数据不完整"}), 400
    repo_id = _resolve_repo_id()
    data = load_repo_data(repo_id)
    if name not in data:
        return jsonify({"success": False, "error": "元件不存在"}), 404
    details = _normalize_lcsc_parameter(details)
    substitutes_provided = "substitutes" in details
    substitutes = details.pop("substitutes", [])
    data[name].update(details)
    # 快速修改货位时不会携带 substitutes，此时保留原有关联。
    if substitutes_provided:
        _set_substitutes(data, name, substitutes)
    save_repo_data(repo_id, data)
    return jsonify({"success": True, "component_name": name, "repo": repo_id})


@app.route("/api/delete", methods=["POST"])
def delete_component():
    body = request.json or {}
    name = body.get("component_name")
    if not name:
        return jsonify({"success": False, "error": "未提供元件名称"}), 400
    repo_id = _resolve_repo_id()
    data = load_repo_data(repo_id)
    if name not in data:
        return jsonify({"success": False, "error": "元件未找到"}), 404
    del data[name]
    # 删除元件时同步清理其他元件中的失效替换料引用。
    for component in data.values():
        existing = component.get("substitutes", [])
        if isinstance(existing, list):
            component["substitutes"] = [item for item in existing if item != name]
    save_repo_data(repo_id, data)
    return jsonify({"success": True, "component_name": name, "repo": repo_id})


@app.route("/api/swap_components", methods=["POST"])
def swap_components():
    body = request.json or {}
    name1, name2 = body.get("name1"), body.get("name2")
    if not name1 or not name2:
        return jsonify({"success": False, "error": "请提供两个元件名称"}), 400
    repo_id = _resolve_repo_id()
    data = load_repo_data(repo_id)
    if name1 not in data or name2 not in data:
        return jsonify({"success": False, "error": "元件未找到"}), 404
    b1, l1 = data[name1].get("box_id"), data[name1].get("led_id")
    b2, l2 = data[name2].get("box_id"), data[name2].get("led_id")
    data[name1]["box_id"], data[name1]["led_id"] = b2, l2
    data[name2]["box_id"], data[name2]["led_id"] = b1, l1
    save_repo_data(repo_id, data)
    return jsonify({"success": True, "repo": repo_id})


# ================================================================== 路由：搜索
@app.route("/lightup")
def light_up():
    """点灯/查找货位 — 始终查询电子元件仓库 (BOM 桥接唯一命中点)。"""
    part_number = request.args.get("part_number", "")
    parameter = request.args.get("parameter", "")
    footprint = request.args.get("footprint", "")
    if not (part_number or parameter or footprint):
        return jsonify({"status": "error", "message": "需要提供至少一个搜索条件"}), 400

    matched_pn, item = search_component(part_number, parameter, footprint,
                                       repo_id=DEFAULT_REPO_ID)
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

    repo_id = _resolve_repo_id()
    results = []
    for pn, data in load_repo_data(repo_id).items():
        v = parse_electronic_value(data.get("parameter", ""))
        if v is not None and min_val <= v <= max_val:
            results.append({"part_number": pn, "data": data, "value": v})
    results.sort(key=lambda x: x["value"])
    return jsonify({"status": "success", "count": len(results),
                    "results": results,
                    "repo": repo_id,
                    "query": {"min": min_val, "max": max_val}})


# ================================================================== 路由：立创
@app.route("/api/crawl_lcsc")
def crawl_lcsc():
    keyword = request.args.get("keyword")
    if not keyword:
        return jsonify({"success": False, "error": "未提供搜索关键字"}), 400
    return jsonify(lcsc.get_lcsc_product_data(keyword))


@app.route("/api/batch_crawl_lcsc", methods=["POST"])
def batch_crawl_lcsc():
    """按所选元件自身的立创编号或参数分别搜索商品。"""
    body = request.get_json(silent=True) or {}
    names = body.get("component_names")
    if not isinstance(names, list) or not names:
        return jsonify({"success": False, "error": "请至少选择一个元件"}), 400

    repo_id = _resolve_repo_id()
    data = load_repo_data(repo_id)
    unique_names = list(dict.fromkeys(str(name).strip() for name in names))
    if len(unique_names) > 30:
        return jsonify({"success": False, "error": "单次最多搜索 30 个元件"}), 400
    missing = [name for name in unique_names if name not in data]
    if missing:
        return jsonify({"success": False, "error": f"以下元件不存在: {', '.join(missing[:5])}"}), 404

    results = []
    for name in unique_names:
        # 与录入区单项搜索保持一致：优先按 parameter 搜索，编号仅作为空参数兜底。
        query = str(data[name].get("parameter") or data[name].get("lcsc_code") or "").strip()
        if query:
            product = lcsc.get_lcsc_product_data(query)
        else:
            product = {"success": False, "error": "元件没有可搜索的参数"}
        results.append({"component_name": name, "query": query, "product": product})
    success_count = sum(bool(item["product"].get("success")) for item in results)
    return jsonify({
        "success": True,
        "count": len(results),
        "success_count": success_count,
        "failed_count": len(results) - success_count,
        "results": results,
        "repo": repo_id,
    })


@app.route("/api/batch_update_lcsc", methods=["POST"])
def batch_update_lcsc():
    """把每个元件各自的立创搜索结果写入库中，并保留货位和替换料。"""
    body = request.get_json(silent=True) or {}
    updates = body.get("updates")
    if not isinstance(updates, list) or not updates:
        return jsonify({"success": False, "error": "没有可更新的立创搜索结果"}), 400

    repo_id = _resolve_repo_id()
    data = load_repo_data(repo_id)
    valid_updates = []
    seen = set()
    for update in updates:
        if not isinstance(update, dict):
            continue
        name = str(update.get("component_name") or "").strip()
        product = update.get("product")
        if name and name not in seen and isinstance(product, dict) and product.get("success"):
            valid_updates.append((name, product))
            seen.add(name)
    if not valid_updates:
        return jsonify({"success": False, "error": "没有搜索成功的元件"}), 400

    unique_names = [name for name, _ in valid_updates]
    missing = [name for name in unique_names if name not in data]
    if missing:
        return jsonify({
            "success": False,
            "error": f"以下元件不存在: {', '.join(missing[:5])}",
        }), 404

    for name, product in valid_updates:
        data[name].update(_lcsc_product_details(product))
    save_repo_data(repo_id, data)
    return jsonify({
        "success": True,
        "updated_count": len(unique_names),
        "component_names": unique_names,
        "repo": repo_id,
    })


# ================================================================== 路由：仓库管理
@app.route("/api/repos", methods=["GET"])
def api_repos_list():
    """返回所有仓库的元数据列表。"""
    return jsonify(list_repos())


@app.route("/api/repos", methods=["POST"])
def api_repos_create():
    """新建一个空白仓库。name 必填,note 可选;id 自动生成。"""
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    note = str(body.get("note") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "仓库名称不能为空"}), 400

    with _index_lock:
        repos = _load_index()
        # 名字去重 (忽略大小写与首尾空白),名字已存在则直接复用同名仓库。
        existing = next(
            (item for item in repos
             if isinstance(item, dict)
             and str(item.get("name", "")).strip().lower() == name.lower()),
            None,
        )
        if existing is not None:
            return jsonify({"success": True, "repo": existing, "existed": True})

        # 多次重试以避免与现有 id 极小概率冲突。
        repo_id = None
        for _ in range(8):
            candidate = _slugify_repo_id(name)
            if not any(item.get("id") == candidate for item in repos
                       if isinstance(item, dict)):
                repo_id = candidate
                break
        if repo_id is None:
            return jsonify({"success": False, "error": "无法生成仓库 id"}), 500

        new_repo = {
            "id": repo_id,
            "name": name,
            "note": note,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        # 先写空文件再写索引,避免索引声称有仓库但磁盘上读不到。
        save_repo_data(repo_id, {})
        repos.append(new_repo)
        _save_index(repos)
        print(f"  ✓ 新建仓库: {name} ({repo_id})")
        return jsonify({"success": True, "repo": new_repo, "existed": False})


@app.route("/api/repos/<repo_id>", methods=["PATCH"])
def api_repos_update(repo_id):
    """修改仓库的 name / note;不允许通过此接口改 id。"""
    if not _is_safe_repo_id(repo_id):
        return jsonify({"success": False, "error": "非法仓库 id"}), 400

    body = request.get_json(silent=True) or {}
    with _index_lock:
        repos = _load_index()
        target = next(
            (item for item in repos
             if isinstance(item, dict) and item.get("id") == repo_id),
            None,
        )
        if target is None:
            return jsonify({"success": False, "error": "仓库不存在"}), 404

        if "name" in body:
            new_name = str(body.get("name") or "").strip()
            if not new_name:
                return jsonify({"success": False, "error": "仓库名称不能为空"}), 400
            target["name"] = new_name
        if "note" in body:
            target["note"] = str(body.get("note") or "").strip()
        _save_index(repos)
        return jsonify({"success": True, "repo": target})


@app.route("/api/repos/<repo_id>", methods=["DELETE"])
def api_repos_delete(repo_id):
    """删除仓库目录。默认仓库 electronics 不可删除,只剩一个仓库时也不允许。"""
    if not _is_safe_repo_id(repo_id):
        return jsonify({"success": False, "error": "非法仓库 id"}), 400
    if repo_id == DEFAULT_REPO_ID:
        return jsonify({"success": False,
                        "error": f"默认仓库 {DEFAULT_REPO_NAME} 不能删除"}), 400

    with _index_lock:
        repos = _load_index()
        if len(repos) <= 1:
            return jsonify({"success": False, "error": "至少保留一个仓库"}), 400
        target = next(
            (item for item in repos
             if isinstance(item, dict) and item.get("id") == repo_id),
            None,
        )
        if target is None:
            return jsonify({"success": False, "error": "仓库不存在"}), 404

        # 先从索引里移除,即使后续删目录失败,前端也不应再访问该仓库。
        repos = [item for item in repos if item.get("id") != repo_id]
        _save_index(repos)

    dir_path = repo_dir(repo_id)
    if os.path.isdir(dir_path):
        shutil.rmtree(dir_path, ignore_errors=True)
    print(f"  ✓ 已删除仓库: {target.get('name')} ({repo_id})")
    return jsonify({"success": True, "deleted": repo_id})


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
    ensure_repo_structure()
    print("=" * 52)
    print(" 🚀 个人元件库 · 统一服务已启动")
    print(f" 📂 数据目录 : {os.path.relpath(REPOS_DIR, BASE_DIR)} "
          f"({len(list_repos())} 个仓库)")
    print(f" 📄 BOM 文件 : {os.path.basename(BOM_FILE)}")
    print(" 🌍 管理界面 : http://127.0.0.1:5000/")
    print(" 🔍 BOM 交互 : http://127.0.0.1:5000/bom_view")
    print("=" * 52)
    # 串口需要由单一进程持续持有，禁止调试重载器重复创建进程和释放 COM3。
    app.run(debug=False, host="0.0.0.0", port=5000)
