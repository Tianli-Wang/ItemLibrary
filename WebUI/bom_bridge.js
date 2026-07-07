/* =====================================================================
 * bom_bridge.js — 交互式 BOM 桥接脚本 (注入到 立创EDA 离线交互式 BOM)
 * ---------------------------------------------------------------------
 * 职责：
 *   1. 捕获用户「选中某个元件」的动作 —— 无论是点击 BOM 表格行，
 *      还是在 2D/3D PCB 视图里点选元件。
 *   2. 读取该元件的 型号 / 参数(值) / 封装 / 位号，向后端 /lightup 查询，
 *      得到料盒 box_id 与 位置 pos_id。
 *   3. 直接在浏览器提示框(Toast)中醒目地弹出 Box / Pos 位置。
 *   4. 通过 postMessage 把结果回传给外层管理界面(index.html)。
 *
 * 捕获采用三条冗余通路，互相兜底：
 *   A) 表格行点击    —— 捕获阶段 click 委托，直接读所在 <tr> 的单元格
 *   B) 高亮变化观察  —— MutationObserver 监听 <tbody> 中行背景/高亮变化
 *                       （3D/画布点选元件时，对应 BOM 行会被高亮）
 *   C) console.log 钩子 —— 立创BOM 行点击原生会打印 器件编号/型号/值，作兜底
 * ===================================================================== */
