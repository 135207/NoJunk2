# -*- coding: utf-8 -*-
"""nojunk_core.py —— NoJunk 重做版 · 核（无 GUI，可独立测试）

只放与界面无关的东西：
  资源目录 / 配置 / 语言包 / 自动定位游戏 / 数据装载 / 写盘（_apply 的全部逻辑）
GUI 全部在 nojunk_ui.py，两边靠 AppState 通信。

设计原则（重做的原因）：
  - 每个功能一个方法，方法名即意图，不搞一行里塞八个操作
  - 所有 bind / 快捷键在 nojunk_ui.py 的 BIND_TABLE 里集中登记（防丢）
  - 写盘逻辑已审查修复（C1-C5）；GUI 全部在 nojunk_ui.py，两边靠 AppState 通信
"""
import os, sys, json, re, struct, time, shutil, pickle, glob, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import game_data
import cd_paz

LANG_DIR = os.path.join(HERE, 'lang')
DATA_DIR = os.path.join(HERE, 'data')
CFG_NAME = 'nojunk2.json'

VERSION = '1.0.3'          # 适配游戏 1.0.0.3392/3424 + Drops_ 族排除

# 配置**结构**版本。与产品 VERSION 分开记：VERSION 每次发布都可能动，
# 但配置结构不一定变；反过来结构变了也必须能被发现。
# 背景（ 用户实测）：1.0.1 写出的 nojunk2.json / _game_cache.pkl
# 被 1.0.2 读到后，工具不做任何事也不报错 —— MOD 静默失效。
# 这类「旧版本文件让新版本悄悄不干活」必须变成**看得见的提示**。
CFG_SCHEMA = 1

# 0008/0.paz 基准大小（A-2 版本指纹）。
# ⚠️ 这是**兜底值**，主判据已改为动态指纹（见 _paz_base_expected）：
#    加载时记下实际大小，写盘前再读一次比对 —— 游戏再更新也不会硬编码失效。
#    只有拿不到加载记录（例如从缓存恢复）时才退回这个常量。
#    更新记录：2026-10-30 10:32 版 = 640115248；同日 18:31 = 640151152；
#    2026-10-31 01:18 新版（buildid 26329202）= 640148640。
#    ⚠️ 一天内变了两次 —— 这正是不该依赖硬编码的原因，改这个值只是兜底。
PAZ_BASE = 640148640
# 版本指纹的**精确判据**不是 1.paz 大小而是 dropsetinfo 条目级指纹：
#   dropsetinfo 实际位于 0008\1.paz（0008/0.pamt 中 paz_index=1）。
#   但 1.paz 是 912MB 大包，官方更新动包内无关资源也会改大小——
#   若以「1.paz 大小」做判据会把「掉落表没动」的更新误报成游戏已变。
#   因此写盘前重读 0.pamt 比对 dropsetinfo 条目的
#   (paz_index, offset, comp_size, orig_size) 四元组（见 _ds_fingerprint_ok）。
OVERLAY_REL = os.path.join('mods', 'NoJunk', 'files', '0008', 'gamedata',
                           'dropsetinfo.staticinfobody')
# 备份目录（files\ 外，1.0.3 新增）：
# 实证：加载器日志仅识别 loose 组 "NoJunk/files/0008"（VFS 扫描只覆盖
# mods/<mod>/files/**），旧位 files\0008\gamedata\ 下的 .bak-* 全部被
# loose 扫描聚合进 nppsa 包造成包膨胀（pamt_target_cache 中 8 个
# dir|0008|gamedata/*.bak-* 键 targets=null 即占位证据）。backup 放
# files 之外不会被聚合。备份命名规则不变：<basename>.bak-<时间戳>[-序号]。
BACKUP_DIR_REL = os.path.join('mods', 'NoJunk', 'backup')
# MOD 自己的目录与自述文件（给加载器显示名字/版本用，格式与 mods/ 下其他
# MOD 一致：modinfo 包一层）。
MOD_DIR_REL = os.path.join('mods', 'NoJunk')
MOD_INFO_REL = os.path.join('mods', 'NoJunk', 'mod.json')
NEXUS_URL = 'https://www.nexusmods.com/crimsondesert'
# 提率目标满权重 W 与解算 _boost_plan 的**唯一定义**在 plan.py（计划层）。
# 这里不再重复定义 —— 需要请 `import plan` 用 plan.W / plan._boost_plan。

LANG_NAME_DEFAULT = {
    'zh-cn': '简体中文', 'zh-tw': '繁體中文', 'en': 'English', 'ja': '日本語',
    'ko': '한국어', 'de': 'Deutsch', 'fr': 'Français', 'es': 'Español',
    'es-mx': 'Español (MX)', 'ru': 'Русский', 'it': 'Italiano', 'pl': 'Polski',
    'tr': 'Türkçe', 'pt-br': 'Português (BR)',
}

OS_LANG_MAP = {
    'zh_CN': 'zh-cn', 'zh_SG': 'zh-cn', 'zh_HK': 'zh-tw', 'zh_TW': 'zh-tw',
    'ja_JP': 'ja', 'ko_KR': 'ko', 'de_DE': 'de', 'fr_FR': 'fr', 'es_ES': 'es',
    'es_MX': 'es-mx', 'ru_RU': 'ru', 'it_IT': 'it', 'pl_PL': 'pl', 'tr_TR': 'tr',
    'pt_BR': 'pt-br',
}


# ── 资源目录（打包后要能找到 lang/ data/）────────────────────────

def res_dir():
    """打包成 exe 后 __file__ 指向临时目录 —— 所以：
    exe 同目录有 lang/ 就用它（用户可自行加语言包），否则用 _MEIPASS，最后源码目录。"""
    base = HERE
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        if os.path.isdir(os.path.join(exe_dir, 'lang')):
            return exe_dir
        if hasattr(sys, '_MEIPASS'):
            return sys._MEIPASS
        return exe_dir
    return base


