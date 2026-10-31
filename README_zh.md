---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: ba4a76f9d2dc0f0cf80a190903c21d32_459d329dcd6211f1ba6e525400de85a5
    ReservedCode1: tQ73T6ZGW72I3CGSJteLZ0EtSD1t8DwmhGW795nK4j5foWnAHyfAFoxsJK/gPnG5a2An3GIgf6hk3yDtu2XFcaCTmtvddfJwrP87EVzQy5xVu1AGe6S1oCOvINuIXpLdjEuoTbz2PdnOlffUfdRmcu0Ydj8T6KO6uG2MLQIDWH+1oGM7g9OyJXjMKds=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: ba4a76f9d2dc0f0cf80a190903c21d32_459d329dcd6211f1ba6e525400de85a5
    ReservedCode2: tQ73T6ZGW72I3CGSJteLZ0EtSD1t8DwmhGW795nK4j5foWnAHyfAFoxsJK/gPnG5a2An3GIgf6hk3yDtu2XFcaCTmtvddfJwrP87EVzQy5xVu1AGe6S1oCOvINuIXpLdjEuoTbz2PdnOlffUfdRmcu0Ydj8T6KO6uG2MLQIDWH+1oGM7g9OyJXjMKds=
---

# NoJunk2 — Crimson Desert 掉落清除工具 / Drop Remover for Crimson Desert