(function () {
  "use strict";
  if (window.__ilBridgeLoaded) return;
  window.__ilBridgeLoaded = true;

  var DEBUG = false;                      // 需排查时置 true：打印详细日志并暴露 window.__ilDebug
  var LIGHTUP_URL = "/lightup";
  var inIframe = window.self !== window.top;

  function log() {
    if (DEBUG) { try { console.info.apply(console, ["[IL-Bridge]"].concat([].slice.call(arguments))); } catch (e) {} }
  }
  function norm(s) { return (s || "").replace(/\s+/g, " ").trim(); }
  // 去掉单元格里的 UI 噪声：详情/展开/收起、以及「N个器件」计数
  function cleanText(s) { return norm((s || "").replace(/详情|展开|收起/g, "")); }
  function cleanDesig(s) {
    return norm((s || "").replace(/详情|展开|收起/g, "")
                         .replace(/\d+\s*个器件/g, "").replace(/器件/g, ""));
  }

  /* ---------------------------------------------------------------- 样式 + 提示框 */
  var toastEl;
  function injectUI() {
    var css = document.createElement("style");
    css.textContent = [
      "#il-toast{position:fixed;top:18px;left:50%;transform:translateX(-50%) translateY(-140%);",
      "z-index:2147483647;min-width:300px;max-width:92vw;padding:14px 20px;border-radius:16px;",
      "background:rgba(20,24,33,.92);color:#fff;-webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px);",
      "box-shadow:0 12px 40px rgba(0,0,0,.35);font-family:-apple-system,'Segoe UI',Roboto,sans-serif;",
      "display:flex;align-items:center;gap:16px;opacity:0;transition:transform .32s cubic-bezier(.2,.9,.3,1.2),opacity .32s;cursor:pointer;}",
      "#il-toast.show{transform:translateX(-50%) translateY(0);opacity:1;}",
      "#il-toast .il-ic{font-size:26px;line-height:1;}",
      "#il-toast .il-main{flex:1;min-width:0;}",
      "#il-toast .il-title{font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}",
      "#il-toast .il-sub{font-size:12px;opacity:.7;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}",
      "#il-toast .il-loc{display:flex;gap:10px;flex-shrink:0;}",
      "#il-toast .il-chip{display:flex;flex-direction:column;align-items:center;padding:6px 14px;border-radius:12px;background:rgba(255,255,255,.14);}",
      "#il-toast .il-chip b{font-size:20px;font-weight:700;line-height:1;}",
      "#il-toast .il-chip span{font-size:10px;opacity:.7;margin-top:3px;letter-spacing:.5px;}",
      "#il-toast.ok{background:linear-gradient(135deg,rgba(37,99,235,.96),rgba(29,78,216,.96));}",
      "#il-toast.warn{background:linear-gradient(135deg,rgba(220,38,38,.96),rgba(190,18,60,.96));}"
    ].join("");
    document.head.appendChild(css);

    toastEl = document.createElement("div");
    toastEl.id = "il-toast";
    toastEl.addEventListener("click", function () { toastEl.classList.remove("show"); });
    document.body.appendChild(toastEl);
  }

  var toastTimer;
  function showToast(state, title, sub, box, pos) {
    if (!toastEl) return;
    var hasLoc = (box !== undefined && box !== null && box !== "");
    toastEl.className = state === "ok" ? "ok" : "warn";
    toastEl.innerHTML =
      '<div class="il-ic">' + (state === "ok" ? "📍" : "❓") + '</div>' +
      '<div class="il-main"><div class="il-title">' + esc(title) + '</div>' +
      (sub ? '<div class="il-sub">' + esc(sub) + '</div>' : '') + '</div>' +
      (hasLoc
        ? '<div class="il-loc"><div class="il-chip"><b>' + esc(box) + '</b><span>料盒 BOX</span></div>' +
          '<div class="il-chip"><b>' + esc(pos) + '</b><span>位置 POS</span></div></div>'
        : '');
    void toastEl.offsetWidth;                       // 触发进入动画
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove("show"); },
                            state === "ok" ? 5000 : 3200);
  }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  /* ---------------------------------------------------------------- 表格 & 列识别 */
  var bom = null;   // { table, cols:{top,bottom,designator,model,value,footprint,code,qty} }

  function classifyHeader(text) {
    var t = norm(text).toLowerCase();
    if (!t) return null;
    if (/封装|footprint|package/.test(t)) return "footprint";          // 器件封装
    if (/顶层|top/.test(t)) return "top";                               // 顶层位号
    if (/底层|bottom/.test(t)) return "bottom";                         // 底层位号
    if (/编号|lcsc|supplier\s*part/.test(t)) return "code";            // 器件编号(立创编号)
    if (/型号|comment|mpn|manufacturer\s*part|part\s*no/.test(t)) return "model"; // 器件型号
    if (/位号|designator|reference|refs?\b/.test(t)) return "designator";
    if (/参数|^值$|数值|(^|[^a-z])value([^a-z]|$)/.test(t)) return "value"; // 参数/值
    if (/用量|数量|q'?ty|quantity/.test(t)) return "qty";              // 用量
    return null;
  }

  function detectTable() {
    var tables = document.querySelectorAll("table");
    var best = null, bestScore = 0;
    tables.forEach(function (tb) {
      var ths = tb.querySelectorAll("thead th, thead td");
      if (!ths.length) {
        var firstRow = tb.querySelector("tr");
        ths = firstRow ? firstRow.querySelectorAll("th,td") : [];
      }
      var cols = {}, score = 0;
      [].forEach.call(ths, function (th, i) {
        var c = classifyHeader(th.innerText || th.textContent);
        if (c && !(c in cols)) { cols[c] = i; score++; }
      });
      var rows = tb.querySelectorAll("tbody tr").length;
      if (rows > 0 && score > bestScore) { bestScore = score; best = { table: tb, cols: cols }; }
    });
    if (best && bestScore >= 2 &&
        (("footprint" in best.cols) || ("value" in best.cols) || ("model" in best.cols))) {
      return best;
    }
    return null;
  }

  function cellText(tds, idx) {
    return (idx != null && tds[idx]) ? norm(tds[idx].innerText || tds[idx].textContent) : "";
  }

  function readRow(tr) {
    if (!bom) return null;
    var tds = tr.children, c = bom.cols;
    var desig = [cellText(tds, c.designator), cellText(tds, c.top), cellText(tds, c.bottom)]
      .map(cleanDesig).filter(Boolean).join(" ");
    var info = {
      designators: desig,
      model: cleanText(cellText(tds, c.model)),
      value: cleanText(cellText(tds, c.value)),
      footprint: cleanText(cellText(tds, c.footprint))
    };
    if (!info.model && !info.value && !info.footprint) return null;
    // 过滤掉表头行（读到的是列名本身）
    if (info.model === "器件型号" || info.value === "参数" || info.footprint === "器件封装") return null;
    return info;
  }

  /* ---------------------------------------------------------------- 选中 → 查询 → 提示 */
  var lastKey = "", lastTime = 0;

  function handleSelection(info, source) {
    if (!info) return;
    var key = [info.model, info.value, info.footprint, info.designators].join("|");
    var now = Date.now();
    if (key === lastKey && (now - lastTime) < 900) return;   // 去重
    lastKey = key; lastTime = now;
    log("选中(", source, "):", info);

    var params = new URLSearchParams();
    if (info.model) params.append("part_number", info.model);
    if (info.value) params.append("parameter", info.value);
    if (info.footprint) params.append("footprint", info.footprint);
    var hasAny = false; params.forEach(function () { hasAny = true; });
    if (!hasAny) return;

    var label = info.model || info.value || info.footprint;
    var sub = [info.designators, (info.value && info.value !== label) ? info.value : "",
               info.footprint].filter(Boolean).join(" · ");

    fetch(LIGHTUP_URL + "?" + params.toString())
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.status === "success") {
          var box = data.location.box_id;
          var pos = data.location.pos_id != null ? data.location.pos_id : data.location.led_id;
          notify("ok", label, sub, box, pos,
                 { found: true, name: label, param: info.value, footprint: info.footprint,
                   designators: info.designators, box_id: box, pos_id: pos,
                   matched: data.matched_part_number });
        } else {
          notify("warn", "未找到货位: " + label, sub, null, null,
                 { found: false, name: label, param: info.value,
                   footprint: info.footprint, designators: info.designators });
        }
      })
      .catch(function (err) { log("查询失败:", err); });
  }

  // 嵌入 SPA(iframe) 时只通知父页面弹一个提示框；独立打开 BOM 时才自己弹，避免重复
  function notify(state, title, sub, box, pos, payload) {
    if (inIframe) postToParent(payload);
    else showToast(state, title, sub, box, pos);
  }

  function postToParent(payload) {
    if (!inIframe) return;
    try {
      payload.source = "il-bom-bridge";
      payload.type = "selection";
      window.parent.postMessage(payload, "*");
    } catch (e) {}
  }

  /* ---------------------------------------------------------------- 三条捕获通路 */
  // A) 表格行点击（捕获阶段，避免被内部 stopPropagation 吞掉）
  function onClickCapture(e) {
    if (!bom) return;
    var tr = e.target.closest && e.target.closest("tr");
    if (!tr || !bom.table.contains(tr) || tr.closest("thead")) return;
    setTimeout(function () { handleSelection(readRow(tr), "行点击"); }, 0);
  }

  // B) 高亮变化观察（覆盖 3D/画布点选 → 行高亮）
  var highlighted = new Set();
  function rowBg(tr) {
    var bg = tr.style && tr.style.backgroundColor;
    if (bg) return bg;
    try { return getComputedStyle(tr).backgroundColor; } catch (e) { return ""; }
  }
  function isBlank(bg) { return !bg || bg === "transparent" || /rgba\(0,\s*0,\s*0,\s*0\)/.test(bg); }

  function scanHighlight() {
    if (!bom) return;
    var rows = bom.table.querySelectorAll("tbody tr");
    var freq = {}, bgs = [];
    [].forEach.call(rows, function (tr) {
      var bg = rowBg(tr); bgs.push(bg); freq[bg] = (freq[bg] || 0) + 1;
    });
    // 出现最多的背景色视为「默认」；少数派且非透明视为「高亮/选中」
    var defaultBg = null, maxN = -1;
    Object.keys(freq).forEach(function (k) { if (freq[k] > maxN) { maxN = freq[k]; defaultBg = k; } });
    var nowSet = new Set();
    [].forEach.call(rows, function (tr, i) {
      var bg = bgs[i];
      var isHi = !isBlank(bg) && bg !== defaultBg && freq[bg] <= Math.max(3, rows.length * 0.2);
      if (isHi) {
        nowSet.add(tr);
        if (!highlighted.has(tr)) {              // 新出现的高亮行 = 本次选中
          setTimeout(function () { handleSelection(readRow(tr), "高亮/画布"); }, 0);
        }
      }
    });
    highlighted = nowSet;
  }
  var scanScheduled = false;
  function scheduleScan() {
    if (scanScheduled) return;
    scanScheduled = true;
    setTimeout(function () { scanScheduled = false; scanHighlight(); }, 60);
  }

  // C) console.log 钩子（立创BOM 行点击原生打印：器件编号/器件型号/值）
  function hookConsole() {
    var orig = console.log;
    console.log = function (msg) {
      orig.apply(console, arguments);
      if (typeof msg === "string" && (msg.indexOf("器件型号") >= 0 || msg.indexOf("器件编号") >= 0)) {
        var model = pick(msg, /器件型号[:：]\s*([^,，]+)/);
        var value = pick(msg, /值[:：]\s*([^,，)]+)/);
        var fp = pick(msg, /封装[:：]\s*([^,，)]+)/);
        if (!fp && bom) {                       // DOM 补全封装：取当前高亮行
          var hi = bom.table.querySelector("tbody tr[style]");
          var r = hi && readRow(hi);
          if (r && r.footprint) fp = r.footprint;
        }
        handleSelection({ model: cleanText(model), value: cleanText(value),
                          footprint: cleanText(fp), designators: "" }, "console");
      }
    };
  }
  function pick(s, re) { var m = s.match(re); return m ? m[1].replace(/undefined/g, "") : ""; }

  /* ---------------------------------------------------------------- 启动 */
  function bindTable() {
    var found = detectTable();
    if (found) {
      bom = found;
      log("已识别 BOM 表格，列映射:", bom.cols);
      if (DEBUG) window.__ilDebug = { bom: bom, readRow: readRow, handle: handleSelection };
      var tbody = bom.table.querySelector("tbody") || bom.table;
      var mo = new MutationObserver(scheduleScan);
      mo.observe(tbody, { attributes: true, attributeFilter: ["style", "class"],
                          subtree: true, childList: true });
      return true;
    }
    return false;
  }

  function start() {
    injectUI();
    hookConsole();
    document.addEventListener("click", onClickCapture, true);
    var tries = 0;
    var t = setInterval(function () {
      tries++;
      if (bindTable() || tries > 100) clearInterval(t);
    }, 200);
    log("桥接脚本已加载 (iframe=" + inIframe + ")");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
