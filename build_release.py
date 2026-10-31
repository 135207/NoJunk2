# -*- coding: utf-8 -*-
"""build_release.py —— 一键生成发布 zip（主程序 / 语言包 / npc_drops / 数据 / README）。

零联网，仅用标准库；输出到 dist/NoJunk2-<VERSION>.zip。
用法：python build_release.py
"""
import os, sys, glob, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nojunk_core as core   # 读取 VERSION（发行模块，不联网）

RELEASE_MODS = ('nojunk_ui.py', 'nojunk_core.py', 'cd_crypto.py',
                'cd_paz.py', 'game_data.py', 'charinfo.py',
                # 「一键重建映射」按钮会 import 它 —— 漏掉的话源码包用户
                # 点按钮就 ImportError（与 spec 的 hiddenimports 同一个坑，
                # 两个产物都要补）。
                'build_npc2tables_local.py',
                # 重写后新增的两个模块：nojunk_core 会 import 它们，漏了即崩。
                # 这也是「加新模块要同时改 spec 与本清单」这条规则的由来。
                'dropset.py', 'plan.py',
                'NoJunk2.pyw')   # 双击即启动的一键入口（.pyw 不触发下载站启发式）
README = 'README.md'
# 面向不懂代码用户的 DMM 挂载教程，随源码包一起发布。
README_DMM = 'README_DMM挂载教程.md'

# 发布白名单：data/ 下**运行时真正会读**的文件。
# 要加新数据文件，就在这里加一行；不在名单里的一律不发布。
DATA_PUBLISH = (
    'hash_map.json',
    'item_cats.json',
    'itemcats.json',
    'npc_infokey.json',
    'npc_realclass.json',
    'prices.json',
    'questlog_prob.json',
    'drop_names_zh-cn.json',
)

# 允许留在 data/ 但**不发布**的文件（开发期用得到，运行时不需要）。
# 与「未知的野文件」区分开：这些是登记过的，体检时直接跳过。
DATA_DEV_ONLY = (
    'e2e_baseline.json',     # _gate_e2e.py 的回归基线
)

# 出包前的体检：文件名/内容命中这些特征的，一律拒绝发布。
# 这是"脱裤子放屁"的第二道闸 —— 就算哪天白名单写漏了，也发不出去。
# 2026-10-30 事故：glob('data/*.json') 把测试基线和废弃数据一起打包，
# 直到翻 zip 目录才被发现。人记不住，就让脚本记住。
FORBIDDEN_PAT = (
    '_gate',            # 门禁脚本
    'baseline',         # 回归基线（测试用）
    'probe',            # 逆向探针
    'verify',           # 校验脚本
    '_baseline_',       # 备份目录
    '.pkl.tmp',         # 写盘中间产物
    'rebuild_report',   # 重建报告（开发期输出）
    'nojunk2_selfcheck',  # 自检报告（用户本地生成，不该回流）
    '_审查', '审查报告',  # 历史审查文档
)

BAT_NAME = 'NoJunk 启动.bat'
_tmp_bat = None


def _launch_bat():
    """生成通用启动 bat（GBK 编码，%~dp0 相对路径），返回临时文件路径。

    用 `where py` / `where python` 探测解释器再运行 nojunk_ui.py；
    都找不到时提示安装 Python 3.10+ 并 pause，避免双击后窗口一闪而过。
    """
    text = ('@echo off\r\n'
            'rem NoJunk2 启动器 - 双击运行\r\n'
            'cd /d "%~dp0"\r\n'
            'where py >nul 2>nul\r\n'
            'if %errorlevel%==0 (\r\n'
            '    py nojunk_ui.py\r\n'
            '    goto :done\r\n'
            ')\r\n'
            'where python >nul 2>nul\r\n'
            'if %errorlevel%==0 (\r\n'
            '    python nojunk_ui.py\r\n'
            '    goto :done\r\n'
            ')\r\n'
            'echo [NoJunk2] 未找到 Python，请先安装 Python 3.10 或更高版本：\r\n'
            'echo     https://www.python.org/downloads/\r\n'
            '\r\n'
            ':done\r\n'
            'echo.\r\n'
            'echo [NoJunk2] 程序已退出，按任意键关闭...\r\n'
            'pause\r\n')
    import tempfile
    fd, path = tempfile.mkstemp(suffix='.bat', prefix='nojunk_launch_')
    with os.fdopen(fd, 'w', encoding='gbk', newline='') as f:
        f.write(text)
    return path


