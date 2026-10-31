# -*- coding: utf-8 -*-
"""game_data.py —— 掉落表与名库的容器（门面层）。

解析逻辑已迁到 `dropset.py`（领域模型，带 64 字节三重自检）。
本文件保留 `GameData` 作为对外门面：**行为与迁移前一致**，已逐槽对拍验证
（`_review/v33_both_paths.py`：两条路径槽集合零差异）。

为什么要分家：
  · 解析是"知识域"（槽布局、官方索引、A/B 分区）——最易随游戏改版失效，
    需要独立的自检与对拍测试。
  · 容器是"组装"（读哪些文件、装名库、给敌人视图）——稳定，不需要常动。
  混在一起时，每次动解析都要在容器里翻找，改动容易漏。

对外 API 与迁移前完全一致，调用方无需改动。
"""
import sys, io, os, json, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cd_paz
import dropset

# ── 从 dropset 转出，保持旧引用可用 ──────────────────────────────
A_END = 377350          # 降级路径的边界（经验值；新版 v2 路径不使用）
SLOT_STEP = 37          # 旧步长启发式（新解析用 64 定长步距，此值仅作档案保留）
# v2：2026-10-30 适配游戏 1.0.0.3392。dropset.py 换成新版记录布局
#   （name_len@+0 / name@+4 / 槽区起点 25+n / 尾部 32B）+ 官方索引全量定位。
# ⚠️ 换解析器就必须改这个标记 —— 否则缓存命中旧结果、新解析器永远跑不到。
#   冒烟测试实测过：标记没改时，加载后拿到的还是旧的 1131 张表。
PARSE_FMT = 'v2'        # 缓存格式标记（解析方式一变，缓存自动失效）
KEY_MAX = dropset.KEY_MAX
RATE_MAX = dropset.RATE_MAX


def _warn(msg):
    """stderr 告警。

    打包版（pythonw / hide_console）下 sys.stderr 可能是 None，裸 write 会
    AttributeError 崩掉整个调用——统一走这里，且吞掉一切异常。
    """
    try:
        if sys.stderr is not None:
            sys.stderr.write(msg + '\n')
    except Exception:
        pass


