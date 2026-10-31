NoJunk2 Language directory guide / NoJunk2 语言目录说明
=====================================================

[English]
This folder holds UI language packs (JSON files). Each file is one UI
language, e.g. zh-cn.json (Simplified Chinese), en.json (English),
zh-tw.json (Traditional Chinese).

1. Directory layout
-------------------
- <lang>.json                UI texts (buttons, hints, dialogs; ~173 keys).
                             To add a language: copy en.json, change the
                             values, and name the file with the language code.
- npc_drops_<lang>.json       Enemy & drop name database (bundled with the
                             release). Format follows npc_drops_en.json:
                             {"<enemy id>": {"name": ..., "drops": [{"id","name",...}]}}.
- data/drop_names_<lang>.json
                             Supplemental drop-name translations (you can
                             maintain this yourself). Format:
                             {"items": {"<drop id>": "name"}}.
                             Items shown as "unnamed (ID)" on the Global tab
                             lack a translation; add the name for that ID,
                             then restart the program. Names added here take
                             priority over the built-in name database. The
                             same applies to English and other languages.

2. The two name databases
--------------------------
- npc_drops_*.json: the built-in, complete drop name database (ships with the
  release; wide coverage).
- drop_names_*.json: external supplemental translation tables (distributed
  with user files; edit and it takes effect; highest priority).

Display names are resolved in this order:
    1) data/drop_names_<lang>.json  (supplement, highest priority)
    2) npc_drops_<lang>.json  drop name database
    3) hash_map / game paloc name database fallback
    4) if none: show "unnamed (ID)"

3. How to add translations
---------------------------
1. Open data/drop_names_<language code>.json (create it if missing, following
   the format above);
2. Add a line under "items":  "drop id": "name";
3. Save and restart NoJunk2 to see the new name.

----------------------------------------------------

[中文]
本目录存放界面语言包（JSON 文件），每个文件对应一种界面语言，
如 zh-cn.json（简体中文）、en.json（英文）、zh-tw.json（繁体中文）。

一、目录结构
------------
- <lang>.json                界面文案（按钮、提示、对话框等，约 173 个键）。
                             新增语言 = 复制 en.json 改键值，文件名用语言码。
- npc_drops_<lang>.json      敌人掉落名库（随发布包内置），格式见 npc_drops_en.json，
                             结构 {"<敌人id>": {"name": ..., "drops": [{"id","name",...}]}}。
- data/drop_names_<lang>.json
                             掉落物补充翻译表（玩家可自行维护），结构
                             {"items": {"<掉落id>": "名字"}}。
                             GUI 全局页显示「未命名 (ID)」的物品即缺翻译项，
                             把 ID 对应的名字补进该文件、重启程序即可生效。
                             补上的名字优先于内置名库显示；en 及其他语言同理。

二、两种名库的分工
------------------
- npc_drops_*.json：内置的完整掉落名库（随发布包分发，覆盖面大）。
- drop_names_*.json：外置补充翻译表（随用户文件分发，编辑即改、优先级最高）。

界面显示名字时按以下顺序取：
    1) data/drop_names_<lang>.json（补充翻译表，最高优先）
    2) npc_drops_<lang>.json 掉落名库
    3) hash_map / 游戏 paloc 名库兜底
    4) 都没有时显示「未命名 (ID)」

三、添加翻译的方法
------------------
1. 打开 data/drop_names_<语言码>.json（没有就新建一个，参照上面格式）；
2. 在 "items" 里加一行  "掉落ID": "中文名字" ；
3. 保存后重启 NoJunk2 即可看到新名字。

（内容由AI生成，仅供参考）
（内容由AI生成，仅供参考）