def user_dir():
    """配置目录：必须持久（不能放每次变的临时目录）。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return HERE


def npc2tables_path():
    """NPC→掉率表 映射的读取位置：用户目录优先，打包内置兜底。

    ⚠️ 不能只读 res_dir()：打包后 res_dir() 指向 _MEIPASS（临时解压目录，
    实测 C:\\Users\\...\\Temp\\_MEIxxx），
      · 在那儿写 = 关掉程序就蒸发
      · 只读它 = 用户「一键重建」的结果永远不会被读到
    所以读取顺序是：用户目录有就用（用户重建过），否则用内置那份。
    """
    up = os.path.join(user_dir(), 'npc2tables.pkl')
    if os.path.exists(up):
        return up
    return os.path.join(res_dir(), 'npc2tables.pkl')




class _SafeUnpickler(pickle.Unpickler):
    """ ：pkl 在用户可写目录，恶意 pkl = 任意代码执行。
    本工具的 pkl 只含 dict/list/tuple/str/int/float/bool/None，白名单。"""
    _ALLOW = {'builtins': {'set', 'frozenset', 'dict', 'list', 'tuple',
                           'str', 'int', 'float', 'bool', 'bytes'}}

    def find_class(self, module, name):
        # ：默认值原来是 {}（dict）——dict 没有 issuperset，
        # 任何非 builtins 模块都会抛 AttributeError 而不是预期的
        # UnpicklingError。安全结果一样（都被拒），但异常类型不对，
        # 一旦有人只 except UnpicklingError 就会漏。改成空 frozenset。
        if self._ALLOW.get(module, frozenset()).issuperset({name}):
            return getattr(__import__(module), name)
        raise pickle.UnpicklingError('forbidden class %s.%s' % (module, name))


def _safe_load_pkl(fp):
    with open(fp, 'rb') as f:
        return _SafeUnpickler(f).load()


# ── 问题日志（nojunk2.log，配置目录旁；超 1MB 截头留尾） ──────────────
LOG_MAX = 1024 * 1024
_log_lock = __import__('threading').Lock()


def log(msg):
    """落一条问题日志。任何地方出错/关键动作都该喊一声——出问题先读日志。"""
    _log_lock.acquire()
    try:
        fp = os.path.join(user_dir(), 'nojunk2.log')
        line = '[%s] %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
        with open(fp, 'a', encoding='utf-8') as f:
            f.write(line)
        try:
            if os.path.getsize(fp) > LOG_MAX:
                with open(fp, 'rb') as f:
                    f.seek(-LOG_MAX // 2, 2)
                    tail = f.read()
                # 01 修复：tail 开头可能切在多字节 UTF-8 字符中间，
                # 用 errors='ignore' 解码再编码对齐边界
                tail = tail.decode('utf-8', errors='ignore').encode('utf-8')
                with open(fp, 'wb') as f:
                    f.write(b'... [truncated] ...\n' + tail)
        except Exception:
            pass
    except Exception:
        pass  # 日志自己绝不能把主流程搞死
    finally:
        _log_lock.release()


def log_exc(where, ex):
    log('!! %s: %s: %s' % (where, type(ex).__name__, ex))


def install_excepthook():
    """未捕获异常也进日志（打包版没控制台，这是唯一的黑匣子）。"""
    def hook(tp, val, tb):
        import traceback
        log('!! 未捕获异常:\n' + ''.join(traceback.format_exception(tp, val, tb)))
    sys.excepthook = hook
    try:
        import threading
        def thook(args):
            import traceback
            # ：原文是 '%s://n%s'，'//n' 是笔误（想写换行 \n），
            # 日志里异常堆栈会糊成一行没法看。
            log('!! 线程异常 %s:\n%s' % (getattr(args.thread, 'name', '?'),
                ''.join(traceback.format_exception(
                    args.exc_type, args.exc_value, args.exc_traceback))))
        threading.excepthook = thook
    except Exception:
        pass


def load_langs():
    """扫 lang/*.json -> {code: {'name':.., 'str': {..}}}。
    目录走 res_dir()（exe 旁挂 lang/ 可覆盖内置），找不到再回退源码目录。"""
    out = {}

    def put(code, obj):
        out[code] = {
            'name': obj.get('_name') or LANG_NAME_DEFAULT.get(code, code),
            'str': {k: v for k, v in obj.items() if not k.startswith('_')},
        }

    lang_dir = os.path.join(res_dir(), 'lang')
    if not os.path.isdir(lang_dir):
        lang_dir = LANG_DIR
    for p in sorted(glob.glob(os.path.join(lang_dir, '*.json'))):
        code = os.path.splitext(os.path.basename(p))[0]
        try:
            # ：原来裸 open 不关句柄（打包后 Python 的 GC 回收
            # 时机不确定，Windows 上语言包文件会被长期占用，用户想替换/修改
            # lang/*.json 时可能被拒绝）
            with open(p, encoding='utf-8') as f:
                put(code, json.load(f))
        except Exception:
            pass
    if not out:
        put('en', {'_name': 'English'})
    return out


def detect_os_lang():
    """按 Windows 系统界面语言猜语言码；认不出就 en（N 网主力零操作）。"""
    try:
        import ctypes, locale
        lid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        tag = locale.windows_locale.get(lid)
        if tag and tag in OS_LANG_MAP:
            return OS_LANG_MAP[tag]
        prim = (tag or '').split('_')[0].lower()
        for code in LANG_NAME_DEFAULT:
            if code.split('-')[0] == prim:
                return code
    except Exception:
        pass
    return 'en'


def auto_find_game():
    """自动定位游戏本体目录（铁律 7：只认本体特征 `0008/0.pamt`，不认 mods/工具目录）。"""
    import string
    cands = []

    def add(path):
        if path and path not in cands:
            cands.append(path)

    steam_bases = []
    try:
        import winreg
        for root, sub in ((winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam'),
                          (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Valve\Steam'),
                          (winreg.HKEY_CURRENT_USER, r'SOFTWARE\Valve\Steam')):
            try:
                k = winreg.OpenKey(root, sub)
                steam_bases.append(winreg.QueryValueEx(k, 'InstallPath')[0])
            except Exception:
                pass
    except Exception:
        pass
    steam_bases += [r'C:\Program Files (x86)\Steam', r'C:\Program Files\Steam']
    for base in steam_bases:
        vdf = os.path.join(base, 'steamapps', 'libraryfolders.vdf')
        if os.path.exists(vdf):
            try:
                txt = open(vdf, encoding='utf-8', errors='replace').read()
                for m in re.finditer(r'"path"\s+"([^"]+)"', txt):
                    add(os.path.join(m.group(1).replace('\\\\', '\\'),
                                     'steamapps', 'common', 'Crimson Desert'))
            except Exception:
                pass
        add(os.path.join(base, 'steamapps', 'common', 'Crimson Desert'))
    for drv in string.ascii_uppercase:
        for stem in ('SteamLibrary', 'Steam', 'SteamGames'):
            add('%s:\\%s\\steamapps\\common\\Crimson Desert' % (drv, stem))
            add('%s:\\Games\\%s\\steamapps\\common\\Crimson Desert' % (drv, stem))
    for c in cands:
        try:
            if os.path.isdir(c) and os.path.exists(os.path.join(c, '0008', '0.pamt')):
                return c
        except Exception:
            continue
    return ''


# ── 状态（GUI 只读写这个对象，不自己猜）────────────────────────

def write_mod_info(game_dir):
    """在 mods/NoJunk/ 下写 mod.json（给加载器显示名字和版本）。

    为什么要有它：以前这个文件是手工放的，写着 version 1.0.0，
    实际早就 1.0.2 了 —— 加载器里看到的是错的版本号，
    排查"到底装了哪一版"时非常误导。现在每次应用都顺手写一次。

    ⚠️ 用户可能自己改过 title/description，所以只**更新**工具负责的
    字段（version / nexus_url），已有的其他字段一律保留。
    """
    try:
        d = os.path.join(game_dir or '', MOD_DIR_REL)
        if not os.path.isdir(d):
            return
        fp = os.path.join(d, 'mod.json')
        info = {}
        try:
            with open(fp, encoding='utf-8') as f:
                old = json.load(f)
            if isinstance(old.get('modinfo'), dict):
                info = dict(old['modinfo'])
        except Exception:
            info = {}
        info.setdefault('title', 'NoJunk2')
        info.setdefault('name', 'NoJunk')
        info.setdefault('author', '135207')
        info.setdefault('description',
                        'Removes junk loot from enemies. 清除敌人掉落的垃圾物品。')
        info['version'] = VERSION          # ← 这个每次都要跟着工具走
        info['nexus_url'] = NEXUS_URL
        tmp = fp + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'modinfo': info}, f, ensure_ascii=False, indent=2)
            f.write('\n')
        os.replace(tmp, fp)
    except Exception:
        pass


def _backup_dir(game_dir):
    """备份目录绝对路径（mods/NoJunk/backup，files 目录外），确保存在。

    1.0.3：备份从 files 内移出，避免被 cdloader loose 扫描
    聚合进 nppsa 聚合包（见 BACKUP_DIR_REL 注释的实证）。"""
    d = os.path.join(game_dir or '', BACKUP_DIR_REL)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _backup_name(game_dir, ov):
    """在备份目录下生成与 overlay 同名的 .bak-<时间戳> 路径（撞名加序号）。

    语义与旧版一致：同秒两次写盘不覆盖上一个恢复点。"""
    d = _backup_dir(game_dir)
    base = os.path.basename(ov)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    bak = os.path.join(d, '%s.bak-%s' % (base, stamp))
    n = 1
    while os.path.exists(bak):
        n += 1
        bak = os.path.join(d, '%s.bak-%s-%d' % (base, stamp, n))
    return bak


def _prune_backups(game_dir, ov, keep=3):
    """备份上限：保留最近 keep 份，超出删最旧（P2-2：旧版无限堆积，
    实测用户目录 28 份 71MB，且摆在 mod 加载器扫描目录里）。
    1.0.3：备份统一在 backup 目录，keep 由 5 → 3。"""
    try:
        d = _backup_dir(game_dir)
        base = os.path.basename(ov)
        baks = sorted((f for f in os.listdir(d)
                       if f.startswith(base + '.bak-')),
                      key=lambda f: os.path.getmtime(os.path.join(d, f)))
        for f in baks[:-keep]:
            os.remove(os.path.join(d, f))
    except Exception:
        pass


# 奖励类掉落表：清掉 = 玩家损失自己花钱买来的 / 活动领来的内容。
# ⚠️ 保护粒度是**槽**（表+物品），不是物品本身——
#    同一个物品常常既从怪物掉落、又在奖励表里（实测 385 个奖励物品在界面里
#    出现 6,602 次）。按物品保护会让用户连正常的怪物掉落也删不掉，
#    那就把工具的主要功能废了。按槽保护：怪物掉的那个槽照删，奖励槽不动。
# 不含 DropSet_*_FavoriteItem_Reward（好感度回礼）——那是 NPC 的喜好物、
# 不是玩家买的内容，语义不同，另行评估（见审查报告）。
# 作用域规则已抽到 dropset.py（规则表化，便于将来加/改规则并自动出解释）。
# 这里保留同名函数只是**转发**，语义必须与 dropset 完全一致——
# 历史上"同一个判断抄两份"正是静默 bug 的主要来源，绝不再抄第二份。
from dropset import is_nondrop_table    # noqa: E402


# 非「掉落物」来源的表。边界是这样划的：
# 本 MOD 改的是掉落物；购买奖励、推广奖励、交易、好感度回礼都不是掉落物，
# 改它们就是越界。
# ⚠️ 反面教训（我第一版做错过）：不要用「是否被敌人引用（npc2tables）」
#    来划边界——那样会把 DropSet_Chest_* / BoxBarrel_* / Pot_* 这些
#    **砸箱子开木桶掉的**也算成越界，实测要砍掉 78.5% 的槽，等于废掉
#    工具主要功能。宝箱木桶陶罐掉出来的同样是掉落物，必须留在作用域内。
#    正确边界是「按来源性质排除少数几类」，不是「按敌人引用只留少数几类」。
_REWARD_PREFIXES = ('Purchase_Reward', 'Drops_Reward',
                    'Bilibili_Drops_Reward', 'Drop_Trade_')
_NONDROP_SUFFIX = ('_FavoriteItem_Reward', '_FavoriteContribution_Reward')


def _clean_npc2tables(raw):
    """npc2tables.pkl 结构净化（，）。

    _SafeUnpickler 只管「能加载哪些类」，不管加载出来的东西长什么样。
    而这个文件就躺在 exe 旁边（可写目录），实测三种脏形态：
      · tables 元素为 int      → _table_sig 里 t.startswith() 崩 AttributeError
      · tables 元素为 str      → 不崩，但 t[0] 取到首字符 'D'，sig 静默变成垃圾
                                 → gid 全变 → 用户存的勾选/白名单全部静默失配
      · 值不是 dict            → 'str' object has no attribute 'get'
    静默错比崩更坏（崩了用戶知道，错了不知道）。这里统一成
    {str(npc_id): {..., 'tables': [(str, ...), ...]}}。
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    for nid, rec in raw.items():
        if not isinstance(rec, dict):
            continue
        tabs = rec.get('tables')
        clean = []
        if isinstance(tabs, (list, tuple)):
            for t in tabs:
                if isinstance(t, str):
                    clean.append((t, None, 0, False))     # 裸表名，宽容收下
                elif isinstance(t, (list, tuple)) and t and isinstance(t[0], str):
                    clean.append(tuple(t))
        rec2 = dict(rec)
        rec2['tables'] = clean
        # 键统一成 str：qlog 的键是 str（JSON 对象键必为 str），
        # pkl 若存了 int 键则所有 .get(nid) 恒落空、敌人全部「暂无对应表」
        out[str(nid)] = rec2
    return out


def _clean_dict_of_dict(d):
    """固化 JSON（item_cats / npc_infokey / questlog_prob）形状净化。

    ：顶层是 dict 还不够，值也必须能当 dict 用 ——
    _main_cats 直接 v.get('main')，值若是个字符串就 AttributeError 崩穿启动；
    _group_by_name 直接 ci.get('main') 同理。脏值整体丢弃（安全降级），
    比带着半份坏数据跑要好定位。
    """
    if not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items() if isinstance(v, dict)}


def _preset_shape_ok(side, val):
    """九轮 P1：预设内层结构校验。enemy: {'checked': {gid: [k..]}, 'enemy_wl': {...}}
    global: {'global_del': [..], 'boost': {k: float}, 'price_max': number}。
    只验类型不验内容（键的有效性由 _gid_targets/规范化承担）。"""
    if not isinstance(val, dict):
        return False
    if side == 'enemy':
        for field in ('checked', 'enemy_wl'):
            v = val.get(field, {})
            if not isinstance(v, dict):
                return False
            for _g, ks in v.items():
                if not isinstance(ks, (list, tuple)):
                    return False
                # 十轮 P1-1：元素也必须是 str——list(list) 过了外层校验，
                # 套用时 list 当字典键 TypeError 崩穿 Tk 回调（实测复现）
                if not all(isinstance(x, str) for x in ks):
                    return False
        return True
    # global
    gd = val.get('global_del', [])
    if not isinstance(gd, (list, tuple)):
        return False
    if not all(isinstance(x, str) for x in gd):
        return False
    bst = val.get('boost', {})
    if not isinstance(bst, dict):
        return False
    if not all(isinstance(k, str) for k in bst):
        return False
    pm = val.get('price_max', -1)
    if not isinstance(pm, (int, float)):
        return False
    import math as _math
    if isinstance(pm, float) and not _math.isfinite(pm):
        return False                    # 十：预设里的 nan/inf 价格上限拒收
    return True


class AppState:
    """全部可变状态集中在这里，GUI 只管显示和收集意图。"""

    # 类级默认值（与 GameData 同一套路）：
    # 多处用 __new__ + 手工塞属性的方式构造实例（门禁 / 测试 / 导出都这么干），
    # 那些实例不走 __init__。类级属性存在后，getattr 会直接命中它而**不会**
    # 触发继承链上的 __getattr__ ——否则 getattr(..., default) 的兜底会被
    # mock 返回 None 吞掉（实测 _gate_ui.py 的 _StubState 就是这么失效的）。
    # ⚠️ 这三个是类级可变对象：只允许整体赋值，禁止原地 .append()/.update()。
    _boost_infeasible = []
    _boost_infeasible_reason = {}
    # blockers 的语义（顺序不代表优先级）见 apply_changes 里透传处的注释
    _boost_infeasible_blockers = {}
    # 上一次 apply 成功时的选择快照，供 undo_last_apply 一并还原
    # （只还原磁盘不还原勾选，界面会和真实状态对不上）。
    _undo_sel_snapshot = None

    def __init__(self):
        self.lang = 'zh-cn'
        self.langs = {}
        self.cfg = {}
        self.cfg_path = os.path.join(user_dir(), CFG_NAME)
        self.game_dir = ''
        self.gd = None                 # game_data.GameData
        self.stale_overlay = None      # 加载时体检到的「过期 overlay」信息（见 check_stale_overlay）
        self.qlog = {}                 # npc_drops_<lang>.json
        self.enemies = []              # 聚合后的敌人列表
        self.item_slots = {}           # item_key -> [(table, off, rates)]
        self.npc2tables = {}           # npc_id -> {'tables': [...]}
        # 用户选择
        self.checked = {}              # (enemy_name, item_key) -> bool
        self.gdel = set()              # 全局删除
        self.gwl = set()               # 全局白名单
        self.ewl = {}                  # enemy_name -> [item_key] 该敌人白名单
        self.boost = {}                # item_key -> 概率 0~1
        self.price_max = -1            # 价格阈值
        # 奖励类掉落表（预购/版本/推广/交易）默认保护；越界的非击杀来源同理。
        # 用户显式放行（include_nondrop）后才动，且界面会二次确认。
        self.include_nondrop = False
        # 界面临时状态
        self.chip = set()            # 官方标签多选筛选（敌人页）
        self.sub_filter = 'all'      # 官方子类筛选（全局页）
        self.gsort = ('n', False)
        self.cur_tab = 0
        self.fallback_fixed = 0
        # 官方固化数据（构建期 data/*.json，运行时只读，零联网）
        self.realclass = {}          # npc_id -> {'kind','sub'} 官方分类（load_game 时填充）
        self.item_cats = {}          # item_key -> {'main','sub'} 官方物品分类
        self.npc_infokey = {}        # npc_id -> npcInfoKey 官方子类型
        self.questlog_prob = {}      # npc_id -> {item_key: {'prob','minQ','maxQ'}}
        self.static_loaded = False   # 固化数据加载标记
        self.load_cfg()

    # ── 配置 ────────────────────────────────────────────────

    def load_cfg(self):
        try:
            self.cfg = json.load(open(self.cfg_path, encoding='utf-8'))
        except Exception:
            self.cfg = {}
        if not isinstance(self.cfg, dict):
            self.cfg = {}
        # 十轮 P1-2：顶层字段类型脏不能炸启动（手改配置/老版本/坏导入都可达，
        # 实测 6 种脏法崩 5 种）。逐字段 isinstance 守卫，脏字段按空处理。
        cfg = self.cfg

        # ── 跨版本配置防护（ 用户实测事故后加）───────────────
        # ① 结构版本戳：不是本次构建认识的结构 → 记下来，交给界面提示，
        #    不静默沿用（静默沿用正是「1.0.1 配置让 1.0.2 失效」的形状）。
        try:
            got = int(cfg.get('cfg_ver') or 0)
        except (TypeError, ValueError):
            got = 0
        self.cfg_schema_from = got
        if got and got != CFG_SCHEMA:
            log('配置结构版本不符: 文件=%d 本版=%d（将按当前结构解释，'
                '如发现勾选失效请重新勾选）' % (got, CFG_SCHEMA))
        # ② 版本放行开关必须是「单次会话」的。旧版会把它写进配置，
        #    下次启动就永久生效 —— 等于把「我知道版本不对，强行写」这个
        #    一次性的决定变成了永久状态，之后任何版本错位都被静默放行，
        #    或者反过来用一个过期基准把写盘永远挡住。这里一律清掉。
        if cfg.pop('paz_base_override', None) is not None:
            log('已清除配置中残留的 paz_base_override（该开关只允许单次会话生效）')

        def _list(key):
            v = cfg.get(key)
            return v if isinstance(v, (list, tuple)) else []

        def _dict(key):
            v = cfg.get(key)
            return v if isinstance(v, dict) else {}

        # 恢复会话状态：_save_sel 会把这些写进 nojunk2.json，
        # 重启后必须原样取回，否则用户上次的删除/提率/白名单全部丢失。
        self.gdel = set(str(x) for x in _list('global_del'))
        # 出厂不预置白名单/保护：默认安装的 nojunk2.json 不含 whitelist/enemy_wl，
        # 因此这里读取的只会是用户自己保存的选择（重启持久化，不丢）
        self.gwl = set(str(x) for x in _list('whitelist'))
        self.ewl = {str(k): [str(x) for x in v] for k, v in
                    _dict('enemy_wl').items() if isinstance(v, (list, tuple))}        # M3：boost 值规范化——导入预设/手改配置可能带进 0/负数/>1，
        # apply 时 int(W*(1-p)/p) 会除零抛穿 Tk 回调（用户只看到没反应）。
        # 口径与 set_prob 一致：p<=0 → 归入全局删除；p>1 → 截到 1（必掉）。
        raw_boost = _dict('boost')
        self.boost = {}
        for k, v in raw_boost.items():
            try:
                p = float(v)
            except (TypeError, ValueError):
                continue
            import math as _math
            if not _math.isfinite(p):
                continue              # 十：nan/inf 拒绝（int(nan) 会在写盘引爆）
            k = str(k)
            if p <= 0:
                self.gdel.add(k)
            else:
                self.boost[k] = min(p, 1.0)
        # 十轮 P2-3：boost 与白名单互斥（load_cfg/预设导入都可能造出同键），
        # 白名单优先——被保护的物品不写提率
        for k in list(self.boost):
            if k in self.gwl:
                del self.boost[k]
        pm = cfg.get('price_max', -1)
        if not isinstance(pm, (int, float)):
            pm = -1
        import math as _math
        if isinstance(pm, float) and not _math.isfinite(pm):
            pm = -1                     # 十：nan/inf 价格上限拒绝
        self.price_max = pm
        # 十一轮 P1：剩下四个顶层字段同威胁模型（实测：presets_enemy=list →
        # enemy_preset_names AttributeError；lang=list → unhashable；game_dir=
        # dict → os.path.isdir TypeError）。load_cfg 是唯一能截住它们的闸门。
        for key in ('presets_enemy', 'presets_global'):
            if not isinstance(cfg.get(key), dict):
                cfg[key] = {}
        if not isinstance(cfg.get('lang', ''), str):
            cfg.pop('lang', None)
        if not isinstance(cfg.get('game_dir', ''), str):
            cfg.pop('game_dir', None)
        # 十一轮 P2-1：gdel∩boost 在加载时闭环（apply ④ 只清内存不落盘，
        # 重启会复活；这里一次清干净）
        self.gdel -= set(self.boost)
        # 敌人勾选（删除项）持久化：_save_sel 按 {gid: [item_key]} 写入，
        # 重启后原样取回，避免勾选丢失
        self.checked = {}
        for gid, ks in _dict('checked').items():
            if not isinstance(ks, (list, tuple)):
                continue
            for k in ks:
                self.checked[(str(gid), str(k))] = True
        # 十二轮 P1：enemy_wl/checked 就地治愈 cfg——_migrate_legacy_keys 绕过
        # 内存直读原始 cfg，脏值会在 load_game 必崩（实测三变体复现）。
        # 与内存口径一致写回。⚠️ 必须在 self.checked 建好之后执行
        # （十二轮我自己的教训：放前面会把 cfg['checked'] 用空内存清掉，A2 挂）
        cfg['enemy_wl'] = {k: list(v) for k, v in self.ewl.items()}
        healed_checked = {}
        for (g, k), v in self.checked.items():
            if v:
                healed_checked.setdefault(g, []).append(k)
        cfg['checked'] = healed_checked
        return self.cfg

    def save_cfg(self, status_cb=None):
        # 十：原子写——裸 json.dump 写一半崩进程 = 配置无声丢失。
        # 与 _load_parsed_cached 的缓存写同口径（tmp + os.replace）
        try:
            # 结构版本戳：下次启动靠它判断「这份配置是不是本版写的」
            self.cfg['cfg_ver'] = CFG_SCHEMA
            tmp = self.cfg_path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.cfg_path)
            return True
        except Exception as ex:
            # 不能静默 —— 否则勾选不保存、用户还不知道为什么
            if status_cb:
                try:
                    status_cb('配置保存失败（目录只读？）：%s' % ex)
                except Exception:
                    pass
            return False

    def t(self, key):
        """取当前语言的文案；缺键回退英文，再缺回退键名本身。"""
        d = self.langs.get(self.lang, {}).get('str', {})
        if key in d:
            return d[key]
        en = self.langs.get('en', {}).get('str', {})
        return en.get(key, key)

    # ── 数据装载 ────────────────────────────────────────────

    def qlog_path(self):
        p = os.path.join(res_dir(), 'npc_drops_%s.json' % self.lang)
        return p if os.path.exists(p) else os.path.join(res_dir(), 'npc_drops_en.json')

    def load_qlog(self):
        try:
            with open(self.qlog_path(), encoding='utf-8') as _f:
                return json.load(_f)
        except Exception:
            with open(os.path.join(res_dir(), 'npc_drops_en.json'),
                      encoding='utf-8') as _f:
                return json.load(_f)

    def load_game(self, game_dir):
        """装载游戏数据。失败抛异常，由 UI 统一弹错。

        提速（用户体感「每次进入都读好久」）：pamt 解析 + dropsetinfo 槽位扫描
        + paloc 名库扫描 + characterinfo 破解的结果按文件指纹缓存到
        user_dir()/_game_cache.pkl，二次启动直接读缓存（秒级）。
        指纹 = (0.paz 大小, 0008/0.pamt 大小+mtime, 语言组 0.pamt 大小+mtime)，
        游戏更新导致任一变化即自动失效重解析。"""
        self.game_dir = game_dir
        self.qlog = self.load_qlog()
        try:
            gd_new, rc_new = self._load_parsed_cached(game_dir)
        except Exception as ex:
            log_exc('load_game(%s)' % game_dir, ex)
            raise
        #  ：加载竞态——旧版逐字段赋值，中途暴露
        # "新 gd + 空 item_slots"半初始化态（实测 402 次窗口）。改为全部产物
        # 局部构造完再一次赋值，老字段在重解析期间保持旧值（总比混搭安全）。
        # 自审 P1-3（ui-rev）：0 表检查必须在 self.gd 赋值之前——
        # raise 后 self.gd 若已落新值，UI 侧就拿到"0 张表的新 gd + 旧 enemies"
        # 混搭态。先验后赋。
        if not gd_new.tabs:
            raise RuntimeError('dropsetinfo 解析出 0 张掉落表'
                               '（游戏版本可能已更新，与本工具不兼容）')
        self.gd, self.realclass = gd_new, rc_new
        # 动态版本指纹：记下加载时的实际 0.paz 大小 + dropsetinfo 条目四元组
        # （paz_index/offset/comp_size/orig_size），写盘前再读一次比对。
        # 这样"游戏在会话期间更新"能被拦住，且不必把大小硬编码成常量
        # （硬编码每次官方更新都会失效一次）。
        # dropsetinfo 位于 0008\1.paz；不比对 1.paz 大小而比对条目本身——
        # 1.paz 是 912MB 大包，动无关资源也会改大小，条目级指纹才精确
        # （仅当掉落表条目本身变化时才拦截写盘，适配补全）。
        try:
            _p0 = os.path.join(game_dir, '0008', '0.paz')
            self._load_paz_size = os.path.getsize(_p0) if os.path.exists(_p0) else 0
            self._load_ds_entry = self._read_ds_entry(game_dir)
        except Exception:
            self._load_paz_size = 0
            self._load_ds_entry = None
        log('load_game 成功: %s（表 %d 张）' % (game_dir, len(self.gd.tabs)))
        self._tab_gcount = None     # P1-2：敌人/映射变了，共享表计数缓存必须失效
        # item_key -> 所有出现过的槽（全局删除用）
        item_slots_new = {}
        for tname, tab in self.gd.tabs.items():
            for ik, (off, rates) in tab.items():
                item_slots_new.setdefault(ik, []).append((tname, off, rates))
        # ：同表重复槽并入 item_slots（全局删除/提率全清）
        for (tname, ik), dups in self.gd.tab_dups.items():
            for off, rates in dups:
                item_slots_new.setdefault(ik, []).append((tname, off, rates))
        # npc -> 表（构建期产物）
        p = npc2tables_path()
        try:
            npc2tables_new = _clean_npc2tables(
                _safe_load_pkl(p) if os.path.exists(p) else {})
        except Exception as ex:
            log_exc('load_game 加载 npc2tables.pkl', ex)
            npc2tables_new = {}
        # 官方固化数据（构建期生成，发行零联网铁律；缺失则安全降级为空）
        def _load_static_json(name):
            try:
                fp = os.path.join(res_dir(), 'data', name)
                with open(fp, encoding='utf-8') as _f:
                    d = json.load(_f)
                return _clean_dict_of_dict(d)
            except Exception:
                return {}
        self.item_cats = _load_static_json('item_cats.json')
        self.npc_infokey = _load_static_json('npc_infokey.json')
        self.questlog_prob = _load_static_json('questlog_prob.json')
        self.static_loaded = True
        # 最后一次赋值：item_slots / npc2tables / enemies 同步落位
        self.item_slots = item_slots_new
        self.npc2tables = npc2tables_new
        self.enemies = self._group_by_name()
        self._migrate_legacy_keys()
        # 加载完立刻体检磁盘上的旧 overlay —— 不然用户一路点到底都不知道
        # 那个文件是上一版游戏留下的，进游戏只会觉得"这破工具没效果"。
        self.stale_overlay = self.check_stale_overlay()
        return len(self.enemies)

    def check_stale_overlay(self):
        """磁盘上已有的 overlay 是否还对得上当前游戏。

        返回 None = 没问题（或压根没 overlay）；
        否则返回 dict(reason, detail, size, want, backup_n)，给 UI 弹提示用。

        为什么要这么查：游戏更新后旧 overlay 的长度/偏移全部作废，
        但**工具不查它、用户也看不出来**——点了应用像是成功了，进游戏毫无变化。
        这是"看起来有 bug"的头号来源，所以要在加载时就把话说清楚。
        """
        try:
            game = self.game_dir
            if not game or not self.gd:
                return None
            ov = os.path.join(game, OVERLAY_REL)
            if not os.path.exists(ov):
                return None
            size = os.path.getsize(ov)
            want = len(self.gd.ds)
            # 同目录下同前缀的备份也一起数，方便提示里说清"还能回滚"
            d = os.path.dirname(ov)
            base = os.path.basename(ov)
            nbak = 0
            try:
                nbak = len([f for f in os.listdir(d)
                            if f.startswith(base + '.bak-')
                            or f == base + '.restorebak'])
            except Exception:
                pass
            if size != want:
                return {'reason': 'size', 'size': size, 'want': want,
                        'backup_n': nbak,
                        'detail': 'overlay 是 %d 字节，当前游戏是 %d 字节' % (size, want)}
            # 长度对得上也不一定对：游戏可能改了内容而总长不变。
            # 比 4 个"只读字段"（key/min/max 从不被本工具改动），
            # 只要它们和原版一致，就说明这个 overlay 确实是基于这版数据写的。
            try:
                with open(ov, 'rb') as f:
                    data = f.read()
            except Exception as ex:
                return {'reason': 'unreadable', 'detail': str(ex),
                        'size': size, 'want': want, 'backup_n': nbak}
            # 抽样：均匀取最多 200 个槽的位置比对只读字段，避免全量慢
            slots = []
            for t, tab in self.gd.tabs.items():
                for ik, (off, _r) in tab.items():
                    slots.append(off)
            if slots:
                step = max(1, len(slots) // 200)
                bad = 0
                for off in slots[::step]:
                    if off + 58 > min(len(data), want):
                        continue
                    if (data[off + 1:off + 5] != self.gd.ds[off + 1:off + 5]
                            or data[off + 42:off + 58] != self.gd.ds[off + 42:off + 58]):
                        bad += 1
                if bad:
                    return {'reason': 'content', 'size': size, 'want': want,
                            'backup_n': nbak, 'bad_n': bad,
                            'detail': '抽样 %d 个槽里有 %d 个字段对不上'
                                      % (len(slots[::step]), bad)}
            return None
        except Exception:
            return None

    def _load_parsed_cached(self, game_dir):
        """带缓存的解析：返回 (GameData, realclass)。

        缓存键取文件指纹；任何一步失败都静默回退到全量解析（缓存只是提速，
        绝不能成为故障源）。缓存写坏/版本不符同理直接重解析。"""
        cache_fp = os.path.join(user_dir(), '_game_cache.pkl')

        def fingerprint():
            try:
                paz0 = os.path.join(game_dir, '0008', '0.paz')
                pamt = os.path.join(game_dir, '0008', '0.pamt')
                paz1 = os.path.join(game_dir, '0008', '1.paz')
                lp = os.path.join(game_dir, '0032', '0.pamt')
                # C-5：0.paz 也带 mtime（同尺寸原地更新的形态要兜住）
                st = [(os.path.getsize(paz0), int(os.path.getmtime(paz0))),
                      (os.path.getsize(pamt), int(os.path.getmtime(pamt)))]
                #  ：dropsetinfo/characterinfo
                # 本体在 1.paz，旧指纹只盯 0.paz——1.paz 单独更新时缓存命中旧偏移
                # → 写盘写坏。把 1.paz 也纳入。
                if os.path.exists(paz1):
                    st.append((os.path.getsize(paz1), int(os.path.getmtime(paz1))))
                if os.path.exists(lp):
                    st.append((os.path.getsize(lp), int(os.path.getmtime(lp))))
                # 条目级指纹：dropsetinfo 的 (paz_index, offset, comp_size, orig_size)
                # 直接来自 pamt——Steam 增量更新只换数据卷时，这里也会变
                try:
                    import cd_paz as _cp
                    for e in _cp.parse_pamt(pamt):
                        if e.path == 'gamedata/dropsetinfo.staticinfobody':
                            st.append((e.paz_index, e.offset, e.comp_size, e.orig_size))
                            break
                except Exception:
                    pass
                return tuple(st)
            except Exception:
                return None

        fp = fingerprint()
        if fp:
            try:
                blob = _safe_load_pkl(cache_fp)
                # （）：加缓存格式标记。改了解析方式（game_data.PARSE_FMT）
                # 而游戏文件没变时，文件指纹不会变 —— 旧缓存会一直命中，
                # 新解析器永远跑不到。用格式标记让缓存自动失效，
                # 比手改产品 VERSION 干净（VERSION 一动就要同步 README/version_info）。
                if (blob.get('fp') == fp and blob.get('ver') == VERSION
                        and blob.get('psrc') == game_data.PARSE_FMT
                        # 解析器源码指纹：改了解析代码就别再命中旧缓存
                        and blob.get('pcode') == self._parser_code_fp()):
                    gd = game_data.GameData.from_cache(
                        game_dir, blob['tabs'], blob['names'], blob['ds'],
                        blob.get('tab_dups'))
                    gd.hdr = blob.get('hdr') or b''   # 见写入处注释：别再丢索引
                    return gd, blob.get('realclass') or {}
            except Exception:
                pass
        # 全量解析
        gd = game_data.GameData(game_dir, {})
        realclass = {}
        try:
            import charinfo
            realclass = charinfo.build_real_classes(game_dir, self.qlog.keys())
            if not realclass:
                realclass = charinfo.load_static(os.path.join(res_dir(), 'data'))
        except Exception:
            realclass = {}
        # F4：降级产物（名库空/realclass 空=解析失败）不写缓存——
        # 瞬时故障被缓存钉死会持续到下次游戏更新，且无提示
        if fp and gd.names and realclass:
            try:
                # C-6：缓存写也走 tmp+原子替换，防崩溃留半个文件
                ctmp = cache_fp + '.tmp'
                with open(ctmp, 'wb') as f:
                    pickle.dump({'fp': fp, 'ver': VERSION,
                                 'psrc': game_data.PARSE_FMT,
                                 # 解析器源码哈希：同一次适配里改两次解析、
                                 # PARSE_FMT 没变时，这条能让缓存自动失效
                                 'pcode': self._parser_code_fp(),
                                 'tabs': gd.tabs,
                                 'names': gd.names, 'ds': bytes(gd.ds),
                                 'realclass': realclass,
                                 # 官方索引必须一起缓存：from_cache 原先把它
                                 # 丢成 b''，于是「从缓存恢复后重解析 overlay」
                                 # 会走无索引降级路径（实测 1186 → 980 表），
                                 # 写盘回读校验就成了降级版对拍。索引只有
                                 # 123KB，带着它换回正确性。
                                 'hdr': bytes(gd.hdr or b''),
                                 'tab_dups': gd.tab_dups}, f)
                os.replace(ctmp, cache_fp)
            except Exception:
                pass
        return gd, realclass

    # ── 敌人聚合（同名变体合并）─────────────────────────────

    def classify(self, tables, has_drops):
        """按表名判敌意/细分。

        ⚠️ 两个坑（实测踩过）：
        1. `*_Common` 是**通用附带表**（Animal_Common 提供肉类、Human_Common 提供杂物），
           特异性低。很多敌人同时挂它 + 自己的专属表，拿它定分类会把
           「盗贼」误判成野兽。所以**先剔掉 _Common 表**再判。
        2. 用「表名包含关键词」而不是前缀：DropSet_Large_Animal_Bird_Common
           的前缀是 Large，前缀判据会漏判。
        """
        if not tables:
            # 有掉落却没匹配到表 —— 多半仍是敌人（匹配算法的锅），归「暂无对应表」，
            # 不能塞进友善，否则会误导（陶罐士兵、炸弹蜘蛛就曾被误分成友善）。
            return ('hostile', 'unknown') if has_drops else ('friendly', 'nodrop')
        specific = [t for t in tables if not t.endswith('_Common')] or tables
        joined = ' '.join((t[8:] if t.startswith('DropSet_') else t) for t in specific)
        for kw in ('Legendary', 'AbyssGear'):
            if kw in joined:
                return ('hostile', 'boss')
        for kw in ('Animal', 'Troll', 'Bug', 'Fish'):
            if kw in joined:
                return ('hostile', 'beast')
        for kw in ('Faction', 'Character', 'MiddleBoss', 'Human', 'Dwarf',
                   'Orc', 'Reblockade', 'Arrow', 'Hexe', 'Soldier'):
            if kw in joined:
                return ('hostile', 'humanoid')
        return ('hostile', 'other')

    # characterinfo 官方分类（desc 官方标签直读，见 charinfo.py）
    def _official_class_of(self, npc_ids):
        """组内 NPC 的官方分类（唯一分类源）。

        组内全部 NPC 的 realclass 一致才采用官方 (kind, sub)；任一缺失或
        不一致时返回 None，调用方回退 classify 表名推断（安全降级）。
        realclass 为空（无游戏/破解失败）时同样返回 None，行为与旧版一致。
        """
        if not getattr(self, 'realclass', None):
            return None
        rc = set()
        for nid in npc_ids:
            info = self.realclass.get(str(nid))
            if info and info.get('kind') and info.get('sub'):
                rc.add((info['kind'], info['sub']))
        if len(rc) == 1:
            return rc.pop()
        return None

    def table_group_count(self, tname):
        """有多少个敌人组共用这张表（共享表提示用：>=5 视为共享表）。
        全量算一次后缓存（_tab_gcount 在 load_game/迁移后失效重建）。"""
        cache = getattr(self, '_tab_gcount', None)
        if cache is None:
            cache = {}
            for e in self.enemies:
                tabs = set()
                for nid in e['npc_ids']:
                    for t in self.npc2tables.get(nid, {}).get('tables', []):
                        tabs.add(t[0])
                for t in tabs:
                    cache[t] = cache.get(t, 0) + 1
            self._tab_gcount = cache
        return cache.get(tname, 0)

    def stale_selection(self):
        """检查「存了的选择，在当前数据里一个都匹配不上」。

        这是跨版本事故的核心症状：1.0.1 的勾选到了 1.0.2 匹配不上，
        于是 apply 什么都不写，界面也不报错 —— 用户看到的只是「MOD 没用」。
        匹配不上几乎只有三种原因：版本换了 / 游戏更新了 / 数据文件陈旧。
        无论哪种，都必须让用户看见，而不是静默。

        返回 dict：
          groups  配置里有勾选的敌人数
          lost    一个槽都匹配不上的敌人数
          items   配不上的 (gid,item) 对数
          schema  读到的配置结构版本（0=无戳记，即更老的版本写的）
        """
        known = {e['gid']: e for e in self.enemies}
        groups = lost = items = 0
        seen = set()
        for (gid, k), v in (self.checked or {}).items():
            if not v:
                continue
            seen.add(gid)
            e = known.get(gid)
            if e is None or not self.drop_in_enemy(e, k):
                items += 1
        for gid in seen:
            groups += 1
            if gid not in known:
                lost += 1
        return {'groups': groups, 'lost': lost, 'items': items,
                'schema': getattr(self, 'cfg_schema_from', CFG_SCHEMA)}

    def game_changed_since_last(self):
        """游戏文件是否自上次运行后变了（指纹写入配置比对）。

        游戏更新是本项目最大的变化源。用户不会知道"为什么敌人都没表了"，
        只会觉得 MOD 坏了。这里给一个可判定的信号，界面据此提示重建映射。
        """
        try:
            import cd_paz as _cp
            g8 = os.path.join(self.game_dir or '', '0008')
            pamt = os.path.join(g8, '0.pamt')
            if not os.path.exists(pamt):
                return False
            es = _cp.parse_pamt(pamt)
            sig = None
            for e in es:
                if e.path == 'gamedata/dropsetinfo.staticinfobody':
                    sig = (e.paz_index, e.offset, e.comp_size, e.orig_size)
                    break
            if sig is None:
                return False
            last = self.cfg.get('fp_dropsetinfo')
            changed = (last is not None and list(last) != list(sig))
            # 无论是否变化都记下最新值，下次才有得比
            self.cfg['fp_dropsetinfo'] = list(sig)
            return bool(changed)
        except Exception:
            return False

    def undo_last_apply(self):
        """撤销上一次应用：把 overlay 恢复成最近一份 .bak 备份。

        备份本来就每次写盘都生成（`.bak-时间戳`），所以撤销几乎是白捡的。
        以前只有「恢复原版」一个极端选项，中间没有退路 —— 改错了只能
        全部推倒重来。
        返回 (ok, 说明)。
        """
        game = self.game_dir
        if not game or not os.path.isdir(game):
            return False, 'need_game'
        ov = os.path.join(game, OVERLAY_REL)
        d = os.path.dirname(ov)
        if not os.path.isdir(d):
            return False, 'undo_none'
        base = os.path.basename(ov)
        try:
            baks = sorted((f for f in os.listdir(d)
                           if f.startswith(base + '.bak-')),
                          key=lambda f: os.path.getmtime(os.path.join(d, f)))
        except Exception:
            baks = []
        if not baks:
            return False, 'undo_none'
        src = os.path.join(d, baks[-1])
        # ：撤销也是写盘——游戏更新后 .bak 里的旧偏移全部失效，
        # 裸写回就是 A-2 要防的形态。与 apply_changes 同口径校验。
        paz0 = os.path.join(game, '0008', '0.paz')
        if (not os.path.exists(paz0)
                or os.path.getsize(paz0) != self._paz_base_expected()
                or not self._ds_fingerprint_ok(game)):
            return False, ('err_game_changed',
                           os.path.getsize(paz0) if os.path.exists(paz0) else 0)
        try:
            with open(src, 'rb') as f:
                data = f.read()
        except Exception as ex:
            log_exc('undo_last_apply 读备份', ex)
            return False, ('err_raw', str(ex))
        if self.gd and len(data) != len(self.gd.ds):
            log('undo_last_apply 拒绝: 备份长度 %d != 当前解析 %d（游戏可能已更新）'
                % (len(data), len(self.gd.ds)))
            return False, ('err_ds_mismatch', 'undo length mismatch')
        try:
            tmp = ov + '.tmp%d' % os.getpid()
            os.makedirs(d, exist_ok=True)
            with open(tmp, 'wb') as f:
                f.write(data)
            os.replace(tmp, ov)
        except Exception as ex:
            log_exc('undo_last_apply', ex)
            return False, ('err_raw', str(ex))
        log('撤销上次应用: 还原自 %s' % src)
        snap = getattr(self, '_undo_sel_snapshot', None)
        if snap:
            self.checked = dict(snap[0])
            self.gdel = set(snap[1])
            self.boost = dict(snap[2])
            self._undo_sel_snapshot = None
        return True, os.path.basename(src)

    def overlay_consistent(self):
        """磁盘上的 overlay 与当前变更计划是否一致。

        这是「我在游戏里看不到效果」最常见的根因之一：界面上勾了、写盘也
        "成功了"，但磁盘上的字节和你想的不一样（被别的 MOD 覆盖、手动改过、
        游戏更新后偏移位移、上次应用失败留下半成品）。
        做法很直接：重读磁盘 overlay，逐槽对照计划该有的值。
        返回 (一致?, [差异...])。没有 overlay 时返回 (True, '无 overlay')。
        """
        game = self.game_dir
        if not game:
            return True, ['未设置游戏目录']
        ov = os.path.join(game, OVERLAY_REL)
        if not os.path.exists(ov):
            return True, ['无 overlay（尚未应用过）']
        try:
            with open(ov, 'rb') as f:
                data = f.read()
        except Exception as ex:
            return False, ['读取 overlay 失败: %s' % ex]
        if len(data) != len(self.gd.ds):
            return False, ['overlay 长度 %d ≠ 原版 %d（可能游戏已更新）'
                           % (len(data), len(self.gd.ds))]
        pl = self.change_plan()
        diffs = []
        # 只读字段（key / min / max）期望值 = 原版字节：删除只清 rate、提率只改
        # rate，都不碰这三个字段（ ：这三字段旧版零校验）。
        # ⚠️ key 只校验 +1 一处：+60 的 key 副本实测 4048 槽有 3 个合法不一致
        #    （99.93%），做强校验会对那三张表永久误报。
        def _ro_diff(tname, off):
            if data[off + 1:off + 5] != self.gd.ds[off + 1:off + 5]:
                diffs.append((tname, off, 'key 被改动', 0))
            if data[off + 42:off + 50] != self.gd.ds[off + 42:off + 50]:
                diffs.append((tname, off, 'min 被改动', 0))
            if data[off + 50:off + 58] != self.gd.ds[off + 50:off + 58]:
                diffs.append((tname, off, 'max 被改动', 0))

        for tname, off, old_r, k in pl.delete:
            if off + 34 > len(data):
                diffs.append((tname, off, '越界', 0))
                continue
            cur = struct.unpack_from('<Q', data, off + 26)[0]
            if cur != 0:
                diffs.append((tname, off, '应为 0（已勾删除）', cur))
            _ro_diff(tname, off)
        for tname, off, old_r, new_r, k in pl.boost:
            if off + 34 > len(data):
                diffs.append((tname, off, '越界', new_r))
                continue
            cur = struct.unpack_from('<Q', data, off + 26)[0]
            if cur != new_r:
                diffs.append((tname, off, '应为 %d' % new_r, cur))
            _ro_diff(tname, off)
        return (not diffs), diffs

    def conflicting_mods(self):
        """找出游戏 mods/ 下同样改写 dropsetinfo 的其他 MOD（自己除外）。

         ：旧版按目录名 'nojunk'/'nojunk2' 跳过——
        NoJunk 1.x 残留文件（.restorebak / 旧 .bak-）永远不会被提示。
        改为：同目录名允许共存（2.x 会覆盖 1.x 的 overlay 文件），但
        扫描时检测目录里是否存在"非本工具产物"的孤儿文件，有则报告。"""
        game = self.game_dir
        if not game:
            return []
        mbase = os.path.join(game, 'mods')
        if not os.path.isdir(mbase):
            return []
        out = []
        # 本工具自身产物的命名规则（唯一来源：apply_changes 备份段 + restore_vanilla）
        _ovb = os.path.basename(OVERLAY_REL)
        _own_bak = _ovb + '.bak-'          # ...bak-<时间戳>[-序号]
        _own_restore = _ovb + '.restorebak'

        def _is_own(f):
            """该文件是不是本工具自己写的（是就不算他人残留）。

            自己的产物清单（就这三类，别处再无写盘）：
              · overlay 本体        dropsetinfo.staticinfobody
              · apply 备份          dropsetinfo.staticinfobody.bak-<时间戳>[-序号]
              · restore_vanilla 备份 dropsetinfo.staticinfobody.restorebak
            """
            return f == _ovb or f.startswith(_own_bak) or f == _own_restore

        try:
            for entry in sorted(os.listdir(mbase)):
                if entry.lower() in ('nojunk', 'nojunk2'):
                    # 同名目录里检查 1.x 残留（restorebak / 旧格式 bak）
                    d = os.path.join(mbase, entry)
                    for r, _dirs, fs in os.walk(d):
                        #  ：旧判据里的 `and 'NoJunk' not in f`
                        # 恒为 True —— f 是 os.walk 给出的**文件名**（形如
                        # dropsetinfo.staticinfobody.bak-20261029-153000），而
                        # 'NoJunk' 是**目录名**，永远不会出现在文件名里。
                        # 后果：本工具每次 apply 自己产生的 .bak- 备份被全部当成
                        # "旧版残留"上报（实测 6 个文件全是自己的产物），用户每次
                        # 自检都看到假警报 → 警告疲劳 → 真警报被忽略。
                        # 正确判据：mods/NoJunk*/ 是本工具的命名空间，凡符合本工具
                        # 命名规则的产物都是自己的；这个目录里**任何别的 dropsetinfo
                        # 文件**（旧版 .bak、他人工具的同名覆盖物等）才是残留。
                        orphans = [f for f in fs
                                   if f.startswith('dropsetinfo')
                                   and not _is_own(f)]
                        if orphans:
                            out.append(os.path.relpath(r, mbase)
                                       + ' (旧版残留 %d 文件)' % len(orphans))
                    continue
                d = os.path.join(mbase, entry)
                if not os.path.isdir(d):
                    continue
                for r, _dirs, fs in os.walk(d):
                    if 'dropsetinfo.staticinfobody' in fs:
                        out.append(os.path.relpath(r, mbase))
        except Exception:
            pass
        return out

    def backup_count(self):
        """backup 目录里 .bak- 备份的数量（太多会占盘）。1.0.3：备份已移出 files\。"""
        game = self.game_dir
        if not game:
            return 0
        d = _backup_dir(game)
        if not os.path.isdir(d):
            return 0
        try:
            return len([f for f in os.listdir(d)
                        if f.startswith(os.path.basename(OVERLAY_REL) + '.bak-')])
        except Exception:
            return 0

    # ── 自检 ─────────────────────────────────────────────────────
    #
    # 2026-10-30 重做：旧版是"边查边打印"——先铺一堆数字，结论埋在最后一行，
    # 而且只说"不一致 216 处"这种，用户看完不知道要干嘛。
    # 新版改成"先体检、后排版"：
    #   · 每条问题登记 (严重度, 一句话现象, 怎么办, 细节)
    #   · 结论提到**最前面**，按 P0/P1/P2 排
    #   · 每条都带"怎么办"，让人照着做就能修
    # 严重度沿用 plan 的定义：P0 = 会白玩（改了没效果/数据坏了），
    # P1 = 能感知到不对但找不出原因，P2 = 小瑕疵不影响用。

    def _diag_collect(self):
        """跑一遍全部检查，返回问题清单。不打印任何东西。

        每条 = dict(sev, what, how, detail)
        """
        probs = []
        gd = self.gd
        game = self.game_dir

        # ── 1. 游戏目录 / 数据基础 ───────────────────────────────
        if not game or not os.path.isdir(game):
            probs.append(dict(
                sev='P0', what='游戏目录无效：%s' % (game or '未设置'),
                how='点「浏览」重新选择游戏根目录（要含 0008/0.pamt 的那个）',
                detail=''))
        if gd is None:
            probs.append(dict(sev='P0', what='游戏数据未加载',
                              how='点「加载」按钮', detail=''))
            return probs
        if not gd.tabs:
            probs.append(dict(
                sev='P0', what='解析出 0 张掉落表',
                how='游戏大概率已更新，本工具还不认识新版格式；'
                    '先点「重建映射」，仍为 0 就说明需要等工具适配',
                detail=''))

        # 解析质量：整除率是"结构对不对"的硬指标
        d = getattr(gd, 'diag', None) or {}
        ratio = d.get('align_ratio')
        if d.get('mode') == 'v2' and ratio is not None and ratio < 0.90:
            probs.append(dict(
                sev='P0',
                what='掉落表结构对不齐（整除率 %.1f%%，正常应 ≥90%%）' % (ratio * 100),
                how='游戏改了掉落表结构，工具需要更新；'
                    '此时不要点应用，写进去的偏移可能是错的',
                detail=''))
        if d.get('break'):
            probs.append(dict(
                sev='P1', what='有 %d 张表在槽自检时中断' % d['break'],
                how='这些表可能少收了掉落物，详情见下方「解析详情」',
                detail=''))

        # ── 2. 版本指纹 ─────────────────────────────────────────
        try:
            p0 = os.path.join(game or '', '0008', '0.paz')
            if os.path.exists(p0):
                real, exp = os.path.getsize(p0), self._paz_base_expected()
                if real != exp:
                    probs.append(dict(
                        sev='P0',
                        what='游戏文件在本次运行期间变了（0.paz %d ≠ %d）' % (real, exp),
                        how='重新点「加载」，让工具按新文件重新解析，再应用',
                        detail=''))
            # dropsetinfo 条目级指纹：1.paz 是 912MB 大包，只比对包大小会把
            # 「掉落表没动」的更新误报为版本变化；重读 0.pamt 比对条目四元组
            # （paz_index/offset/comp_size/orig_size）才是精确判据。
            if not self._ds_fingerprint_ok(game or ''):
                probs.append(dict(
                    sev='P0',
                    what='游戏文件在本次运行期间变了（dropsetinfo 条目位置/大小不符）',
                    how='重新点「加载」，让工具按新文件重新解析，再应用',
                    detail=''))
        except Exception:
            pass

        # ── 3. 已存在的 overlay 是否过期 ─────────────────────────
        # 始终现场重算，别用 load_game 时的快照：用户可能中途点过
        # 「恢复原版」或「应用」，快照早就过期了，拿旧的报会冤枉人。
        info = None
        if game:
            try:
                info = self.check_stale_overlay()
            except Exception:
                info = None
        if info:
            how = ('去「全局」页点「恢复原版」清掉它，再重新选择、重新应用'
                   if info.get('reason') == 'size'
                   else '重新点一次「应用」，让工具重写这份补丁')
            probs.append(dict(
                sev='P0',
                what='磁盘上的旧补丁已过期：' + info.get('detail', ''),
                how=how, detail=''))

        # ── 4. 存的选择是否还对得上 ──────────────────────────────
        try:
            r = self.stale_selection()
            lost = r.get('lost', 0) + r.get('items', 0)
            if lost:
                probs.append(dict(
                    sev='P0',
                    what='%d 项已存的选择在当前数据里找不到了' % lost,
                    how='游戏更新或数据陈旧导致。点「重建映射」，'
                        '然后重新勾选要删的掉落',
                    detail=''))
        except Exception:
            pass

        # ── 5. 当前计划的健康度 ─────────────────────────────────
        try:
            pl = self.change_plan()
            s = pl.as_dict()
            if s.get('infeasible'):
                probs.append(dict(
                    sev='P1',
                    what='%d 张表装不下你设的掉率，已自动跳过' % s['infeasible'],
                    how='把这几张表的目标掉率调低，或减少同时提率的物品',
                    detail=''))
            reasons = s.get('by_reason') or {}
            if reasons.get('NONDROP'):
                probs.append(dict(
                    sev='P2',
                    what='%d 个槽属于奖励/交易来源，按设计已跳过' % reasons['NONDROP'],
                    how='这是预期行为。确实要改，去全局页勾「包含非掉落来源」',
                    detail=''))
        except Exception as ex:
            probs.append(dict(sev='P1', what='变更计划算不出来：%s' % ex,
                              how='把这份报告发给作者', detail=''))

        # ── 6. 磁盘 overlay 与计划是否一致（最容易"白玩"的一项）──
        try:
            ov = os.path.join(game or '', OVERLAY_REL)
            has_ov = os.path.exists(ov)
            plan_n = 0
            try:
                pl = self.change_plan()
                plan_n = len(pl.delete) + len(pl.boost)
            except Exception:
                pass
            if plan_n and not has_ov:
                probs.append(dict(
                    sev='P0',
                    what='你有 %d 项改动还没写到磁盘（overlay 不存在）' % plan_n,
                    how='点「应用」把它写进去',
                    detail=''))
            elif has_ov and plan_n:
                okv, diffs = self.overlay_consistent()
                # diffs 的第一项若是字符串，说明是"读不了/长度不符"这类整体性问题
                if isinstance(diffs, list) and diffs and isinstance(diffs[0], str):
                    probs.append(dict(
                        sev='P0', what='磁盘补丁与当前游戏对不上：%s' % diffs[0],
                        how='点「恢复原版」清掉，再重新选择、应用',
                        detail=''))
                elif not okv:
                    nzero = sum(1 for x in diffs if '应为 0' in str(x[2]))
                    nboost = len(diffs) - nzero
                    kinds = []
                    if nzero:
                        kinds.append('%d 个"该删却没删"' % nzero)
                    if nboost:
                        kinds.append('%d 个"掉率没改成目标值"' % nboost)
                    probs.append(dict(
                        sev='P0',
                        what='磁盘上的补丁和你的计划不一致：共 %d 处（%s）'
                             % (len(diffs), '，'.join(kinds) or '未知'),
                        how='最常见是上次应用中途失败或被别的 MOD 覆盖。'
                            '重新点一次「应用」即可',
                        detail=''))
        except Exception:
            pass

        # ── 7. 环境 ─────────────────────────────────────────────
        try:
            cm = self.conflicting_mods()
            if cm:
                probs.append(dict(
                    sev='P1', what='检测到同样改写掉落表的其他 MOD：%s' % cm,
                    how='两个 MOD 会互相覆盖（谁后加载谁生效）。'
                        '只保留一个，否则改了看不到效果',
                    detail=''))
        except Exception:
            pass
        try:
            nb = self.backup_count()
            if nb > 5:
                probs.append(dict(
                    sev='P2', what='overlay 目录里有 %d 个备份文件' % nb,
                    how='每份约 2.7MB。确认游戏里效果正常后，'
                        '可以手动删掉 .bak-* 的老备份',
                    detail=''))
        except Exception:
            pass
        try:
            wdir = os.path.join(game or '', 'mods')
            if game:
                # ⚠️ mods/ 可能还不存在（从没装过 MOD 的干净游戏）。
                # os.access 对不存在的目录一律返回 False，直接拿来判定
                # 会误报"不可写"——实际是"还没建，写的时候会自动创建"。
                # 所以：不存在就只看父目录（游戏根）能不能写。
                probe = wdir if os.path.isdir(wdir) else (game or '')
                if probe and not os.access(probe, os.W_OK):
                    probs.append(dict(
                        sev='P0', what='游戏目录不可写：%s' % probe,
                        how='游戏装在受保护位置（如 Program Files）。'
                            '把游戏移到普通目录，或用管理员权限运行本工具',
                        detail=''))
        except Exception:
            pass

        # ── 8. 配置 ─────────────────────────────────────────────
        try:
            # ⚠️ 配置里存的是 'cfg_ver'（见 save_cfg），不是 'schema' ——
            # 读错键名会永远拿到默认 0，然后对所有人误报"配置文件是旧版"。
            sch = self.cfg.get('cfg_ver', CFG_SCHEMA)
            if sch < CFG_SCHEMA:
                probs.append(dict(
                    sev='P2',
                    what='配置文件是旧版（结构 %s，本版 %d）' % (sch, CFG_SCHEMA),
                    how='重新保存一次设置就会自动升级，不影响使用',
                    detail=''))
        except Exception:
            pass
        return probs

    def self_check(self):
        """一键自检 —— 出问题时点这个，把报告整段复制发给作者。

        报告结构：**结论在最前**（P0 白玩 / P1 有毛病 / P2 小瑕疵），
        每条都写"怎么办"；后面才是详细数据，供人工核对。
        """
        probs = []
        fatal = None
        try:
            probs = self._diag_collect()
        except Exception as ex:
            fatal = repr(ex)

        L = []
        A = L.append
        A('NoJunk2 自检报告')
        A('版本: %s   配置结构: %s' % (VERSION, CFG_SCHEMA))
        A('生成: %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
        A('')

        # ══ 结论（放最前：用户最想知道"我该怎么办"）══
        A('=' * 52)
        if fatal:
            A('结论：自检本身出错了，请把下面整段发给作者')
            A('      %s' % fatal)
        else:
            by = {'P0': [], 'P1': [], 'P2': []}
            for p in probs:
                by.setdefault(p['sev'], []).append(p)
            if not probs:
                A('结论：没发现异常 ✅')
                A('      如果游戏里仍看不到效果，多半是加载器那边没生效：')
                A('        · 每次在这里点完「应用」，都要去加载器里再 Apply 一次；')
                A('        · 确认加载器里 NoJunk 是启用状态，且没有别的 MOD 抢同一个文件。')
            else:
                A('结论：发现 %d 个问题（P0 %d 不能带着玩 / P1 %d / P2 %d）'
                  % (len(probs), len(by['P0']), len(by['P1']), len(by['P2'])))
                A('')
                tag = {'P0': '【P0 · 会白玩】', 'P1': '【P1 · 有毛病】',
                       'P2': '【P2 · 小瑕疵】'}
                i = 0
                for sev in ('P0', 'P1', 'P2'):
                    for p in by.get(sev, []):
                        i += 1
                        A('%s %d. %s' % (tag[sev], i, p['what']))
                        A('       怎么办：%s' % p['how'])
                        if p.get('detail'):
                            A('       细节：%s' % p['detail'])
                        A('')
        A('=' * 52)
        A('')

        # ══ 详细数据（人工核对用）══
        A('[游戏]')
        A('  目录: %s' % (self.game_dir or '-'))
        try:
            p0 = os.path.join(self.game_dir or '', '0008', '0.paz')
            if os.path.exists(p0):
                A('  0.paz 实际 %d / 期望 %d %s'
                  % (os.path.getsize(p0), self._paz_base_expected(),
                     '✔' if os.path.getsize(p0) == self._paz_base_expected() else '✘'))
            else:
                A('  0.paz: 未找到')
            ent = self._read_ds_entry(self.game_dir or '')
            if ent:
                A('  dropsetinfo 条目 %s'
                  % ('一致 ✔' if ent == self._load_ds_entry else '已变化 ✘'))
            else:
                A('  dropsetinfo 条目: 未找到')
        except Exception:
            pass
        try:
            ov = os.path.join(self.game_dir or '', OVERLAY_REL)
            A('  overlay: %s' % ('已生成 %d 字节' % os.path.getsize(ov)
                                 if os.path.exists(ov) else '无（还没应用过）'))
        except Exception:
            pass
        A('')

        A('[解析]')
        if self.gd:
            d = getattr(self.gd, 'diag', None) or {}
            A('  方式: %s%s' % (d.get('mode', '?（来自缓存，无诊断）'),
                               '' if d.get('mode') else '  ← 重启工具可看到完整诊断'))
            A('  表数 %d / 槽数 %d / 重复槽组 %d'
              % (len(self.gd.tabs),
                 sum(len(v) for v in self.gd.tabs.values()),
                 len(self.gd.tab_dups)))
            if d.get('align_ratio') is not None:
                A('  结构整除率: %.1f%%（≥90%% 为正常）' % (d['align_ratio'] * 100))
            A('  槽自检 ff/krep: %s / %s' % (d.get('ff', '-'), d.get('krep', '-')))
            for w in (d.get('warn') or [])[:8]:
                A('  ⚠ %s' % w)
        else:
            A('  未加载')
        A('')

        A('[选择与计划]')
        try:
            r = self.stale_selection()
            A('  勾选组 %d / 找不到 %d / 物品级失配 %d（配置结构 %s，本版 %d）'
              % (r['groups'], r['lost'], r['items'],
                 r.get('schema', '?'), CFG_SCHEMA))
        except Exception as ex:
            A('  读取失败: %s' % ex)
        try:
            s = self.change_plan().as_dict()
            A('  清零 %d / 改写 %d / 提率 %d / 影响敌人组 %d'
              % (s['deleted'], s['slots'], s['boosts'], s['enemies']))
            A('  跳过 %d %s' % (s['skipped'], s['by_reason'] or ''))
        except Exception as ex:
            A('  计划计算失败: %s' % ex)
        A('')

        A('[磁盘差异明细]（「应用了没效果」时才需要看）')
        try:
            okv, diffs = self.overlay_consistent()
            if isinstance(diffs, list) and diffs and isinstance(diffs[0], str):
                A('  %s' % diffs[0])
            elif okv:
                A('  overlay 与计划一致')
            else:
                A('  不一致 %d 处，前 10 条：' % len(diffs))
                for t, o, want, cur in diffs[:10]:
                    A('    %-40s @%-7d %s（实际 %s）' % (t[:40], o, want, cur))
                if len(diffs) > 10:
                    A('    …其余 %d 处同理（多半是整批没写进去）' % (len(diffs) - 10))
        except Exception as ex:
            A('  检查失败: %s' % ex)
        A('')

        A('[环境]')
        try:
            cm = self.conflicting_mods()
            A('  冲突 MOD: %s' % (cm if cm else '无'))
        except Exception:
            pass
        try:
            A('  备份文件: %d 个' % self.backup_count())
        except Exception:
            pass
        try:
            gd_ = self.game_dir
            w = bool(gd_) and os.access(os.path.join(gd_, 'mods'), os.W_OK)
            A('  写盘权限: %s' % ('可写' if w else '不可写'))
        except Exception:
            A('  写盘权限: 无法判定')
        return '\n'.join(L)


    def enemy_uncalibrated(self, gid):
        """该敌人组的挂表是否**未经 questlog 校验**（重建推断出来的）。

        M8 写明：重建出来的新敌人一律标 `calibrated=False`，"推断挂表，
        未经 questlog 校验，UI 应提示未校准"。以前界面完全不知道这回事——
        而「一键重建映射」一次就会新增上百个这样的敌人，界面却当成已确认的
        正常敌人显示。用户照着勾 → 表可能是猜的 → 错了不吭声。
        """
        try:
            ids = self._gid_targets(gid)
        except Exception:
            ids = []
        if not ids:
            for e in self.enemies:
                if e['gid'] == gid:
                    ids = e['npc_ids']
                    break
        for nid in ids:
            ent = self.npc2tables.get(str(nid))
            if isinstance(ent, dict) and ent.get('calibrated') is False:
                return True
        return False

    def change_plan(self):
        """算出本次变更计划（**唯一决策入口**）。

        预览 / 写盘 / 导出 / 恢复后重建，全部调用这一个方法。
        以前这四处的判断是各自抄的，改规则时漏改一处就是静默不一致
        （实测已发生 4 次）。现在结构上只能有一条路径。
        """
        import plan as _plan
        return _plan.build_plan(self.gd, self.npc2tables, self.enemies,
                                self.item_slots, self,
                                lambda t: self._slot_allowed(t))

    def scoped_boost_count(self):
        """会真正写盘的提率项数（与写盘路径同口径）。

        提率目标若只落在非掉落物表里，写盘一个字节都不会动；
        这时若仍报「提率 N 项」，用户以为改上了，实际没改——静默欺骗。
        UI 计数 / 状态栏 / preview / 空选择判定 全部走这里。
        """
        n = 0
        for k in self.boost:
            try:
                ik = int(k)
            except (TypeError, ValueError):
                continue
            if any(self._slot_allowed(t) for t, _o, _r
                   in self.item_slots.get(ik, [])):
                n += 1
        return n

    def _slot_allowed(self, tname):
        """该表是否在本次作用域内——**唯一的判定入口**，四条路径共用。

        作用域 = 掉落物。非掉落物来源（购买/推广奖励、交易、好感度回礼）
        默认排除，可用 include_nondrop 显式放行。
        ⚠️ 新增任何写盘/导出路径都走这里，别各自写一份判断。
        """
        if self.include_nondrop:
            return True
        return not is_nondrop_table(tname)

    def nondrop_slots_of(self, k):
        """该物品所有「非掉落物来源」的槽 [(tname, off, rates)]。
        界面标注（🎁）与二次确认用。"""
        try:
            ik = int(k)
        except (TypeError, ValueError):
            return []
        return [(t, o, r) for t, o, r in self.item_slots.get(ik, [])
                if is_nondrop_table(t)]

    def drop_in_enemy(self, e, k):
        """该物品是否在此敌人组的映射掉落表里（含共享表）。
        False = 它来自好感度回礼/偷窃/宝箱等非击杀来源（questlog 混排显示），
        按敌删除管不到它 —— UI 用来置灰防呆（用户实测踩过：勾了没反应）。"""
        try:
            ik = int(k)
        except (TypeError, ValueError):
            return False
        for nid in e['npc_ids']:
            for t in self.npc2tables.get(nid, {}).get('tables', []):
                if ik in self.gd.tabs.get(t[0], {}):
                    return True
        return False

    def _table_sig(self, tables):
        """掉落表签名：排序后去 DropSet_ 前缀、用 '#' 连接。

        gid 用它做「表集合指纹」：同名敌人按 (name, 表集合) 拆分，
        表集合相同才合并，不同则各自成组。'#' 与 iid 分隔符 '|' 不冲突。"""
        return '#'.join((t[8:] if t.startswith('DropSet_') else t)
                        for t in sorted(set(tables)))

    def _gid_of(self, npc_ids, sig):
        """组唯一标识：与界面语言解耦（旧版 gid 含显示名，切语言后勾选/白名单
        全部静默失效）。取组内最小 NPC 数字 id + 表签名：'n<id>#<sig>'；
        无表（unknown）时退化为 'n<id>'。id 取不到数字时用字符串兜底。"""
        def _sk(x):
            try:
                return (0, int(x))
            except (TypeError, ValueError):
                return (1, str(x))
        base = min(npc_ids, key=_sk) if npc_ids else '0'
        try:
            anchor = 'n%d' % int(base)
        except (TypeError, ValueError):
            anchor = 'n%s' % base
        return ('%s#%s' % (anchor, sig)) if sig else anchor

    def _group_by_name(self):
        """questlog NPC 按「显示名 + 掉落表集合」聚合（gid 拆分改造）。

        旧版纯按 name 合并：同名但掉落表不同的敌人被并成一组，勾选删除/白名单
        会同时作用到所有变体的表（点修改掉率不生效/作用错表的根因）。
        现在同名且表集合相同才合并；同名但表集合不同拆成多组，
        每组有唯一 gid，checked/ewl/UI iid/写盘全部以 gid 为键。"""
        by = {}
        for nid, info in self.qlog.items():
            nm = info.get('name') or nid
            tables = [t[0] for t in self.npc2tables.get(nid, {}).get('tables', [])]
            sig = self._table_sig(tables)
            g = by.setdefault((nm, sig),
                              {'npc_ids': [], 'drops': {}, 'tables': set(), 'sig': sig})
            g['npc_ids'].append(nid)
            for d in info.get('drops', []):
                k = str(d['id'])
                old = g['drops'].get(k)
                if not old:
                    g['drops'][k] = dict(d)
                else:
                    if isinstance(d.get('p'), (int, float)):
                        old['p'] = max(old.get('p') or 0, d['p'])
                    if d.get('q') and old.get('q'):
                        old['q'] = [min(old['q'][0], d['q'][0]),
                                    max(old['q'][1], d['q'][1])]
            for t in tables:
                g['tables'].add(t)
        out = []
        for (nm, _sig), g in by.items():
            # 组级官方附加信息：官方子类型 npcInfoKey / desc 原文 / 官方标签并集
            infokey, desc_txt, tags = '-', '-', set()
            for nid in g['npc_ids']:
                if infokey == '-':
                    ik = self.npc_infokey.get(str(nid)) or ''
                    if ik:
                        infokey = ik
                rc = self.realclass.get(str(nid)) or {}
                if desc_txt == '-' and rc.get('desc'):
                    desc_txt = rc['desc']
                if rc.get('desc'):
                    try:
                        import charinfo
                        pr = charinfo._parse_desc(rc['desc'])
                        if pr:
                            typ, camp, extras = pr
                            tags |= {typ, camp} | set(extras)
                    except Exception:
                        pass
            # 官方掉率聚合（questlog probability，与内部权重并存不改写盘）
            prob_of = {}
            for nid in g['npc_ids']:
                pm = self.questlog_prob.get(str(nid))
                if not pm:
                    continue
                for k, v in pm.items():
                    prob_of.setdefault(k, v)
            drops = []
            for k, d in g['drops'].items():
                q = d.get('q') or [1, 1]
                pv = prob_of.get(k) or {}
                drops.append({
                    'id': k,
                    'name': self.gd.item_name(int(k), k) if k.isdigit() else k,
                    'p': d.get('p') or 0,
                    'prob': pv.get('prob') if isinstance(pv.get('prob'), (int, float)) else None,
                    'q': '%d' % q[0] if q[0] == q[1] else '%d-%d' % (q[0], q[1]),
                    'type': d.get('type') or 'random',
                })
            drops.sort(key=lambda x: x['name'])
            # 官方分类优先（desc 官方标签唯一源）；无官方分类时回退表名推断
            kind, sub = self._official_class_of(g['npc_ids']) or \
                self.classify(sorted(g['tables']), bool(g['drops']))
            out.append({'name': nm, 'gid': self._gid_of(g['npc_ids'], g['sig']),
                        'sig': g['sig'],
                        'npc_ids': g['npc_ids'], 'drops': g['drops'],
                        'tables': sorted(g['tables']), 'n_drops': len(drops),
                        'drops_list': drops, 'grade': 0, 'kind': kind, 'sub': sub,
                        'infokey': infokey, 'desc': desc_txt, 'tags': tags})
        # gid 唯一性兜底：同名+同签名必然同组不会重复；仅当敌人名本身含 '#'
        # 且与另一组签名恰好撞车时（理论极端），补 '#序号' 后缀保证 iid 唯一
        seen_gid = {}
        for e in out:
            base = e['gid']
            n = seen_gid.get(base, -1) + 1
            seen_gid[base] = n
            if n:
                e['gid'] = '%s#%d' % (base, n)
        self._fallback_match(out)
        out.sort(key=lambda x: (x['kind'] != 'hostile', -x['n_drops'], x['name']))
        return out

    def _fallback_match(self, out):
        """兜底匹配：给「暂无对应表」的敌人再找一次表。
        判据：该表能覆盖它 >=80% 的掉落物。
        安全约束：跳过被 >=5 个敌人共用的共享表（否则把大众表安到它头上会连累别人）。"""
        used = {}
        for e in out:
            for t in e['tables']:
                used[t] = used.get(t, 0) + 1
        fixed = 0
        for e in out:
            if e['tables']:
                continue
            items = set()
            for k in e['drops']:
                if k.isdigit():
                    items.add(int(k))
            if not items:
                continue
            best = []
            for tname, tab in self.gd.tabs.items():
                if used.get(tname, 0) >= 5:
                    continue
                hit = items & set(tab.keys())
                if hit:
                    best.append((len(hit) / float(len(items)), tname))
            if not best:
                continue
            best.sort(reverse=True)
            cov, tname = best[0]
            if cov >= 0.8:
                e['tables'].append(tname)
                used[tname] = used.get(tname, 0) + 1
                # F2 修复：fallback 命中的表必须同时写回 npc2tables——
                # 否则 drop_in_enemy/apply_changes ②（都查 npc2tables）判不到它，
                # UI 表列显示有表、实际却置灰拒勾，用户困惑（审查实测）。
                for nid in e['npc_ids']:
                    rec = self.npc2tables.setdefault(
                        nid, {'name': e['name'], 'en': None, 'tables': [],
                              'n_drops': e['n_drops']})
                    if not any(t[0] == tname for t in rec['tables']):
                        rec['tables'].append((tname, cov, len(hit), True))
                fixed += 1
        for e in out:
            e['kind'], e['sub'] = self._official_class_of(e['npc_ids']) or \
                self.classify(e['tables'], e['n_drops'] > 0)
            #  ：掉落数据硬约束——
            # 有击杀掉落表的组绝不能标 nodrop（desc 阵营标签 NPC ≠ 不可击杀，
            # 流浪佣兵就是阵营 NPC 但有专表 DropSet_Character_Wandering_Mercenary）
            if e['kind'] == 'friendly' and e['n_drops'] > 0:
                e['kind'], e['sub'] = 'hostile', 'humanoid'
        self.fallback_fixed = fixed

    def group_of(self, gid):
        """按组唯一标识 gid 查敌人组（不再是 name，同名拆组后 name 有歧义）。"""
        for e in self.enemies:
            if e['gid'] == gid:
                return e
        return None

    # ── 选择操作（纯逻辑，UI 只负责调用+刷新）────────────────

    def cycle_drop(self, enemy, k):
        """点勾选框：☐不删 → ☑要删 → ⊠该敌人保护 → ☐。返回新标记。
        enemy 实为 gid（组唯一标识）；提率物品不参与勾选删除（语义冲突）。
        物品不在该组映射掉落表（好感度回礼/偷窃等非击杀来源）→ 'ext' 拒绝。"""
        if k in self.gwl:
            return 'gwl'
        if k in self.boost:
            return 'boost'
        # 未校准敌人的挂表是推断出来的，没经过 questlog 校验——往里写就是
        # 往「猜来的表」上写，可能删错槽。界面把这类敌人置灰只是提示，
        # 键盘/全选/预设等路径不走界面判断，所以这里必须在核心层兜住。
        if self.enemy_uncalibrated(enemy):
            return 'uncal'
        g = self.group_of(enemy)
        if g is not None and not self.drop_in_enemy(g, k):
            return 'ext'
        ew = list(self.ewl.get(enemy, []))
        if k in ew:
            self.ewl[enemy] = [x for x in ew if x != k]
            if not self.ewl[enemy]:
                self.ewl.pop(enemy, None)
            self.checked[(enemy, k)] = False
            return 'none'
        if self.checked.get((enemy, k)):
            self.checked[(enemy, k)] = False
            self.ewl[enemy] = sorted(set(ew) | {k})
            return 'wl'
        self.checked[(enemy, k)] = True
        return 'del'

    def mark_of(self, enemy, k):
        """行标记：全局白名单 > 提率 > 敌人白名单 > 删除 > 无。
        boost 必须有自己的标记 —— 否则 UI 上色/排序拿不到提率态，
        橙色 boo 标签形同虚设。"""
        if k in self.gwl:
            return 'gwl'
        if k in self.boost:
            return 'boost'
        if k in set(self.ewl.get(enemy, [])):
            return 'wl'
        return 'del' if self.checked.get((enemy, k)) else 'none'

    def toggle_global(self, k, which):
        """全局页：删 / 保护 互斥切换。"""
        if which == 'del':
            if k in self.gdel:
                self.gdel.discard(k)
            else:
                self.gdel.add(k)
                self.gwl.discard(k)
                self.boost.pop(k, None)
                self._clear_checked(k)
        else:
            if k in self.gwl:
                self.gwl.discard(k)
            else:
                self.gwl.add(k)
                self.gdel.discard(k)
                self.boost.pop(k, None)
                self._clear_checked(k)

    def _clear_checked(self, k):
        """移除某物品在所有敌人下的勾选（全局状态接管/恢复时清掉残留，
        避免 counts 虚高与预览口径失真）。"""
        for key in [key for key in self.checked if key[1] == k]:
            del self.checked[key]

    def set_prob(self, k, p):
        """设掉率：p<=0 = 删除，p>=1 = 必掉，中间任意值。"""
        import math as _math
        if not _math.isfinite(p):
            return                  # 十：nan/inf 直接拒绝
        p = min(p, 1.0)             # ：核心 API 契约自洽——UI 拦截，核心也截
        self._clear_checked(k)
        if p <= 0:
            self.boost.pop(k, None)
            self.gdel.add(k)
            self.gwl.discard(k)
        else:
            self.boost[k] = p
            self.gdel.discard(k)
            self.gwl.discard(k)

    def clear_all(self):
        """全部取消：清勾选/全局删/提率。白名单不动（那是保护层）。"""
        self.checked = {}
        self.gdel = set()
        self.boost = {}

    def counts(self):
        """状态栏计数。"""
        return {
            'del': sum(1 for v in self.checked.values() if v) + len(self.gdel),
            'boost': self.scoped_boost_count(),
            'enemy': sum(1 for v in self.checked.values() if v),
            'wl': len(self.gwl),
        }

    def preview_changes(self):
        """写盘前预览（只统计不写盘）：返回影响面统计。

        槽/敌人/保护口径与 apply_changes 完全一致——因为**就是同一份计划**。
        历史 bug 全是"预览一个数、写盘另一个数"，现在结构上不可能了。
        """
        if not self.gd:
            return None
        # 与 apply_changes / 导出 **同一份 plan**（唯一决策入口）。
        pl = self.change_plan()
        affected = set(pl.affected)
        # ⚠️ slots 的语义必须保持「将被清零的槽数」——UI 文案 preview_text 写的是
        #    "N slots will be zeroed"，且 _gate_core J1 断言它 == apply 返回的清零数。
        #    我曾把提率槽也算进 slots，导致文案虚高；J1 当时的用例 boost 为空没抓到。
        #    总数另给一个 changed 字段，两者分开，别混。
        zeroed = len(pl.delete)
        changed = len({(t, o) for t, o, _r, _k in pl.delete}
                      | {(t, o) for t, o, _a, _b, _c in pl.boost})
        shared = sum(1 for tname, _o in ([(t, o) for t, o, _r, _k in pl.delete]
                                         + [(t, o) for t, o, _a, _b, _c in pl.boost])
                     if self.table_group_count(tname) >= 5)
        return {'slots': zeroed, 'changed': changed, 'enemies': len(affected),
                'boosts': self.scoped_boost_count(),
                'protected': len(pl.skipped),
                'by_reason': pl.as_dict()['by_reason'],
                'infeasible': len(pl.infeasible),
                'shared': shared}

    # ── 写盘（已审查修复：C1-C5，含 P4 槽级保护）─────────────

    @staticmethod
    def _parser_code_fp():
        """解析器指纹：解析器源码的内容哈希。改了解析代码 → 缓存自动失效。

        为什么不能只靠 `game_data.PARSE_FMT`：
        它是**手工维护**的字符串。同一次适配里改了两次解析逻辑而标记没跟着改时，
        第二次会被上一次运行写下的缓存静默挡住 —— 表现为"改了代码但结果没变"，
        排查时极易误判成"我的改动没生效"。2026-10-30 适配 3392 时实测踩到过。
        打包后读不到源码（frozen），退回 PARSE_FMT 兜底。
        """
        import hashlib
        h = hashlib.sha1()
        h.update(game_data.PARSE_FMT.encode('utf-8'))
        if not getattr(sys, 'frozen', False):
            for _n in ('dropset.py', 'cd_paz.py', 'game_data.py'):
                try:
                    with open(os.path.join(HERE, _n), 'rb') as _fh:
                        h.update(_fh.read())
                except Exception:
                    pass
        return h.hexdigest()[:16]

    def _paz_base_expected(self):
        """版本指纹期望值。优先用**加载时记下的实际大小**（动态指纹）：
        写盘前现场读 vs 加载时读，不一致 = 会话期间游戏更新了 → 拦截。
        拿不到加载记录时回退内置常量 PAZ_BASE。
        用户在 UI 确认过「官方更新后的新版本」后用单次进程内放行值。
         ：override 改单次进程内放行——旧版写进配置永久生效，
        之后任何版本错位都会被静默放行。现在只在本次会话有效。
         ：删 cfg['paz_base_override'] 兜底分支（load_cfg
        必 pop 该键，UI 只写 _paz_override_once——旧分支是死代码 + 后门）。"""
        try:
            if getattr(self, '_paz_override_once', 0):
                return self._paz_override_once
        except Exception:
            pass
        try:
            if getattr(self, '_load_paz_size', 0):
                return self._load_paz_size
        except Exception:
            pass
        return PAZ_BASE

    def _read_ds_entry(self, game):
        """重读 0008/0.pamt，返回 dropsetinfo 条目四元组
        (paz_index, offset, comp_size, orig_size)；读不到返回 None。"""
        try:
            pamt = os.path.join(game or '', '0008', '0.pamt')
            if not os.path.exists(pamt):
                return None
            for e in cd_paz.parse_pamt(pamt):
                if e.path.endswith('gamedata/dropsetinfo.staticinfobody'):
                    return (e.paz_index, e.offset, e.comp_size, e.orig_size)
            return None
        except Exception:
            return None

    def _ds_fingerprint_ok(self, game):
        """dropsetinfo 条目级版本指纹：当前磁盘 pamt 里的条目四元组
        与加载时记录的是否一致。一致才允许写盘（读不到 = 保守拒绝）。

        直构对象（门禁假环境等未走 load_game 的场景）没有加载记录时
        降级放行——回到旧的仅 0.paz 大小检查，避免把"没加载过"
        误判成"游戏已变"。真实 UI 路径必先 load_game，记录非空，
        仍会被严格比对。"""
        want = getattr(self, '_load_ds_entry', None)
        if want is None:
            return True
        try:
            return self._read_ds_entry(game) == want
        except Exception:
            return False

    def apply_changes(self):
        """返回 (ok, message)。GUI 负责弹框和确认，这里只做计算+写盘。"""
        if not self.gd:
            return False, 'need_game'
        # 必须在方法级定义：空选择分支里那句 game= 是分支内的，而后面的
        # A-3 冲突检查 / overlay 路径都在分支外，靠它才拿得到 game。
        game = self.game_dir
        #  修复：空选择判定必须用 any(values)——UI 取消勾选是置 False
        # 不删键（cycle_drop/_clear_one 等），`not self.checked` 会把「全部
        # 取消」误判为「还有勾选」，绕过删 overlay 分支导致旧效果残留磁盘
        if not any(self.checked.values()) and not self.gdel and not self.boost:
            # -1 补全：声明式重建下，空选择 = 回到原版——备份后删 overlay。
            # （旧行为 no_sel 直接返回，取消全部修改后磁盘残留旧效果）
            game = self.game_dir
            if not game or not os.path.isdir(game):
                return False, 'no_sel'
            ov0 = os.path.join(game, OVERLAY_REL)
            if not os.path.exists(ov0):
                return False, 'no_sel'
            paz0 = os.path.join(game, '0008', '0.paz')
            if (not os.path.exists(paz0)
                    or os.path.getsize(paz0) != self._paz_base_expected()
                    or not self._ds_fingerprint_ok(game)):
                return False, ('err_game_changed',
                               os.path.getsize(paz0) if os.path.exists(paz0) else 0)
            bak0 = _backup_name(game, ov0)
            try:
                shutil.copy2(ov0, bak0)
                os.remove(ov0)
            except Exception as ex:
                return False, ('err_raw', str(ex))
            _prune_backups(game, ov0)     # ：空选择删 overlay 也修剪备份
            return True, (0, 0, 0, 0, bak0)
        # ── ①+② 删除槽：走**变更计划**（唯一决策入口）─────────────────
        # 以前这里和 preview / 导出各算一遍，改规则时漏改一处就是静默不一致。
        # 现在三处全部消费同一份 plan.build_plan 的结果（_gate_plan P1 盯住）。
        for k in list(self.boost):
            self.gdel.discard(k)     # 提率与删除互斥
        pl = self.change_plan()
        slots = [(t, o, r, k, '*') for t, o, r, k in pl.delete]
        # 计划里被跳过的槽：记日志，不静默（原因码分布一目了然）
        if pl.skipped:
            log('本次应用跳过 %d 个槽: %s'
                % (len(pl.skipped), pl.as_dict()['by_reason']))

        # 净变更：既没槽要清、也没提率 → 无事可做（旧版会照样写一份
        # 与原版相同的 overlay 并报成功，用户以为改上了）
        if not slots and not self.boost:
            return False, 'no_sel'

        # A-2 版本指纹（返回实际大小，GUI 直接格式化 %d，不再自己 getsize）
        # ——必须在**写盘之前**拦：游戏更新后指纹对不上时若照写，overlay
        #   内容会基于错误的偏移，整档掉落错乱。
        # 适配补全：dropsetinfo 在 1.paz（paz_index=1），0.paz 不变只动 1.paz
        # 的更新同样会被拦截（原实现仅查 0.paz，会漏判并基于过期偏移写盘）。
        paz0 = os.path.join(game, '0008', '0.paz')
        if (not os.path.exists(paz0)
                or os.path.getsize(paz0) != self._paz_base_expected()
                or not self._ds_fingerprint_ok(game)):
            return False, ('err_game_changed',
                           os.path.getsize(paz0) if os.path.exists(paz0) else 0)

        # A-3 其他 mod 冲突（罗盘 H-11：.DISABLED 后缀根本没禁用 mod，也要扫）
        # L5：豁免必须精确匹配自己的目录名，子串豁免会漏扫 NoJunkPlus 之类
        mbase = os.path.join(game, 'mods')
        if os.path.isdir(mbase):
            for entry in sorted(os.listdir(mbase)):
                if entry.lower() in ('nojunk', 'nojunk2'):
                    continue
                d = os.path.join(mbase, entry)
                if not os.path.isdir(d):
                    continue
                for r2, _dirs, fs in os.walk(d):
                    if 'dropsetinfo.staticinfobody' in fs:
                        return False, ('err_conflict', r2)

        ov = os.path.join(game, OVERLAY_REL)
        # -1（声明式重建）：buf 一律从原版 gd.ds 起算，不再增量读 overlay
        # ——否则「取消提率/取消删除后再应用」旧效果残留磁盘（实测确认）。
        # 配置即事实：当前勾选是什么，磁盘就是什么。
        buf = bytearray(self.gd.ds)
        # P0-1 守卫：buffer 必须容得下所有目标槽（ds 被截断/版本不符时裸
        # struct.error 会抛穿 Tk 回调，用户只看到"点了没反应"）
        need = max((off + 34 for _t, off, _r, _k, _w in slots), default=0)
        if self.boost:
            need = max(need, max((o + 34 for _k in self.boost
                                  if str(_k).lstrip('-').isdigit()  # F3：守卫必须在 int() 前求值
                                  for _t, o, _r in self.item_slots.get(int(_k), [])), default=0))
        if len(buf) < need:
            return False, ('err_raw',
                           'dropsetinfo 数据长度异常（%d < %d），请校验游戏完整性'
                           % (len(buf), need))
        for tname, off, rates, k, who in slots:
            # H1 修复：已是 0 → 幂等跳过；非 0 → 一律清零（不管是不是原值）。
            # 守卫原为「当前值==原版 rates 才清零」——提率缩放改写同表槽后，
            # 想删这些槽时守卫不成立，删除被静默跳过。
            if off + 34 <= len(buf) and struct.unpack_from('<Q', buf, off + 26)[0] != 0:
                struct.pack_into('<Q', buf, off + 26, 0)

        # ── ④ 提率：也来自同一份计划 ──────────────────────────────────
        boosted = []
        for tname, off, old_r, new_r, k in pl.boost:
            if off + 34 <= len(buf):
                struct.pack_into('<Q', buf, off + 26, new_r)
            if k not in ('*',) and not k.endswith('#dup'):
                boosted.append((k, tname, self.boost.get(k), new_r))
        # 不可行有**两类成因**，不要以为只有「Σp>100%」一种：
        #   ① 目标是该表唯一掉落物（概率恒 100%，p<1 不可达）
        #   ② Σp>100% / others 预算撑不出目标占比
        # 真实存档 1140 张表里 631 张是单槽表，用户设 50% 也会被判不可行 ——
        # 成因清单以 plan.py 的 INFEAS_* 常量为准，这里只透出、不解读。
        if pl.infeasible:
            for tname, tg in pl.infeasible:
                log('提率不可达，整表跳过: 表=%s 目标=%s' % (tname, tg))
            self._boost_infeasible = pl.infeasible
            # 成因是纯增量字段：plan 侧没有时退化成空，不改变任何既有行为。
            # 取值走 getattr（plan 侧还没跟上时不 AttributeError），并 dict() 拷一份
            # ——plan 每次 change_plan() 都重建，而 UI 读的是这里的快照，
            #   拷一份避免两者产生别名耦合。
            self._boost_infeasible_reason = \
                dict(getattr(pl, 'infeasible_reason', {}) or {})
            # blockers：占住额度的满权重槽属于哪些物品（UI 要告诉用户
            # "是它们占住了额度"）。plan 侧按占额度降序给出。
            # ⚠️⚠️ 这个降序**不表达"哪个更该删"的优先级**——实测（，
            #    1148 个 blocker 槽）：权重几乎恒等于 W，1140 张表里只有 1 张的
            #    顺序会因此不同，其余每个 blocker 的累计权重都恰好是 1×W。
            #    ⇒ **删掉其中任意一个，对可行性的贡献是一样的**；
            #      排序只决定"名字列表里显示哪几个"（UI 取前 3），没有语义。
            #    ⇒ 半年后看到"列表第一个 + 降序"别读成"最该删的就是第一个"，
            #      也不要照此写"建议优先删除第一个"这类引导文案。
            #    保留降序本身是有用的：保证同名列表稳定、可复现。
            # 同 reason 一样走 getattr 兜底 + 拷一份（浅拷贝：值是 key 列表，
            # plan 每次重建、消费方只读，无需深拷）。
            # UI 只取「空 / 非空」这一个信号做 SUM_OVER_100 的 (a)/(b) 分流：
            # 非空 = 表内确有满权重槽占住额度；空 = 只能是你自己设的几项
            # 加总 >100%。两者可同时成立（两句都展示）。
            # 物品名字未渲染：点名需拆成四段独立文案 —— 存在「Σp>1 且
            # blockers 非空」的组合，二选一版本会诱导用户删完仍然失败。
            # 字段里的 key 保留供将来启用：名字走 gd.item_name()，启用时
            # 必须说明代价（这些是游戏原版必定掉落物，删后不再必掉）。
            self._boost_infeasible_blockers = \
                dict(getattr(pl, 'infeasible_blockers', {}) or {})
        else:
            self._boost_infeasible = []
            self._boost_infeasible_reason = {}
            # 必须跟着清空：不清会残留上一轮的 blockers，串到（跨轮污染）
            self._boost_infeasible_blockers = {}

        #  ：槽头自检——size 校验只防换包，
        # 防不了"包没换但内部结构位移"（游戏更新最常见形态）。写盘前逐目标槽
        # 验证 buf 里确实是该物品的槽；偏移一歪立刻拦下，不静默写坏。
        #  ：提率槽本来**不在自检范围内**（slots 只含删除槽），
        # 于是"偏移漂移 + 提率"是静默写坏：目标槽没生效、旁边无关物品被改成必掉，
        # 而 apply_changes 照样返回 True。同一风险删除路径有保护、提率没有。
        # 这里把提率槽并进自检。
        # pl.boost 的 key 有三种形态，必须分开对待：
        #   · '123'        提率目标 —— 有真实 key，纳入自检
        #   · '123#dup'    重复槽清零 —— **剥掉后缀就是真实 key**，也要纳入自检
        #     （初版用 k.isdigit() 直接过滤，把它和 '*' 一起跳过了，实测
        #      "目标槽正常、仅重复槽错位" 场景下照样写盘，静默清掉无关物品）
        #   · '*'          others 缩放占位 —— 生成时就没保留 key（plan.py 按
        #      offset 收集 others），无法比对，只能跳过（已知残留缺口）
        # 第 5 位标记用 'b'（来自 boost 路径）：删除路径第 5 位的 '*' 是 who 标记，
        # 与这里的 '*'（others 占位）同形不同义，不能混用。
        def _bk(_kk):
            """boost 条目的 key → int；拿不到真实 key（'*'）返回 None。"""
            if _kk.endswith('#dup'):
                _kk = _kk[:-4]
            return int(_kk) if _kk.isdigit() else None

        _boost_slots = []
        for _bt, _bo, _bor, _bnr, _bkz in pl.boost:
            _bik = _bk(_bkz)
            if _bik is not None:
                _boost_slots.append((_bt, _bo, None, str(_bik), 'b'))
        for _tn, _off, _r, _k, _who in slots + _boost_slots:
            if _off + 5 > len(buf) or buf[_off] != 0x01 \
                    or struct.unpack_from('<I', buf, _off + 1)[0] != int(_k):
                log('槽头自检失败: %s off=%d key=%s' % (_tn, _off, _k))
                return False, ('err_ds_mismatch', '%s @%d' % (_tn, _off))

        #  ：净变更判定——boost 全失效（key 不在任何表）
        # 时 slots/boosted 都为空，但旧版照样写出与原版相同的 overlay 并报成功
        if not slots and not boosted:
            return False, 'no_sel'

        # 备份 + 原子写 + 回读（必须 try：只读目录/PF 下会 PermissionError）
        # ：tmp 带 pid——双实例并发写时固定名会被互相截断出嵌合 overlay
        tmp = ov + '.tmp%d' % os.getpid()
        try:
            os.makedirs(os.path.dirname(ov), exist_ok=True)
            bak = None
            if os.path.exists(ov):
                # L3：同秒两次写盘备份名碰撞会覆盖上一个恢复点，撞名追加序号
                # 1.0.3：备份移出 files\，落到 mods/NoJunk/backup/
                bak = _backup_name(game, ov)
                shutil.copy2(ov, bak)
            with open(tmp, 'wb') as f:
                f.write(buf)
            os.replace(tmp, ov)
        except PermissionError:
            log('apply_changes 写盘被拒（权限）: %s' % ov)
            return False, ('err_perm', ov)
        except Exception as ex:
            log_exc('apply_changes 写盘', ex)
            return False, ('err_raw', str(ex))
        finally:
            # L4：写盘失败时 .tmp 残留清扫
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

        # ：回读校验也在 try 内——写成功但回读失败（杀软扫描占用等）
        # 不该弹裸异常
        try:
            back = open(ov, 'rb').read()
        except Exception as ex:
            return False, ('err_raw', 'write ok but verify-read failed: %s' % ex)
        ok = sum(1 for _, off, r, _, _ in slots
                 if struct.unpack_from('<Q', back, off + 26)[0] == 0)
        okb = sum(1 for k, tname, _p, wexp in boosted
                  for tn, off, _r in self.item_slots.get(int(k), [])
                  if tn == tname and struct.unpack_from('<Q', back, off + 26)[0] == wexp)
        # 删除只清 rate、提率只改 rate，两者**都不碰** key(+1) / min(+42) /
        # max(+50)，所以这三个字段的期望值就是原版字节。
        # 旧版零校验：磁盘上这些字段被别的 MOD / 手改 / 写盘连带踩脏时，
        # 工具照样报"应用成功"，用户进游戏才发现掉的东西或数量不对。
        # 用切片比而不是 unpack——越界时切片只会变短（判为不符），
        # 不会像 struct.unpack_from 那样抛穿 Tk 回调。
        # ⚠️ 只校验 +1 这一处 key：+60 的 key 副本**不是 100% 可靠** ——
        #    真实存档 4048 个槽里有 3 个合法不一致（DropSet_GreyWolf_Camp_Join_Marius /
        #    JoinGraymane_Kliff / JoinGraymane_Damian，实测 99.93%），拿它做强校验
        #    会对这三张表永久误报（与 dropset.py 里 krep 4071/4074 同源）。
        _ro_offs = {off for _t, off, _r, _k, _w in slots}
        _ro_offs |= {o for _t, o, _or, _nr, _k in pl.boost}
        bad_ro = []
        for _o in sorted(_ro_offs):
            if back[_o + 1:_o + 5] != self.gd.ds[_o + 1:_o + 5]:
                bad_ro.append((_o, 'key'))
            elif back[_o + 42:_o + 50] != self.gd.ds[_o + 42:_o + 50]:
                bad_ro.append((_o, 'min'))
            elif back[_o + 50:_o + 58] != self.gd.ds[_o + 50:_o + 58]:
                bad_ro.append((_o, 'max'))
        log('apply_changes 完成: 删 %d 槽(回读%d) 提率 %d 键(回读%d) 备份 %s'
            % (len(slots), ok, len({k for k, _, _, _ in boosted}), okb, bak))
        _prune_backups(game, ov)
        #  ：回读不符必须拦截并回滚——旧版只统计照样报成功，
        # 用户以为改好了实际没写对。回滚到刚做的备份。
        if ok != len(slots) or okb != len(boosted) or bad_ro:
            log('!! 回读校验不符（%d/%d 删除, %d/%d 提率, key/min/max 异常 %d 槽），'
                '回滚到 %s'
                % (ok, len(slots), okb, len(boosted), len(bad_ro), bak))
            if bad_ro:
                log('!! 只读字段不符（偏移, 字段）: %s' % bad_ro[:10])
            if bak and os.path.exists(bak):
                try:
                    shutil.copy2(bak, ov)
                except Exception as ex:
                    log_exc('回读不符回滚失败', ex)
            return False, ('err_verify', (ok, len(slots), okb, len(boosted)))
        self._undo_sel_snapshot = (dict(self.checked),
                                   set(self.gdel),
                                   dict(self.boost))
        # 写盘成功后补一份 mod.json，让加载器显示的版本号跟着工具走
        # （以前手工放的那份写着 1.0.0，实际早就是 1.0.2 了）
        write_mod_info(game)
        return True, (len(slots), ok, len({k for k, _, _, _ in boosted}), okb, bak)

    def restore_vanilla(self):
        """恢复原版：删 overlay（先备份 .restorebak）。返回 (ok, msg)。"""
        log('restore_vanilla 被调用')
        if not self.gd:
            return False, 'need_game'
        ov = os.path.join(self.game_dir, OVERLAY_REL)
        if not os.path.exists(ov):
            return False, 'restore_none'
        # 1.0.3：restorebak 也移出 files\，落到 mods/NoJunk/backup/
        bak = os.path.join(_backup_dir(self.game_dir),
                           os.path.basename(ov) + '.restorebak')
        try:
            if os.path.exists(bak):
                os.remove(bak)
            shutil.copy2(ov, bak)
            os.remove(ov)
        except Exception as ex:
            return False, ('err_raw', str(ex))
        self.clear_all()
        return True, bak

    def restore_item(self, k):
        """恢复单个物品为原始掉率：清掉它的全部自定义标记并重建写盘。

        返回 (ok, msg)：
          - (False, 'need_game') 未加载游戏
          - (True, bak) 恢复完成（bak 为旧 overlay 的备份路径，可能为 None）
          - 其他失败元组与 apply_changes 一致
        """
        log('restore_item: %s' % k)
        if not self.gd:
            return False, 'need_game'
        # ：has_custom 前置守卫原来只写在 UI 的 _restore_one 里。
        # 本方法自己会「备份 + 删除 overlay」，对没有自定义项的物品调用它，
        # 走到第 ③ 段就是「备份了、overlay 删了、其实什么都没恢复」——破坏性
        # 动作的前置条件必须由被调方管，不能只靠调用方（多了入口就漏）。
        # UI 侧的判断保留（更早、提示更友好），这里作为核心不变量兜底。
        has_custom = (k in self.boost or k in self.gdel
                      or any(v for (_g, kk), v in self.checked.items()
                             if kk == k and v))
        if not has_custom:
            log('restore_item: %s 无自定义标记，拒绝写盘' % k)
            return False, 'restore_item_none'
        ov = os.path.join(self.game_dir, OVERLAY_REL)
        # P0-2 修复：版本指纹校验提前到删除 overlay 之前——旧流程「先删 ov
        # 再重建」，指纹不符（游戏更新）时 overlay 已删、其余修改全丢，
        # 用户整个 MOD 静默失效。
        paz0 = os.path.join(self.game_dir, '0008', '0.paz')
        if (not os.path.exists(paz0)
                or os.path.getsize(paz0) != self._paz_base_expected()
                or not self._ds_fingerprint_ok(self.game_dir)):
            return False, ('err_game_changed',
                           os.path.getsize(paz0) if os.path.exists(paz0) else 0)
        # 先备份当前 overlay（存在才备份；命名与 apply_changes 一致）
        # L3：同秒两次操作备份名会碰撞覆盖唯一恢复点，撞名追加序号
        # 1.0.3：备份移出 files\，落到 mods/NoJunk/backup/
        bak = None
        if os.path.exists(ov):
            bak = _backup_name(self.game_dir, ov)
            try:
                shutil.copy2(ov, bak)
            except Exception as ex:
                return False, ('err_raw', str(ex))
        # 清除该物品全部自定义标记（checked 里所有 (nm, k) 键一并移除）
        saved_boost = dict(self.boost)
        saved_gdel = set(self.gdel)
        saved_checked = dict(self.checked)
        self.boost.pop(k, None)
        self.gdel.discard(k)
        for key in [key for key in self.checked if key[1] == k]:
            self.checked.pop(key, None)
        # 没有任何剩余修改：overlay 已备份，直接删掉即整体回到原版
        if not any(self.checked.values()) and not self.gdel and not self.boost:
            try:
                if os.path.exists(ov):
                    os.remove(ov)
            except Exception as ex:
                return False, ('err_raw', str(ex))
            return True, bak
        # 还有剩余修改：删掉 overlay（已备份）后从原始 gd.ds 重建写盘，
        # 剩余所有删除/提率重新应用，本物品自然回到原始掉率。
        # P0-2：重建失败时回滚——从备份恢复 overlay 与内存状态，
        # 绝不允许「删了旧的却建不成新的」。
        try:
            if os.path.exists(ov):
                os.remove(ov)
        except Exception as ex:
            return False, ('err_raw', str(ex))
        ok, msg = self.apply_changes()
        if not ok and msg == 'no_sel':
            # 剩余修改实际只剩本物品（已清除），overlay 已删即恢复完成
            return True, bak
        if not ok:
            self.boost, self.gdel, self.checked = saved_boost, saved_gdel, saved_checked
            try:
                if bak and os.path.exists(bak) and not os.path.exists(ov):
                    shutil.copy2(bak, ov)
            except Exception:
                pass
            return ok, msg
        # 成功：apply_changes 已从原始 gd.ds 重建写盘（返回统计元组），
        # 这里统一返回本次恢复前的备份路径，供 UI 展示 restore_item_done
        if bak:
            _prune_backups(self.game_dir, ov)      # ：restore_item 也修剪备份
        return True, bak

    # ── 多管理器字节补丁导出（CDUMM / JSON Mod Manager / DMM 通吃）────

    def compute_patch_changes(self):
        """把当前选择翻译成字节补丁清单（不写游戏，纯计算，供导出 JSON）。

        返回 [(off, orig_rate, new_rate, label)]，off 为 dropsetinfo 文件内
        rates 字段的绝对偏移（槽位 off+26）。语义与 apply_changes 对齐：
        全局删除/按敌人勾选 → 0；提率 → W 且同表其他槽按原版权重缩放
        （基准为 gd.tabs 原版 rates —— 管理器补丁打在原版文件上，
        与 apply_changes 读 overlay 现场值的口径不同但效果等价）。
        """
        if not self.gd:
            return []
        # 与 apply_changes / preview **同一份 plan**，不另算一遍。
        # 基准用原版 gd.tabs（补丁打在原版文件上，这是管理器语义）；
        # plan 内部的 others 现场口径已由 _gate_plan P1 保证与写盘等价。
        pl = self.change_plan()

        # 表级 off→item_key 反查缓存：label 用，避免每槽全表遍历
        off2ik = {}

        def item_label(tname, off):
            if (tname, off) not in off2ik:
                ik = None
                for k, (o, _r) in self.gd.tabs.get(tname, {}).items():
                    if o == off:
                        ik = k
                        break
                if ik is None:
                    for (tn, k), lst in self.gd.tab_dups.items():
                        if tn != tname:
                            continue
                        for o, _r in lst:
                            if o == off:
                                ik = k
                                break
                        if ik is not None:
                            break
                off2ik[(tname, off)] = self.gd.item_name(ik) if ik is not None else '?'
            return off2ik[(tname, off)]

        changes = {}     # off -> (orig, new, label)
        for tname, off, r, k in pl.delete:
            if r > 0:
                changes[off] = (r, 0, '%s @ %s' % (item_label(tname, off), tname))
        for tname, off, old_r, new_r, k in pl.boost:
            tag = '提率' if not (k == '*' or k.endswith('#dup')) else '随表调整'
            changes[off] = (old_r, new_r,
                            '%s @ %s (%s)' % (item_label(tname, off), tname, tag))

        return sorted((off + 26, o, n, lb) for off, (o, n, lb) in changes.items())

    def export_patch_json(self, path, mod_name='NoJunk2 drops'):
        """导出字节补丁 JSON（CDUMM / JSON Mod Manager / DMM 通用格式）。
        返回 (ok, n_changes 或错误串)。不写游戏本体，只生成文件。"""
        # ：导出与写盘同源风险——偏移失效的补丁被管理器部分应用
        # 会写坏掉落文件。导出前做与 apply 相同的版本指纹校验。
        if self.game_dir:
            paz0 = os.path.join(self.game_dir, '0008', '0.paz')
            if (not os.path.exists(paz0)
                    or os.path.getsize(paz0) != self._paz_base_expected()
                    or not self._ds_fingerprint_ok(self.game_dir)):
                return False, 'err_game_changed'
        changes = self.compute_patch_changes()
        if not changes:
            return False, 'no_sel'
        patch = {
            'name': mod_name,
            'version': VERSION,
            'author': '135207 (Nexus Mods) + NoJunk2',
            'description': ('Drop removal / rate boost generated by NoJunk2. '
                            '%d byte changes on dropsetinfo.' % len(changes)),
            'allow_partial_apply': True,
            'patches': [{
                'game_file': 'gamedata/dropsetinfo.staticinfobody',
                'changes': [
                    {'offset': off,
                     'original': struct.pack('<Q', orig).hex(),
                     'patched': struct.pack('<Q', new).hex(),
                     'label': lb}
                    for off, orig, new, lb in changes
                ],
            }],
        }
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(patch, f, ensure_ascii=False, indent=1)
        except Exception as ex:
            return False, str(ex)
        return True, len(changes)

    # ── 预设（两套：按敌人 / 全局，分开存）───────────────────

    def enemy_preset_names(self):
        return sorted(self.cfg.get('presets_enemy', {}).keys())

    def global_preset_names(self):
        return sorted(self.cfg.get('presets_global', {}).keys())

    def save_enemy_preset(self, name):
        sel = {}
        for (nm, k), v in self.checked.items():
            if v:
                sel.setdefault(nm, []).append(k)
        self.cfg.setdefault('presets_enemy', {})[name] = {
            'checked': {k: sorted(v) for k, v in sel.items()},
            'enemy_wl': {k: list(v) for k, v in self.ewl.items()},
        }
        self.save_cfg()

    def _gid_targets(self, key):
        """旧键 → 现存 gid 的映射（预设套用与配置迁移共用）。

        键的历史形态有三种，全部兼容：
          1. 新 gid（'n<id>#<sig>' / 'n<id>'）：现存的直接用；
          2. 旧 gid（'<显示名>#<sig>'）：优先按 名字+签名 双条件匹配
             （M6：只按 sig 会把「盗贼#Human_Common」扩散到所有同表集的
             无关敌人，语义被偷换）；名字因切语言对不上时，仅当签名
             全库唯一才退化为纯 sig 匹配，多组命中则保守不迁移；
          3. 纯显示名：映射到同名全部组（旧版「覆盖同名全部变体」的语义）。
        都找不到（预设里残留的失效项）保留原样，避免误伤。"""
        if any(e['gid'] == key for e in self.enemies):
            return [key]
        # 0. 锚点兜底（数据重建后 sig 会变——例如 npc2tables 补了共享表——
        #    旧 gid 按锚点 NPC id 找回组：'n1000201#旧sig' → 含 1000201 的组）
        if key.startswith('n'):
            m = re.match(r'n(\d+)', key)
            if m:
                anchor = m.group(1)
                gids = [e['gid'] for e in self.enemies if anchor in e['npc_ids']]
                if gids:
                    return gids
        if '#' in key:
            nm, _sep, sig = key.partition('#')
            both = [e['gid'] for e in self.enemies
                    if e.get('sig') and e['sig'] == sig and e['name'] == nm]
            if both:
                return both
            cand = [e['gid'] for e in self.enemies
                    if e.get('sig') and e['sig'] == sig]
            if len(cand) == 1:
                return cand
        gids = [e['gid'] for e in self.enemies if e['name'] == key]
        return gids or [key]

    def _migrate_legacy_keys(self):
        """旧版本 cfg/preset 以 enemy_name 为 checked/ewl/预设键；gid 拆分后
        旧键按 name 复制到同名全部组（保持旧行为不丢数据），旧键保留以便降级。
        幂等：新键已存在时不再覆盖合并（避免重复执行越并越脏）。"""
        if not self.enemies:
            return
        targets = self._gid_targets

        dirty = False

        def merge_into(dst, src_key, val):
            """把 src_key 的列表值合并进 dst 的 gid 键（保序去重）。
            H3 修复：迁移命中后必须删除源键——旧键留在配置里的话，
            用户在新 gid 上做的任何「移除」都会在下次启动时被重新合并复活
            （复活循环：删不掉的白名单）。"""
            nonlocal dirty
            hit = False
            for gid in targets(src_key):
                if gid == src_key:
                    continue
                hit = True
                merged = list(dict.fromkeys(list(dst.get(gid, [])) + list(val)))
                if set(merged) != set(dst.get(gid, [])):
                    dst[gid] = merged
                    dirty = True
            if hit:
                dst.pop(src_key, None)
                dirty = True

        for nm, ks in list(self.cfg.get('enemy_wl', {}).items()):
            merge_into(self.cfg['enemy_wl'], nm, ks)
        for nm, ks in list(self.ewl.items()):
            merge_into(self.ewl, nm, ks)
        # checked 同样以 gid 为键（load_cfg 已按 {gid: [item_key]} 恢复），
        # 旧 gid（含显示名）必须一并迁移，否则切语言/升级后勾选静默丢失
        ck = {}
        for (g, k), v in self.checked.items():
            if not v:
                continue
            for gid in targets(g):
                ck.setdefault(gid, set()).add(k)
        new_checked = {(g, k): True for g, ks in ck.items() for k in ks}
        if set(new_checked) != {key for key, v in self.checked.items() if v}:
            self.checked = new_checked
            dirty = True
        # 磁盘上的 cfg['checked'] 也必须一并治愈 —— 只改内存不改 cfg 的话，
        # 下次启动 load_cfg 又读回旧键，迁移永远重复、落不了盘
        cfg_ck = self.cfg.get('checked')
        if isinstance(cfg_ck, dict):
            healed = {}
            for g, ks in cfg_ck.items():
                for gid in targets(g):
                    healed.setdefault(gid, set()).update(ks)
            healed = {g: sorted(s) for g, s in healed.items()}
            if healed != {g: sorted(set(ks)) for g, ks in cfg_ck.items()}:
                self.cfg['checked'] = healed
                dirty = True
        for pn, p in self.cfg.get('presets_enemy', {}).items():
            if not _preset_shape_ok('enemy', p):
                continue              # 九轮 P1：内层结构脏的预设跳过不炸
            ck = p.get('checked', {})
            for nm, ks in list(ck.items()):
                merge_into(ck, nm, ks)
            wl = p.get('enemy_wl', {})
            for nm, ks in list(wl.items()):
                merge_into(wl, nm, ks)
        if dirty:
            self.save_cfg()

    def apply_enemy_preset(self, name):
        p = self.cfg.get('presets_enemy', {}).get(name)
        if not _preset_shape_ok('enemy', p):   # 九轮 P1：内层结构也验
            return False
        self.checked = {}
        for nm, ks in p.get('checked', {}).items():
            for gid in self._gid_targets(nm):
                for k in ks:
                    self.checked[(gid, k)] = True
        self.ewl = {}
        for nm, ks in p.get('enemy_wl', {}).items():
            for gid in self._gid_targets(nm):
                self.ewl[gid] = list(ks)
        return True

    def save_global_preset(self, name):
        self.cfg.setdefault('presets_global', {})[name] = {
            'global_del': sorted(self.gdel),
            'boost': dict(self.boost),
            'price_max': self.price_max,
        }
        self.save_cfg()

    def apply_global_preset(self, name):
        p = self.cfg.get('presets_global', {}).get(name)
        if not _preset_shape_ok('global', p):   # 九轮 P1：内层结构也验
            return False
        self.gdel = set(str(x) for x in p.get('global_del', []))
        # M3：预设里的 boost 同样规范化（0→删除、负丢弃、>1 截 1）
        self.boost = {}
        for k, v in p.get('boost', {}).items():
            try:
                pv = float(v)
            except (TypeError, ValueError):
                continue
            import math as _math
            if not _math.isfinite(pv):
                continue              # 十：nan/inf 拒绝
            k = str(k)
            if pv <= 0:
                self.gdel.add(k)
            else:
                self.boost[k] = min(pv, 1.0)
        self.gdel -= set(self.boost)   # 提率与删除互斥（与 apply_changes ④ 一致）
        self.price_max = p.get('price_max', -1)
        return True

    def del_preset(self, which, name):
        key = 'presets_enemy' if which == 'enemy' else 'presets_global'
        if name in self.cfg.get(key, {}):
            self.cfg[key].pop(name, None)
            self.save_cfg()
            return True
        return False

    # ── 预设导入 / 导出（独立 JSON 文件，可随发布包/备份带走）────

    def export_presets(self, path, which='all'):
        """把 cfg 里的预设导出为独立 JSON 文件。which: 'enemy' / 'global' / 'all'。"""
        data = {}
        if which in ('all', 'enemy'):
            data['presets_enemy'] = self.cfg.get('presets_enemy', {})
        if which in ('all', 'global'):
            data['presets_global'] = self.cfg.get('presets_global', {})
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        return True

    def import_presets(self, path, which='all'):
        """从独立 JSON 文件导入预设（合并进 cfg，同名不覆盖以免误伤）。
        返回 (导入数, 导入的预设名列表, 同名跳过数)。

        八轮 P1 修复：导入前做结构校验——脏 JSON（如预设值不是 dict）落盘后，
        每次启动 _migrate_legacy_keys 都会对非 dict 值 p.get 抛 AttributeError，
        游戏永远加载失败（实测复现）。坏条目丢弃并计数，由 UI 提示。"""
        # ：原来裸 json.load(open(...))，①句柄不关；②顶层不是
        # object（如 [1,2]）时 data.get 抛 AttributeError，且异常会一路抛到
        # 调用方——核心方法不该把「读了个坏文件」变成崩溃。
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return 0, [], 0
        n, names, skipped = 0, [], 0
        bad = 0
        for key, side in (('presets_enemy', 'enemy'), ('presets_global', 'global')):
            if which not in ('all', side):
                continue
            src = data.get(key, {})
            if not isinstance(src, dict):
                bad += 1
                continue
            dst = self.cfg.setdefault(key, {})
            for name, val in src.items():
                if not isinstance(name, str) or not _preset_shape_ok(side, val):
                    bad += 1         # 结构不对（含内层字段类型错）的一律拒收
                    continue
                if name in dst:
                    skipped += 1
                    continue
                dst[name] = val
                n += 1
                names.append(name)
        self.save_cfg()
        if bad:
            try:
                if sys.stderr is not None:
                    sys.stderr.write('import_presets: %d 个结构损坏的条目已丢弃\n' % bad)
            except Exception:
                pass
        return n, names, skipped


# ── cdloader VFS 写盘后预热重建 + 收敛 ────────────────────────────
# 背景：NoJunk2 写盘会改 mods/NoJunk 指纹，下次启动加载器会全量重建，
# 重建产物含 meta/0.papgt(34B stub)，该 stub 会让游戏启动即 0xC0000005
# 崩溃。因此写盘成功后立即用 --build-only 预热重建，再把映射树里的
# meta/ 前缀条目删掉（收敛），用户下次启动直接 cache hit 且不崩。
# 实测链路（2026-10-31 已验证）：build-only 重建(0.30s) → 删 meta stub →
# 同步 vfs_state.mapped_files → 下次启动 VFS cache hit，游戏 60s 无崩溃，
# dropsetinfo(2823997B) 完整生效。
CDLOADER_VFS_EXE = 'cdloader-VFS-v9.4.7.exe'
CDLOADER_BUILD_TIMEOUT = 60.0


def auto_rebuild_and_converge(game_dir):
    """写盘成功后调用：预热重建 cdloader VFS 并收敛映射树。

    参数 game_dir: 游戏根目录绝对路径
    （如 F:\\SteamLibrary\\steamapps\\common\\Crimson Desert）。
    返回结构化 dict：
      ok              True/False
      error           失败原因（ok=False 时，已转字符串，可直接展示）
      rebuild_seconds 重建耗时（秒）
      entries_before  收敛前映射树条目数
      entries_after   收敛后映射树条目数
      exe / mapping / state  实际使用的可执行文件与修改的 JSON 路径
      loader_located  True/False：掉落表 gamedata/dropsetinfo.staticinfobody
                      target 是否被加载器成功定位（仅 ok=True 时有意义）
      loader_detail   自检说明文字（未定位时含 vfs_state warnings 旁证）
    任何失败（缺 exe / 超时 / 读不到 JSON / 返回值非 0）都不抛异常，
    统一 ok=False 返回，由调用方降级为 warning，不阻断写盘成功主流程。
    自检门禁同理：定位失败只上报，绝不回滚写盘结果。
    """
    result = {'ok': False, 'error': '', 'rebuild_seconds': 0.0,
              'entries_before': 0, 'entries_after': 0,
              'exe': '', 'mapping': '', 'state': '',
              'loader_located': None, 'loader_detail': ''}
    t0 = 0.0
    try:
        game_dir = os.path.normpath(game_dir or '')
        if not game_dir or not os.path.isdir(game_dir):
            result['error'] = 'bad_game_dir: %r' % game_dir
            return result
        # 1) 定位加载器可执行文件（优先已验证的 v9.4.7，缺失时回退
        #    .cdloader 下的 cdloader-VFS-*.exe）
        exe = os.path.join(game_dir, CDLOADER_VFS_EXE)
        if not os.path.isfile(exe):
            cands = glob.glob(os.path.join(game_dir, '.cdloader',
                                           'cdloader-VFS-*.exe'))
            if cands:
                exe = sorted(cands)[0]
            else:
                result['error'] = 'no_cdloader_exe'
                return result
        result['exe'] = exe
        cdl = os.path.join(game_dir, '.cdloader')
        mapping = os.path.join(cdl, 'vfs_mapping_tree.json')
        state = os.path.join(cdl, 'vfs_state.json')
        result['mapping'] = mapping
        result['state'] = state
        # 2) build-only 预热重建（不启动游戏，超时上限 60s）
        t0 = time.monotonic()
        proc = subprocess.run(
            [exe, '--build-only', '--game-dir', game_dir],
            capture_output=True, text=True, timeout=CDLOADER_BUILD_TIMEOUT)
        result['rebuild_seconds'] = round(time.monotonic() - t0, 2)
        if proc.returncode != 0:
            tail = ((proc.stderr or '') + (proc.stdout or '')).strip()[-300:]
            result['error'] = 'build_only_failed rc=%d: %s' % (proc.returncode, tail)
            return result
        # 3) 读重建后映射树，删除所有 meta/ 前缀条目（保留 nppsa 两条）
        if not os.path.isfile(mapping):
            result['error'] = 'no_mapping_after_build'
            return result
        with open(mapping, 'r', encoding='utf-8-sig') as f:
            tree = json.load(f)
        entries = tree.get('entries', [])
        result['entries_before'] = len(entries)
        kept = [e for e in entries
                if not (str(e.get('virtual_relative_path', '')).startswith('meta/')
                        or str(e.get('runtime_virtual_relative_path', '')).startswith('meta/'))]
        result['entries_after'] = len(kept)
        if kept != entries:
            tree['entries'] = kept
            with open(mapping, 'w', encoding='utf-8') as f:
                json.dump(tree, f, ensure_ascii=False, indent=4)
        # 4) 同步 vfs_state.json 的 mapped_files（保持 last_fingerprint /
        #    vfs_root 不变，保证下次启动 cache hit）
        if os.path.isfile(state):
            with open(state, 'r', encoding='utf-8-sig') as f:
                st = json.load(f)
            st['mapped_files'] = [e.get('virtual_relative_path')
                                  for e in kept if e.get('virtual_relative_path')]
            with open(state, 'w', encoding='utf-8') as f:
                json.dump(st, f, ensure_ascii=False, indent=4)
        # 5) 兼容性自检门禁：读取 pamt_target_cache.json 与 vfs_state.json，
        #    检测掉落表 gamedata/dropsetinfo.staticinfobody 的 target 是否被
        #    加载器成功定位。加载器 v9.4.7 无法解析新版 LEB128 pamt 索引时会
        #    把该条目写成 null，覆盖因此静默失效——这里必须在写盘后如实暴露。
        #    自检失败只写入 result 供 UI 警告，绝不回滚或阻断写盘成功流程。
        result['loader_located'] = True
        result['loader_detail'] = ''
        cache = os.path.join(cdl, 'pamt_target_cache.json')
        located, detail = None, ''
        try:
            if os.path.isfile(cache):
                with open(cache, 'r', encoding='utf-8-sig') as f:
                    pc = json.load(f)
                targets = pc.get('targets') if isinstance(pc, dict) else None
                if isinstance(targets, dict):
                    hit = None
                    for k, v in targets.items():
                        if k.endswith('|gamedata/dropsetinfo.staticinfobody'):
                            hit = (k, v)
                            break
                    if hit is None:
                        located, detail = False, 'pamt_target_cache 中未找到掉落表条目 gamedata/dropsetinfo.staticinfobody'
                    elif hit[1] is None:
                        located, detail = False, '掉落表 target=null（cdloader 未定位，索引格式不兼容）'
                    elif isinstance(hit[1], dict) and hit[1].get('target'):
                        located, detail = True, '掉落表已定位（%s）' % hit[1].get('target')
                    else:
                        located, detail = False, '掉落表 target 为空值：%r' % (hit[1],)
                else:
                    located, detail = False, 'pamt_target_cache 缺少 targets 字段'
            else:
                located, detail = False, 'pamt_target_cache.json 不存在（加载器未生成缓存）'
            if located is False:
                # 附加上 vfs_state 的警告作为旁证（如 "未在 0008/0.pamt 中找到"）
                if os.path.isfile(state):
                    with open(state, 'r', encoding='utf-8-sig') as f:
                        vst = json.load(f)
                    warns = vst.get('warnings') if isinstance(vst, dict) else None
                    if isinstance(warns, list) and warns:
                        detail += '；vfs_state warnings: %s' % ' / '.join(
                            str(w) for w in warns)
        except Exception as ex:
            located, detail = False, '自检异常：%s' % ex
        result['loader_located'] = located
        result['loader_detail'] = detail
        result['ok'] = True
        return result
    except subprocess.TimeoutExpired:
        result['error'] = 'build_only_timeout > %.0fs' % CDLOADER_BUILD_TIMEOUT
        result['rebuild_seconds'] = round(time.monotonic() - t0, 2)
        return result
    except Exception as ex:
        result['error'] = '%s' % ex
        return result