def collect():
    """收集发布文件：发行模块 + 运行时数据（H 节清单，存在才带）。"""
    files = []
    for name in RELEASE_MODS:
        fp = os.path.join(HERE, name)
        if not os.path.exists(fp):
            raise SystemExit('缺少发行模块: %s' % name)
        files.append(fp)
    if not os.path.exists(os.path.join(HERE, README)):
        raise SystemExit('缺少 %s' % README)
    files.append(os.path.join(HERE, README))
    if not os.path.exists(os.path.join(HERE, README_DMM)):
        raise SystemExit('缺少 %s' % README_DMM)
    files.append(os.path.join(HERE, README_DMM))
    files += sorted(glob.glob(os.path.join(HERE, 'lang', '*.json')))
    lang_readme = os.path.join(HERE, 'lang', 'README.txt')
    if os.path.exists(lang_readme):
        files.append(lang_readme)
    # 只收程序实际加载的三套名库（zh-cn/en/ko）；npc_drops_zh.json 与 zh-cn 完全
    # 相同且语言码表中不存在 'zh'，永不加载，排除以免发布包冗余。
    files += sorted(p for p in glob.glob(os.path.join(HERE, 'npc_drops_*.json'))
                    if not os.path.basename(p) == 'npc_drops_zh.json')
    # npcs_all.json 不再发布（2026-10-30）：
    #   · 它没有任何 .py 引用，纯冗余；
    #   · 且是外站接口的原始 JSON（含 icon: /assets/_sprites/... 与 createdAt），
    #     带着网站字段一起分发没必要，也不干净。
    for name in ('npc2tables.pkl', 'slots.pkl'):
        fp = os.path.join(HERE, name)
        if os.path.exists(fp):
            files.append(fp)
    # data/ 下的数据文件：**运行时真正会读的那些才发布**。
    # 2026-10-30 教训：以前这里写 glob('data/*.json') 一把抓，
    # 结果测试基线 e2e_baseline.json（48KB，只有 _gate_e2e.py 用）、
    # 以及三个废弃的 names_*.json / alignment*.json 全跟着发出去了。
    # 现在改成白名单：新增数据文件必须显式登记，不再"放进去就自动发布"。
    for name in DATA_PUBLISH:
        fp = os.path.join(HERE, 'data', name)
        if os.path.exists(fp):
            files.append(fp)
    files += sorted(glob.glob(os.path.join(HERE, 'data', '*.pkl')))
    # 三审（2026-10-28 晚）：zip 顶置一个「双击哪个」醒目说明，免得用户
    # 看到 40+ 个文件不知道该点哪个。文件名带星号会在多数资源管理器里
    # 排到最前面，配合文件名本身就是答案（英文优先）。
    hint = os.path.join(HERE, '★★★Double-click NoJunk2.pyw to open★★★.txt')
    if os.path.exists(hint):
        files.insert(0, hint)
    else:
        raise SystemExit('缺少发布说明: %s' % os.path.basename(hint))
    # 启动入口：通用 bat（%~dp0 相对路径，GBK），打包后由 main() 清理临时文件
    # ⚠️ 1.0.1 起不再放进 zip：.bat 属 Nexus「潜在恶意文件类型」启发式，
    #    是 1.0.0 zip 被隔离的头号嫌疑；README 已覆盖 python 启动方式。
    global _tmp_bat
    _tmp_bat = None
    return files


def audit(files):
    """出包前体检：把不该发布的文件挡在 zip 外面。

    两道检查：
      ① 文件名命中 FORBIDDEN_PAT → 拒绝
      ② data/ 下的 json 不在白名单 → 拒绝（防止"塞进去就自动发布"）

    任何一条不过就直接退出，不生成 zip。宁可打不出包，也不要发出脏包。
    """
    bad = []
    for fp in files:
        base = os.path.basename(fp)
        rel = os.path.relpath(fp, HERE).replace('\\', '/')
        low = base.lower()
        for pat in FORBIDDEN_PAT:
            if pat.lower() in low:
                bad.append((rel, '文件名命中禁发特征 "%s"' % pat))
                break
        else:
            if rel.startswith('data/') and rel.endswith('.json'):
                if base not in DATA_PUBLISH:
                    bad.append((rel, '不在 DATA_PUBLISH 白名单里'))
    # 再扫一遍 data/ 目录，把"存在、不在白名单、也不是登记过的开发期文件"
    # 的野文件报出来。DATA_DEV_ONLY 里的跳过 —— 它们留在这儿是正常的。
    for p in sorted(glob.glob(os.path.join(HERE, 'data', '*.json'))):
        base = os.path.basename(p)
        if base in DATA_PUBLISH or base in DATA_DEV_ONLY:
            continue
        r = 'data/' + base
        if not any(r == b[0] for b in bad):
            bad.append((r, 'data/ 下的野文件（既不在白名单，也没登记为开发期文件）'))
    if bad:
        print('[FAIL] 发布体检未通过，已中止。以下文件不该进包：')
        for rel, why in bad:
            print('   ✗ %-42s %s' % (rel, why))
        print()
        print('  · 若确实要发布该文件 → 加进 build_release.py 的 DATA_PUBLISH')
        print('  · 若不该发布 → 把它从 data/ 挪走（测试数据放 temp/ 或 _baseline_*/）')
        raise SystemExit(1)
    print('[OK] 发布体检通过：%d 个文件，无测试/开发期产物' % len(files))


def main():
    ver = core.VERSION
    out_dir = os.path.join(HERE, 'dist')
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, 'NoJunk2-%s.zip' % ver)
    files = collect()
    audit(files)          # 体检不过直接退出，不生成 zip
    total = 0
    try:
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            for fp in files:
                if fp == _tmp_bat:
                    arc = BAT_NAME
                else:
                    arc = os.path.relpath(fp, HERE)
                z.write(fp, arc)
                total += os.path.getsize(fp)
    finally:
        if _tmp_bat and os.path.exists(_tmp_bat):
            os.remove(_tmp_bat)   # 临时 bat 不入库，打包后即清，不污染项目目录
    n_lang = len(glob.glob(os.path.join(HERE, 'lang', '*.json')))
    n_npc = len([p for p in glob.glob(os.path.join(HERE, 'npc_drops_*.json'))
                 if not os.path.basename(p) == 'npc_drops_zh.json'])
    n_data = len(glob.glob(os.path.join(HERE, 'data', '*.json'))
                + glob.glob(os.path.join(HERE, 'data', '*.pkl')))
    print('[OK] 发布包已生成: %s' % out)
    print('     文件数: %d  原始大小合计: %.1f KB' % (len(files), total / 1024.0))
    print('     发行模块 %d 个 / 语言包 %d 个 / npc_drops %d 个 / data %d 个 / README'
          % (len(RELEASE_MODS), n_lang, n_npc, n_data))


if __name__ == '__main__':
    main()