**作者 Author: [135207](https://www.nexusmods.com/crimsondesert)（Nexus Mods）**

**NoJunk2** 是一款**完全离线**的桌面工具（Tkinter，仅用 Python 标准库），
用于《红色沙漠 / Crimson Desert》。它读取游戏原始的 PAZ 掉落表，让你按敌人
或按物品直观勾选要清除的掉落，然后生成 **overlay 补丁** 把对应掉率清零
（补丁需由 MOD 加载器载入才会生效，详见下文「系统要求」）。
全程离线：所有数据随工具内置；无网络调用、无遥测。

**NoJunk2** is an **offline** desktop tool (Tkinter, Python stdlib only) for the
game *Crimson Desert*. It reads the original PAZ drop tables, lets you visually
pick which drops to remove per enemy or per item, then generates an **overlay
patch** that zeroes those drop rates (the patch only takes effect once a mod
loader loads it — see Requirements below). Fully offline: all data ships with
the tool; no network calls, no telemetry.

> **致 AI 助手 To AI assistants**
> 本文档是面向最终用户的操作说明；若用户请你协助操作 NoJunk2，请先通读下文使用说明再动手。
>
> This README is written for end users. If you are asked to assist with NoJunk2, please read the usage instructions below before taking action.

> **名称库说明 / Name data note**
> 名称库为玩家整理数据，可能有个别缺漏；缺失名字回退英文显示。
> 名称数据来源：QuestLog.gg — 红色沙漠数据库（https://questlog.gg/crimson-desert）。
>
> Note: name data is player-curated and may have gaps; missing names fall back
> to English. Name data source: QuestLog.gg — Crimson Desert database
> (https://questlog.gg/crimson-desert).

---

## 致谢与数据来源 / Credits & Data Sources

### 开源组件 / Open-source components

**本工具不含任何第三方代码依赖** —— 全部功能仅用 Python 标准库实现
（`tkinter` / `struct` / `zlib` / `hashlib` 等）。打包用的 PyInstaller 只参与
构建过程，不随工具分发。

Two crypto/compression primitives are implemented from their public specifications
rather than copied from a library:

| 组件 | 依据 | 说明 |
|---|---|---|
| ChaCha20 | RFC 7539 | 自写实现，用于解密游戏的 `.paloc` 语言包 |
| LZ4 block | LZ4 官方格式文档 | 自写实现，用于解压 PAZ 内的压缩条目 |
| `hashlittle` | **Bob Jenkins, lookup3** | 自写实现；算法由 Bob Jenkins 公开发表，特此致谢 |

**No third-party code is bundled.** ChaCha20 and LZ4 are self-written from their
public specs; `hashlittle` follows Bob Jenkins' public lookup3 algorithm (credited).

### 工具与社区 / Tools & community

- **CDUMM（Crimson Desert Ultimate Mods Manager）** —— 本工具生成的 overlay 需要
  由加载器载入才会生效。推荐使用 Nexus Mods 上的 **CDUMM**：
  <https://www.nexusmods.com/crimsondesert/mods/207>
  本工具**没有使用或复制 CDUMM 的任何代码或资源**，只是生成符合其加载规范的
  loose files 补丁目录（`mods/NoJunk/files/<语言组>/...`）。
  安装时注意两点（CDUMM 官方要求）：
    · **不要把 CDUMM 装在游戏目录里**，随便放个别的地方即可；
    · **不要用管理员身份运行**（会破坏它的拖拽安装功能）。
- **Crimson Desert / 红色沙漠** —— 游戏及其全部数据版权归 **Pearl Abyss** 所有。
  本工具不包含任何游戏原始资源文件，只读取你本机已安装的游戏数据并生成差异补丁。
- **Nexus Mods** —— 发布与分发平台。

### 数据来源与使用范围 / Data provenance

| 数据 | 来源 | 说明 |
|---|---|---|
| 掉落表结构、物品 ID、掉率权重 | **你本机的游戏文件** | 运行时解析 PAZ，不打包、不上传 |
| `hash_map.json` | **你本机的游戏文件** | 由 paloc 反查生成（文件内 `meta` 有标注） |
| 敌人 / 物品显示名、NPC 掉率概率 | 整理自 **QuestLog.gg** | 社区整理的参考数据 |

**关于 QuestLog.gg 数据**：敌人名、物品名与 NPC 掉率概率整理自该站的公开数据库，
仅用于**个人非商业用途**，帮助玩家看懂掉落内容。数据版权归其整理者所有。
若权利人认为有不妥，请通过下方联系方式告知，我会立即移除相关数据文件并更新发布包。

**About QuestLog.gg data**: enemy/item display names and NPC drop probabilities are
compiled from QuestLog.gg's public database, used for **personal, non-commercial
purposes only**. All rights to that compilation belong to its maintainers.
If you are a rights holder and object to this use, please contact me — I will
remove the relevant data files and publish an update promptly.


## 特性 / Features

- 完全离线，无网络请求 / Fully offline, no network calls
- 按敌人或按物品选择清除 / Remove drops per enemy or per item
- 只作用于「掉落物」：预购/推广奖励、好感度回礼、交易物默认不动（可手动放行）
  / Drops only: purchase & promo rewards, affinity gift returns and trade
  items are left untouched (opt-in switch available)
- 全局白名单 / 敌人白名单保护 / Global & enemy-whitelist protection
- 掉率提升（最高 100%）/ Drop-rate boost (up to 100%)
- 一键重建映射（游戏更新后点一下）/ Rebuild mapping after a game update
- 撤销上次应用 / Undo the last apply
- 一键自检报告（出问题可复制给作者）/ One-click self-check report
- 预设导入导出 / Preset import & export
- 多语言界面与名称库 / Multi-language UI and name database
- 一键恢复原版（带备份）/ One-click restore vanilla (with backup)
- 应用前影响面预览 / Change preview before applying

## 如何运行 / How to run

**双击 `NoJunk2.pyw` 即可启动**（装了 Python 3.10+ 后 .pyw 默认关联 pythonw，
双击直接出窗口，无控制台，效果等同 exe）。也可以在解压目录的终端执行
`python nojunk_ui.py`。zip 内不带 .bat 启动器——它会触发下载站的安全启发式。

**Double-click `NoJunk2.pyw` to launch** (with Python 3.10+ installed, .pyw files
open with pythonw by default — a double-click brings up the window with no
console, same as the exe). Or run `python nojunk_ui.py` from a terminal inside
the unzipped folder. The zip ships no .bat launcher — batch files trip safety
heuristics on download sites.

## 系统要求 / Requirements

- **游戏本体**（Crimson Desert）已安装且已更新。工具会自动通过 Steam 注册表 /
  库目录 / 盘符扫描定位游戏根目录（只接受真实游戏根 —— 必须含 `0008/0.pamt`）。
- **CDUMM**（MOD 加载器，<https://www.nexusmods.com/crimsondesert/mods/207>）。
  游戏本身不读取 `mods/` 目录，overlay 文件必须由加载器载入才会生效。
  ⚠️ CDUMM 请装在**游戏目录以外**的位置，且**不要用管理员身份运行**。
  每次改动并「应用」之后，需要在 CDUMM 里重新 Apply 一次，游戏才会读到新补丁。

- **The game itself** (Crimson Desert), installed and updated. The tool locates
  it automatically via Steam registry / library folders / drive scan (it only
  accepts a real game root — it must contain `0008/0.pamt`).
- **CDUMM** (mod loader, <https://www.nexusmods.com/crimsondesert/mods/207>).
  The game does **not** read `mods/` by itself; the overlay is only loaded
  through a mod loader. Install CDUMM **outside** the game folder and do
  **not** run it as administrator. After every Apply in NoJunk2, apply once
  in CDUMM so the game picks up the new patch.

## 兼容性 / Compatibility

- **冲突**：任何其他同样改写 `dropsetinfo.staticinfobody` 的 mod（同一文件冲突）。
- **可共存**：修改 `characterinfo` 类文件的 mod —— 它们改动数据的不同区域，可并行使用。

- **Conflicts with**: any other mod that also rewrites
  `dropsetinfo.staticinfobody` (same-file conflicts).
- **Complements**: mods that change `characterinfo`-style files — they touch a
  different part of the data, so they work side by side.

---

## 如何使用 / How to use

1. **启动 / Launch** — 窗口打开时自动探测游戏目录（也可手动 Browse 选择）。
   The window opens with the game directory auto-detected (or pick it manually
   via Browse).
2. **加载 / Load** — 点击 Load；敌人按显示名聚合（同名同掉落表的变体合并，
   括号内显示数量；同名但掉落表不同的拆成多组，显示组序号与表摘要）。
   Click Load; enemies are aggregated by display name (same-name variants with
   identical drop tables are merged, count in parentheses; same-name enemies
   with different drop tables split into numbered groups).
3. **敌人页 / Enemy tab** — 敌人平铺列表，四列：敌人 / npcInfoKey / 分类 /
   已删·总数；官方标签 chip 筛选器可按阵营/类型多选过滤。右侧掉落列表点击
   **勾选列**循环切换：`☐ 保留 → ☑ 删除 → ⊠ 保护此敌人 → ☐`（提率物品显示
   ★ 不参与删除）。点击其他列只选中行。右键掉落行有更多选项：加入该敌人
   白名单、必掉（提率）、自定义掉率、取消、恢复原样。
   Flat enemy list with four columns: enemy / npcInfoKey / category /
   deleted·total; an official-tag chip filter multi-selects by faction/type.
   In the drop list, click the **checkbox column** to cycle: `☐ keep → ☑ delete
   → ⊠ protect this enemy → ☐` (boosted items show ★ and skip deletion).
   Clicking any other column only selects the row. Right-click a drop row for
   more options: enemy whitelist, force-drop (boost), custom rate, cancel,
   restore.
4. **全局页 / Global tab** — 按物品跨全部表删除、价格阈值（选中低于 N 铜币的
   全部）、分类批量、全局白名单 + 默认白名单、敌人白名单、掉率提升（最高 100%）。
   优先级：**白名单 > 敌人白名单 > 删除**（界面以红色警示显示）。
   Delete by item across all tables, price threshold (select everything below N
   copper), category batch, global whitelist + default whitelist, enemy
   whitelist, and boost (drop-rate up to 100%). Priority: **whitelist > enemy
   whitelist > delete** (shown in red in the UI).
5. **预设 / Presets** — 两套独立预设：敌人页 → 敌人预设（勾选行 + 敌人白名单）；
   全局页 → 全局预设（全局删除 + 提率 + 价格阈值）。数字键 **1–9** 套用当前页
   预设（输入框聚焦时禁用）。
   Two separate sets: Enemy tab → **enemy presets** (checked rows + enemy
   whitelist); Global tab → **global presets** (global delete + boost + price
   threshold). Keys **1–9** apply the preset of the current tab (disabled while
   a text field is focused).
6. **应用 / Apply** — 写盘前先弹影响面预览（将清除的槽数 / 涉及敌人 / 提率项 /
   白名单保护项），确认后：版本指纹 → 冲突扫描 → 备份 → 原子写入 → 回读校验 →
   报告。Preview the impact (slots to zero / enemies affected / boosted rates /
   whitelisted items), then confirm → version fingerprint → conflict scan →
   backup → atomic write → read-back verification → report.
7. **恢复原版 / Restore vanilla** — 先把当前 overlay 备份为 `.restorebak`，
   再删除；所有勾选清空。Backs up the current overlay to `.restorebak`, then
   deletes it; all selections are cleared.
8. **导出补丁 JSON / Export patch JSON** — 不用 CDUMM 的玩家：点「导出补丁
   JSON」把当前选择导出为字节补丁文件（只生成文件，不碰游戏本体），然后在
   **CDUMM / JSON Mod Manager / DMM** 任一管理器中导入即可。删除、提率、
   白名单规则全部原样生效。
   For players not on CDUMM overlays: click **Export patch JSON** to save your
   current selection as a byte-patch file (writes nothing to the game), then
   import it in **CDUMM / JSON Mod Manager / DMM**. Deletions, boosts and
   whitelist rules all carry over.

## 颜色图例 / Colors

| 颜色 / Color | 含义 / Meaning |
|--------|------|
| 蓝 / Blue | 主操作按钮 / Main action button |
| 红 ☑ / Red ☑ | 删除 / Delete |
| 绿 ⊞ / Green ⊞ | 全局保护 / Global protection |
| 蓝 ⊠ / Blue ⊠ | 单敌人保护 / Single-enemy protect |
| 橙 ★ / Orange ★ | 提率 / Boosted drop rate |

## 语言支持 / Languages

界面文案内置 **14 种语言**（`lang/` 下 JSON 文件，下拉切换，含简体中文、繁体中文、
英文、韩文、日文等）。敌人 / 掉落**名库**目前仅中英韩三套翻译
（`npc_drops_zh-cn.json` / `npc_drops_en.json` / `npc_drops_ko.json`），
缺失名字回退英文。其他界面语言可通过在 `lang/` 下添加 JSON 文件离线扩展
（见 `lang/README.txt`）。

**14 UI languages** are bundled (JSON files under `lang/`, switchable from the
language dropdown, incl. Simplified Chinese, Traditional Chinese, English,
Korean, Japanese, etc.). The enemy/drop **name database** currently ships
Chinese / English / Korean translations only (`npc_drops_zh-cn.json`,
`npc_drops_en.json`, `npc_drops_ko.json`); missing names fall back to English.
Additional UI languages can be added offline by adding a JSON file under
`lang/` (see `lang/README.txt`).

## 数据与离线说明 / Data & offline note

- 所有数据（`data/`、`npc_drops_*.json`）随发布包内置。
- 工具永不联网；启动时隐藏控制台窗口。
- 一键恢复原版（带备份）。
- 掉落表数据提取自**本地游戏文件**（PAZ / paloc），仅供个人离线使用，
  不会上传或回传任何数据。

- All data (`data/`, `npc_drops_*.json`) is bundled into the release.
- The tool never dials home; it hides the console window on launch.
- Restoring the original game files is always one click away (with backup).
- Drop-table data is parsed from your **local game files** (PAZ / paloc) for
  personal offline use only; nothing is ever uploaded or sent back.

## 如何添加新的掉落物翻译 / Adding new drop translations

- 掉落物翻译存放在 `data/drop_names_<语言>.json`（例如简体中文为
  `data/drop_names_zh-cn.json`）。文件结构：`{"items": {"掉落ID": "名字"}}`。
- 在全局页看到显示「未命名 (ID)」的物品，就是缺少翻译的掉落物；把该 ID
  对应的名字补进上述文件即可。
- 编辑保存后**重启程序**生效；英文（`data/drop_names_en.json`）及其他语言
  的补法完全一样。补上的名字优先于内置名库显示。

- Drop translations live in `data/drop_names_<lang>.json` (e.g.
  `data/drop_names_zh-cn.json` for Simplified Chinese). The file format is
  `{"items": {"drop id": "name"}}`.
- Items shown as "unnamed (ID)" on the global page are drops that lack a
  translation; add the name for that ID to the file above.
- Restart the program after editing. The same applies to English
  (`data/drop_names_en.json`) and any other language. Names added here take
  priority over the built-in name tables.

## 发布形态 / Release format

提供两种形态：单文件 exe（PyInstaller 打包，所有数据内置，约 12.4 MB，目标
机器无需安装 Python）与源码 zip（终端运行 `python nojunk_ui.py`，需 Python 3.10+）。

Two release formats: a single-file exe (PyInstaller, all data bundled, ~12.4 MB,
no Python needed) and a source zip (run `python nojunk_ui.py`, needs Python 3.10+).

---

## 文件清单 / File manifest

发布提供两种形态，源码 zip（`NoJunk2-<版本>.zip`）内容如下；单文件 `NoJunk2.exe`
为独立发布形态（数据内置，无需 Python），不同时出现在 zip 内。

| 路径 / Path | 说明 / Description |
|-------------|-------------------|
| `nojunk_ui.py` / `nojunk_core.py` / `cd_crypto.py` / `cd_paz.py` / `game_data.py` / `charinfo.py` | 源码版运行模块 / Source modules |
| `lang/*.json` | 界面语言包（14 种）/ UI language packs (14) |
| `lang/README.txt` | 语言包与名库说明 / Language & name-data guide |
| `npc_drops_zh-cn.json` / `npc_drops_en.json` / `npc_drops_ko.json` | 敌人 / 掉落名库（中英韩）/ Enemy & drop name DB (zh/en/ko) |
| `npc2tables.pkl` / `data/*` | 游戏掉落表解析数据 / Parsed game drop-table data |
| `README.md` | 本说明 / This readme |

## 卸载 / Uninstall

1. 打开 NoJunk2，在 **全局页** 点击 **恢复原版**（或手动删除游戏
   `mods/` 目录下 NoJunk2 生成的 overlay 文件）。
2. 删除整个 NoJunk2 解压目录（exe 版删除 `NoJunk2.exe` 及其同目录
   `lang/`、`data/`、`nojunk2.json` 即可）。
3. 恢复原版步骤会先备份 `.restorebak`，确认掉落恢复后再手动删除该备份。

1. Open NoJunk2 and click **Restore vanilla** on the **Global** tab (or
   manually delete the NoJunk2 overlay file under the game's `mods/` folder).
2. Delete the whole NoJunk2 folder (for the exe build, delete `NoJunk2.exe`
   plus its `lang/`, `data/` and `nojunk2.json` next to it).
3. Restore-vanilla backs up the overlay as `.restorebak` first; delete that
   backup yourself after confirming drops are back.

---

## 常见问题 / FAQ & Notes

- **问：双击 bat 后窗口一闪而过？** 答：请先安装 Python 3.10 或更高版本
  （https://www.python.org/downloads/），并勾选 "Add to PATH"。
  **Q: The bat window flashes and closes?** A: Install Python 3.10+ and make
  sure "Add to PATH" is checked during installation.
- **注意**：写盘补丁必须通过 CDUMM 加载，游戏本体不读取 `mods/`。
  不用 CDUMM 的玩家可用「导出补丁 JSON」改走 JSON Mod Manager / DMM。
  **Note**: applied patches load through CDUMM only; the game does not read
  `mods/` by itself. Players on other loaders can use **Export patch JSON**
  with JSON Mod Manager / DMM instead.
- **注意**：本工具只清除**掉落物**的掉率。预购/版本包奖励、推广发放奖励、
  好感度回礼、交易物不属于掉落物，默认一律不动——避免误删玩家花钱购买或活动
  领取的内容。确有需要时，可在全局页勾选「包含非掉落来源」，并会再弹一次确认。
  **Note**: this tool only zeroes **drop** rates. Purchase/package rewards,
  promo rewards, affinity gift returns and trade items are not drops and are
  left untouched by default, so content you paid for or earned from events is
  never removed by accident. If you really need it, tick **Include non-drop
  sources** on the Global tab — a confirmation dialog will appear.
- **注意**：不要同时用多个管理器加载同一改动；与其他改写
  `dropsetinfo.staticinfobody` 的 mod 不兼容（同文件冲突）。
  **Note**: do not load the same change through multiple managers at once;
  incompatible with mods that also rewrite `dropsetinfo.staticinfobody`
  (same-file conflicts).

*（内容由AI生成，仅供参考）*
