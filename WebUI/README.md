# 个人元件库 · WebUI

一个「实体元件库」的管理与交互系统：为每个元件登记它在料盒中的物理货位
（`box_id` = 哪个料盒，`pos_id` = 料盒内第几个位置），并能：

- 维护元件库（增删改查、快速改位、位置交换、数值/范围/多字段搜索、智能排序）
- 从 **立创商城** 抓取元件的完整参数（型号、品牌、类目、封装、全部规格参数、数据手册、库存）
- 解析 **立创EDA 导出的离线交互式 BOM**，在 BOM 表格里点选元件、或在 2D/3D 视图里
  选中元件时，自动查出它的货位并在浏览器里**弹出提示框显示 `box_id` 与 `pos_id`**

## 运行

```bash
pip install -r requirements.txt      # 仅需 Flask + flask-cors
python app.py                        # 或双击 run_app.bat
```

浏览器打开 <http://127.0.0.1:5000/>

> 关键字搜索立创商城需要本机装有 **Node.js**（用于自动破解立创的反爬 JS 挑战）。
> 没有 Node 时其余功能不受影响，可改用「粘贴立创编号/链接」抓取参数。

## 文件结构（重构后）

| 文件 | 作用 |
|------|------|
| `app.py` | **统一后端**（Flask）。元件库 CRUD、智能点灯搜索 `/lightup`、范围搜索、立创抓取、BOM 服务与上传。合并了旧的三个脚本，去除了 bs4/requests 依赖。 |
| `index.html` | **管理界面 SPA**（现代浅色）。元件库管理 + 内嵌 BOM 交互 + 全局定位提示框。 |
| `bom_bridge.js` | 注入到交互式 BOM 的**桥接脚本**。捕获「行点击 / 3D画布点选」，查询货位并弹出提示框。 |
| `lcsc.py` | **立创商城参数抓取**（标准库 urllib）。 |
| `lcsc_challenge.js` | Node 脚本：在沙箱里执行立创搜索页的反爬 JS，算出放行 cookie。 |
| `components.json` | 元件库数据（`{ 名称: { box_id, led_id, parameter, voltage, footprint, note, ... } }`）。 |
| `InteractiveBOM_v7.html` | 当前使用的立创EDA 离线交互式 BOM（可在界面「导入 BOM」替换）。 |

> 注：`led_id` 即界面上的「位置 pos_id」，字段名保留以兼容既有数据与固件。

### 立创抓取说明

支持 **关键字(型号) / 立创编号 `Cxxxxx` / 商品ID / 商品链接** 四种输入：

- **关键字**：立创搜索页有一层反爬 JS Cookie 挑战，`lcsc.py` 会调用
  `lcsc_challenge.js`（Node）在沙箱执行该 JS 拿到 cookie，再取搜索结果里
  **最匹配的第一项**，然后抓其详情页的全部参数。cookie 会在进程内缓存复用。
- **立创编号 / ID / 链接**：直接抓详情页（服务端渲染，最稳定）。交互式 BOM
  中的元件本身就带立创编号。

若本机没有 Node，关键字搜索会自动降级并提示改用编号/链接。

### 排障

`bom_bridge.js` 顶部的 `DEBUG` 置为 `true` 后，浏览器控制台会打印 `[IL-Bridge]` 详细日志，
并暴露 `window.__ilDebug`，用于确认 BOM 表格识别与列映射是否正确（换用不同 BOM 导出时有用）。

## 旧文件（已被 `app.py` 取代，保留作备份）

`DataUI.py`、`DanymicBomServer.py`、`InputDataset.py`、`InputWebUI.html`、`LCSCCopy.py`
