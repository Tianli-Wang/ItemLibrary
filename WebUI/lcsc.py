# -*- coding: utf-8 -*-
"""
lcsc.py — 立创商城 (szlcsc.com / lcsc.com) 参数爬取
------------------------------------------------------------------
仅依赖 Python 标准库 (urllib)，无需 requests / bs4。

背景 / 为什么这样设计
------------------------------------------------------------------
立创的「搜索」接口 (so.szlcsc.com) 有一层反爬 JS Cookie 挑战 —— 首次请求会
返回一段混淆 JS，算出 cookie 后 reload 才放行真正的结果页。本模块用 Node
(lcsc_challenge.js) 在沙箱里执行该 JS 拿到 cookie，从而支持「关键字搜索」：
    关键字 → 搜索结果页(带 cookie) → 取最匹配商品 → 抓详情页拿全部参数

同时保留「商品详情页」直抓（服务端渲染，稳定）：
  • 中文站  https://item.szlcsc.com/{商品ID}.html          -> __NEXT_DATA__ (中文参数)
  • 国际站  https://www.lcsc.com/product-detail/{立创编号}.html -> __NEXT_DATA__ (含 productId)

支持的 query：关键字(型号) / 立创编号 Cxxxxx / 商品ID / 商品链接。
交互式 BOM 里每个元件自带立创编号，管理界面也可直接粘贴。
（若本机没有 Node，关键字搜索会自动降级并提示改用 立创编号/链接。）

对外主接口：
    get_lcsc_product_data(query) -> dict
"""

import json
import re
import gzip
import io
import os
import ssl
import shutil
import tempfile
import subprocess
import urllib.request
import urllib.parse

# ---------------------------------------------------------------------------
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.szlcsc.com/",
}

# 部分环境证书链不全，放宽以保证可用性（仅用于公开只读抓取）
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_CODE_RE = re.compile(r'^C\d{3,10}$', re.I)
_CHALLENGE_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lcsc_challenge.js")
_cookie_cache = {"cookie": None}   # 反爬 cookie 缓存（进程内复用，约 5 分钟有效）


def _fetch(url, timeout=12, cookie=None):
    """GET 一个 URL，返回解码后的文本 (自动处理 gzip)。"""
    headers = dict(_HEADERS)
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        raw = resp.read()
        enc = (resp.headers.get("Content-Encoding") or "").lower()
        if "gzip" in enc:
            try:
                raw = gzip.decompress(raw)
            except OSError:
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def _extract_next_data(html):
    """从详情页解析 __NEXT_DATA__ JSON。"""
    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1).strip())
    except Exception:
        return None


def _first(*vals):
    for v in vals:
        if v not in (None, "", "N/A", "-", []):
            return v
    return None


def _absolutize(url, host="https://item.szlcsc.com"):
    """把相对链接补全为绝对链接。"""
    if url and url.startswith("//"):
        return "https:" + url
    if url and url.startswith("/"):
        return host + url
    return url


def _prefer_abs(a, b):
    """在两个候选链接中优先选择绝对地址。"""
    a_abs = bool(a) and a.startswith("http")
    b_abs = bool(b) and b.startswith("http")
    if b_abs and not a_abs:
        return b
    return _absolutize(a) or _absolutize(b)


# ---------------------------------------------------------------------------
#  详情页解析
# ---------------------------------------------------------------------------
def _parse_szlcsc(nd):
    """解析中文站 item.szlcsc.com 详情页的 __NEXT_DATA__。"""
    wd = (nd.get("props", {}).get("pageProps", {}).get("webData", {}) or {})
    if not wd:
        return None
    pr = wd.get("productRecord", {}) or {}
    catalog = (wd.get("currentCatalog", {}) or {}).get("catalogName")
    params = {}
    for p in (wd.get("paramList", []) or []):
        name = (p.get("parameterName") or "").strip()
        val = (p.get("parameterValue") or "").strip()
        if name:
            params[name] = val
    pdf = (wd.get("pdfFileDetailVO", {}) or {}).get("fileUrl")
    return {
        "lcsc_code": pr.get("productCode"),
        "product_name": pr.get("productModel"),
        "brand": (wd.get("brandVO", {}) or {}).get("brandName"),
        "catalog": catalog,
        "description": pr.get("remark"),
        "footprint": _first(pr.get("encapsulationModel"), params.get("封装")),
        "datasheet": pdf,
        "image": _first(pr.get("breviaryImageUrl"),
                        (pr.get("luceneBreviaryImageUrls") or [None])[0]),
        "stock": wd.get("totalStockNumber"),
        "params": params,
    }


