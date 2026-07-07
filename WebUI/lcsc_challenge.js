/* lcsc_challenge.js — 解立创搜索页的反爬 JS Cookie 挑战
 * 用法: node lcsc_challenge.js <challenge.html 文件路径>
 * 输出: 一行 cookie "name=value"（失败则空输出）
 *
 * 原理: 挑战页含两段 <script>：第一段定义 _xvasu/_xvtsc/_xvpfs/_xvpts，
 * 第二段是混淆代码，计算 document.cookie 后 location.reload()。
 * 这里在 vm 沙箱里 mock document/window/btoa，执行后捕获它写入的 cookie。
 */
const vm = require("vm");
const fs = require("fs");

function main() {
  const file = process.argv[2];
  if (!file) return "";
  let html;
  try { html = fs.readFileSync(file, "utf8"); } catch (e) { return ""; }

  const scripts = [];
  const re = /<script\b[^>]*>([\s\S]*?)<\/script>/gi;
  let m;
  while ((m = re.exec(html))) if (m[1].trim()) scripts.push(m[1]);
  if (!scripts.length) return "";

  let cookie = "";
  const sandbox = {
    btoa: (s) => Buffer.from(s, "binary").toString("base64"),
    atob: (s) => Buffer.from(s, "base64").toString("binary"),
    Date, RegExp, String, Math, parseInt, parseFloat, Array, Object, JSON,
    console: { log() {}, error() {}, warn() {}, info() {} },
  };
  sandbox.document = {
    _c: "",
    set cookie(v) { cookie = v; this._c = v; },
    get cookie() { return this._c; },
  };
  sandbox.window = { location: { reload() {}, href: "", replace() {}, assign() {} } };
  sandbox.window.document = sandbox.document;
  sandbox.self = sandbox.window;
  sandbox.globalThis = sandbox;

  vm.createContext(sandbox);
  for (const s of scripts) {
    try { vm.runInContext(s, sandbox, { timeout: 4000 }); }
    catch (e) { /* 自我保护代码可能抛错；cookie 可能已写入 */ }
    if (cookie) break;
  }
  return (cookie || "").split(";")[0].trim();
}

process.stdout.write(main());