class GameData:
    """掉落表 + 名库的容器。

    类级默认值不是为了省事：多处用 `GameData.__new__` + 手工塞 ds 的方式
    构造对象（_gate_parse / _gate_e2e / _gate_realdata / build_npc2tables_local
    都是），那些实例不走 __init__，没有类级默认就会 AttributeError 崩启动。
    给默认值比逐个改调用方稳——漏一个调用方就是一次崩溃。
    """
    hdr = b''            # 官方索引内容；空 = 走降级路径
    entries = []
    diag = {}            # 最近一次解析的自检诊断（供日志/自检报告用）

    def __init__(self, game_dir, npc_db=None, lang_group='0032'):
        self.game_dir = game_dir
        self.tab_dups = {}
        G8 = os.path.join(game_dir, '0008')

        self.entries = cd_paz.parse_pamt(os.path.join(G8, '0.pamt'))
        self.ds = cd_paz.read_paz_file(
            self.entries, 'gamedata/dropsetinfo.staticinfobody', G8)

        self.hdr = b''
        try:
            self.hdr = cd_paz.read_paz_file(
                self.entries, 'gamedata/dropsetinfo.staticinfoheader', G8)
        except Exception as ex:
            _warn('官方索引读取失败，将走降级路径: %s' % ex)

        self.tabs = self._parse_tables()

        self.names = {}
        try:
            Gl = os.path.join(game_dir, lang_group)
            es_l = cd_paz.parse_pamt(os.path.join(Gl, '0.pamt'))
            for fname in ('character.paloc', 'item.paloc'):
                pd = cd_paz.read_paz_file(es_l, 'gamedata/' + fname, Gl)
                self.names.update(self._scan_names(pd))
        except Exception as ex:
            _warn('paloc 名库加载失败: %s' % ex)

        self.qlog = npc_db or {}

    @classmethod
    def from_cache(cls, game_dir, tabs, names, ds, tab_dups=None):
        """从缓存重建：跳过 pamt/paz 解析，直接装入已解析产物。"""
        obj = cls.__new__(cls)
        obj.game_dir = game_dir
        obj.entries = []
        obj.hdr = b''
        obj.ds = ds
        obj.tabs = tabs
        obj.names = names
        obj.qlog = {}
        obj.tab_dups = tab_dups or {}
        obj.diag = {}
        return obj

    # ── 解析：委托 dropset ───────────────────────────────────────
    def _index_records(self):
        """（兼容旧接口）官方索引 → (A 区记录, A 区结束偏移)。"""
        return dropset.index_records(self.ds, self.hdr)

    def _parse_tables(self):
        """解析掉落表。委托 dropset，并把自检诊断留在 self.diag。"""
        tabs, dups, diag = dropset.parse_tables(self.ds, self.hdr, strict=True)
        self.tab_dups = dups
        self.diag = diag
        for w in diag.get('warn') or []:
            _warn('⚠️ ' + w)
        if self.hdr and diag.get('mode') == 'legacy':
            _warn('⚠️ 官方索引格式不符（%d 字节），已走降级路径：'
                  '使用写死的 A_END=%d，已知会漏表，请重新逆向索引格式'
                  % (len(self.hdr), A_END))
        return tabs

    def _parse_tables_legacy(self):
        """（兼容旧接口）强制走降级路径。"""
        tabs, dups, diag = dropset.parse_tables(self.ds, b'', strict=True)
        self.tab_dups = dups
        self.diag = diag
        return tabs

    # ── 名库 ────────────────────────────────────────────────────
    def _scan_names(self, pd):
        """从 paloc 字节流里抽「数字 key → 显示名」对。

        三种字符集各一条正则：中文/韩文按 UTF-8 首字节区间，英文按 ASCII。
        key 形如 9~20 位数字，名字紧跟其后（允许最多 16 字节的元数据间隔）。

        已知边界：key 后若紧跟字母类元数据，会被 ASCII 正则一起吞进名字。
        评估过更严的正则 `(?=[^0-9A-Za-z]|$)`，会丢掉 466 个合法名字，
        代价远大于它修的问题（那 650 个垃圾 key 本就不会被查询）。维持现状。
        """
        EN_RE = re.compile(rb"[A-Za-z][A-Za-z0-9 _':\-.()]{2,39}")
        ZH_RE = re.compile(rb'(?:[\xe4-\xe9][\x80-\xbf]{2}){2,20}')
        KO_RE = re.compile(rb'(?:[\xea-\xed][\x80-\xbf]{2}){2,20}')
        KEY_RE = re.compile(rb'[0-9]{9,20}(?![0-9])')

        out = {}
        i = 0
        while i < len(pd) - 12:
            if 48 <= pd[i] <= 57:
                m = KEY_RE.match(pd[i:])
                if m:
                    ke = i + len(m.group())
                    nm = (ZH_RE.search(pd[ke:ke + 28])
                          or KO_RE.search(pd[ke:ke + 28])
                          or EN_RE.search(pd[ke:ke + 28]))
                    if nm and nm.start() <= 16:
                        try:
                            out[m.group().decode()] = nm.group().decode('utf-8')
                            i = ke + nm.end()
                            continue
                        except UnicodeDecodeError:
                            pass
            i += 1
        return out

    # ── 查询 ────────────────────────────────────────────────────
    def slots_of(self, tname, ik):
        """该物品在此表的全部槽（含同表重复槽）→ [(off, rates), ...]。"""
        out = []
        main = self.tabs.get(tname, {}).get(ik)
        if main:
            out.append(main)
        out.extend(self.tab_dups.get((tname, ik), []))
        return out

    def item_name(self, key, fallback=''):
        return self.names.get(str(key), fallback or ('item_%s' % key))

    def enemies(self):
        """敌人维度展示视图：NPC 掉落库 + 物品名。按名字排序。"""
        out = []
        for nid, n in self.qlog.items():
            drops = []
            for d in n['drops']:
                k = str(d['id'])
                drops.append({'id': k,
                              'name': d.get('name') or self.item_name(k),
                              'p': d.get('p'), 'q': d.get('q'),
                              'type': d.get('type')})
            out.append({'id': nid, 'name': n['name'], 'grade': n.get('grade'),
                        'drops': drops})
        out.sort(key=lambda x: x['name'])
        return out


if __name__ == '__main__':
    if sys.stdout is not None and hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                                      errors='replace')
    G = r'F:/SteamLibrary/steamapps/common/Crimson Desert'
    with open(r'D:/新建文件夹/_nojunk_snap/npc_drops_zh.json', encoding='utf-8') as _f:
        qlog = json.load(_f)
    gd = GameData(G, qlog)
    _warn('表: %d 张 / 名库: %d 条 / 敌人: %d'
          % (len(gd.tabs), len(gd.names), len(qlog)))
    _warn('解析自检: %s' % gd.diag)
    for nid in ('1000201', '1000191', '1000018', '1000042'):
        n = qlog.get(nid)
        if not n:
            continue
        _warn('=== %s (%s) 掉%d种 ===' % (n['name'], nid, len(n['drops'])))
        for d in n['drops'][:10]:
            _warn('   %-8s %-14s p=%-6s %s-%s %s' % (
                d['id'], (d.get('name') or '')[:14], d.get('p'),
                d.get('minQuantity'), d.get('maxQuantity'), d.get('dropType')))