def _parse_lcsc(nd):
    """解析国际站 www.lcsc.com 详情页的 __NEXT_DATA__ (英文参数, 但含 productId)。"""
    wd = (nd.get("props", {}).get("pageProps", {}).get("webData", {}) or {})
    if not wd:
        return None
    params = {}
    for p in (wd.get("paramVOList", []) or []):
        name = (p.get("paramName") or p.get("paramNameEn") or "").strip()
        val = (p.get("paramValueEn") or p.get("paramValue") or "").strip()
        if name:
            params[name] = val
    return {
        "product_id": wd.get("productId"),
        "lcsc_code": wd.get("productCode"),
        "product_name": _first(wd.get("productModel"), wd.get("title")),
        "brand": wd.get("brandNameEn"),
        "catalog": wd.get("catalogName"),
        "description": wd.get("productIntroduction"),
        "footprint": wd.get("encapStandard"),
        "datasheet": wd.get("pdfUrl"),
        "image": wd.get("productImageUrl"),
        "params": params,
    }


def get_by_id(product_id):
    """按 szlcsc 数字商品ID 抓取中文详情。"""
    url = f"https://item.szlcsc.com/{product_id}.html"
    nd = _extract_next_data(_fetch(url))
    if not nd:
        return None, url
    return _parse_szlcsc(nd), url


def get_by_code(code):
    """按 立创编号 Cxxxxx 抓取：先经国际站拿到 productId，再抓中文详情合并。"""
    code = code.upper()
    lcsc_url = f"https://www.lcsc.com/product-detail/{code}.html"
    en = None
    try:
        nd = _extract_next_data(_fetch(lcsc_url))
        if nd:
            en = _parse_lcsc(nd)
    except Exception:
        en = None

    zh, zh_url = None, None
    pid = (en or {}).get("product_id")
    if pid:
        try:
            zh, zh_url = get_by_id(pid)
        except Exception:
            zh = None

    if not zh and not en:
        return None, lcsc_url

    # 合并：中文参数优先，英文补空
    base = dict(en or {})
    if zh:
        for k, v in zh.items():
            if k == "params":
                merged = dict(en.get("params", {}) if en else {})
                merged.update(v or {})   # 中文覆盖
                base["params"] = merged
            elif k in ("datasheet", "image"):
                # 链接优先取绝对地址 (中文站常给相对路径)
                base[k] = _prefer_abs(v, base.get(k))
            elif v not in (None, "", []):
                base[k] = v
    base.setdefault("lcsc_code", code)
    return base, (zh_url or lcsc_url)


# ---------------------------------------------------------------------------
#  关键字搜索（过反爬 JS Cookie 挑战）
# ---------------------------------------------------------------------------
def _solve_challenge(challenge_html):
    """用 Node 执行反爬 JS，返回 cookie 'name=value'（无 node / 失败则 None）。"""
    node = shutil.which("node")
    if not node or not os.path.exists(_CHALLENGE_JS):
        return None
    tf = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8")
    try:
        tf.write(challenge_html)
        tf.close()
        out = subprocess.run([node, _CHALLENGE_JS, tf.name],
                             capture_output=True, text=True, timeout=20)
        return (out.stdout or "").strip() or None
    except Exception:
        return None
    finally:
        try:
            os.unlink(tf.name)
        except OSError:
            pass


def _fetch_search(keyword):
    """抓取搜索结果页 HTML（自动处理反爬 cookie，带进程内缓存）。"""
    url = "https://so.szlcsc.com/global.html?k=" + urllib.parse.quote(keyword)
    html = _fetch(url, cookie=_cookie_cache.get("cookie"))
    if "_xvpfs" in html:                          # 命中反爬挑战页
        cookie = _solve_challenge(html)
        if not cookie:
            return None
        _cookie_cache["cookie"] = cookie
        html = _fetch(url, cookie=cookie)
        if "_xvpfs" in html:                      # 仍是挑战 → 失败
            return None
    return html


def _search_top(keyword):
    """搜索关键字，返回最匹配商品 {product_id, lcsc_code, product_name, param_hint}。"""
    html = _fetch_search(keyword)
    if not html:
        return None
    nd = _extract_next_data(html)
    if nd:
        try:
            prl = (nd["props"]["pageProps"]["soData"]
                     ["searchResult"]["productRecordList"])
            if prl:
                vo = prl[0].get("productVO", {}) or {}
                return {"product_id": vo.get("productId"),
                        "lcsc_code": vo.get("productCode"),
                        "product_name": vo.get("productModel"),
                        "param_hint": prl[0].get("paramLinkedMap", {}) or {}}
        except Exception:
            pass
    m = re.search(r'item\.szlcsc\.com/(\d+)\.html', html)   # 兜底
    if m:
        return {"product_id": m.group(1)}
    return None


# ---------------------------------------------------------------------------
#  统一入口
# ---------------------------------------------------------------------------
def _classify(query):
    """把用户输入判定为 url / code / id / keyword。"""
    q = query.strip()
    # URL
    m = re.search(r'item\.szlcsc\.com/(\d+)\.html', q)
    if m:
        return ("id", m.group(1))
    m = re.search(r'product-detail/(C\d+)\.html', q, re.I)
    if m:
        return ("code", m.group(1).upper())
    # 立创编号
    if _CODE_RE.match(q):
        return ("code", q.upper())
    # 纯数字 -> 商品ID
    if q.isdigit():
        return ("id", q)
    return ("keyword", q)


def get_lcsc_product_data(query):
    """主接口：按 关键字 / 立创编号 / 商品ID / 商品URL 抓取完整参数。"""
    query = (query or "").strip()
    if not query:
        return {"success": False, "error": "未提供搜索关键字"}

    kind, value = _classify(query)
    param_hint = {}

    if kind == "keyword":
        hit = _search_top(query)
        if not hit or not (hit.get("product_id") or hit.get("lcsc_code")):
            hint = "" if shutil.which("node") else "（本机未安装 Node，无法自动过反爬）"
            return {
                "success": False, "kind": "keyword",
                "error": (f"未在立创商城搜到匹配商品{hint}。\n"
                          "可尝试更精确的型号，或直接粘贴 立创编号(如 C25795) / 商品链接。"),
                "searched": query,
            }
        param_hint = hit.get("param_hint") or {}
        # 中文详情页目前可能先返回阿里云 WAF 挑战页，直接按 product_id 抓取会拿不到
        # __NEXT_DATA__。国际站的立创编号详情页仍可稳定返回结构化数据，因此优先走编号。
        if hit.get("lcsc_code"):
            kind, value = "code", hit["lcsc_code"]
        else:
            kind, value = "id", str(hit["product_id"])

    try:
        if kind == "id":
            data, url = get_by_id(value)
        else:  # code
            data, url = get_by_code(value)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"抓取失败: {e}", "searched": query}

    if not data:
        return {"success": False,
                "error": "未能解析该商品详情 (__NEXT_DATA__ 缺失或页面结构变化)",
                "searched": query}

    params = data.get("params", {}) or {}
    for k, v in param_hint.items():        # 用搜索结果里的参数补全详情缺失项
        params.setdefault(k, v)
    catalog = data.get("catalog") or "N/A"
    result = {
        "success": True,
        "query": query,
        "product_url": url,
        "lcsc_code": data.get("lcsc_code"),
        "product_name": data.get("product_name") or query,
        "brand": data.get("brand") or "",
        "catalog": catalog,
        "description": data.get("description") or "N/A",
        "footprint": data.get("footprint") or "",
        "datasheet": _absolutize(data.get("datasheet")) or "",
        "image": _absolutize(data.get("image")) or "",
        "stock": data.get("stock"),
        "params": params,
        # ---- 兼容旧前端字段 ----
        "功能类型": _first(params.get("类型"), params.get("功能类型"),
                       params.get("功能特性")) or "N/A",
        "工作电压": _first(params.get("工作电压"), params.get("电压"),
                       params.get("额定电压")) or "N/A",
        "输出电压": params.get("输出电压") or "N/A",
        "输出电流": params.get("输出电流") or "N/A",
        "类目": catalog,
    }
    return result


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "C25795"
    print(json.dumps(get_lcsc_product_data(q), indent=2, ensure_ascii=False))
