# -*- coding: utf-8 -*-
"""dropset.py —— 掉落表领域模型（解析 + 槽 + 作用域规则）。只读。

职责：把 dropsetinfo 的字节变成「表 / 槽」，并回答"这张表算不算掉落物"。
输入：dropsetinfo 原始字节 + 官方索引字节
输出：{表名: {物品key: (off, rate)}} + 重复槽 + 自检计数
不做：不碰文件、不碰 UI、不决定删不删（那是变更计划的事）

## 两套布局：旧版 vs 新版(1.0.0.3392)

### 新版记录（实测 2026-10-30，1.0.0.3392）
```
+0        u32 name_len
+4        name[name_len]                  ★ 旧版在 +8，这是最致命的一处差 4
+4+n      u32 dropset_key
+8+n      u8 0
+9+n      u8 0
+10+n     u8 变体
+25+n     槽区起点（每槽 64B，定长）
…
尾部固定 32B
槽数 = (记录长 - 25 - n - 32) / 64
```
实测 1,186 张 DropSet_ 表记录里 1,149 张整除（97%），
且槽自检 `ff ff@+58` 与 `item_key 重复@+60` 双双 **100% 命中**。

### 槽布局（64 字节，新旧版**一致**，旧代码的常量是对的）
```
+0   01
+1   u32 item_key
+5   4×00
+9   u32 数量
+13  13×00
+26  u64 rate
+34  u64 0
+42  u64 最小数量
+50  u64 最大数量
+58  ff ff        ← 自检 2
+60  u32 item_key 重复  ← 自检 3（必须等于 +1）
```
自检 1 = 头部特征（01 + 两段 00 成片）。
三处独立校验：实测 A 区 4074 个槽里 ff 命中 4074/4074、key 重复一致 4071/4074。
用上它们之后，游戏改版动了槽布局会**当场被发现**，而不是静默解析错。

## 自检纪律（血泪教训，别自作聪明）
- **100% 命中的特征才能当过滤器**（本版：`ff ff`、槽数整除性）
- **低于 100% 的只做报告**（`item_key` 重复在旧版是 4071/4074，
  那 3 个例外经查是**合法槽**，拿它当过滤器会静默丢真槽）
"""
import struct
from collections import namedtuple

# ── 槽结构常量（新旧版一致）──────────────────────────────────
SLOT_SIZE = 64
SLOT_KEY_OFF = 1
SLOT_QTY_OFF = 9
SLOT_RATE_OFF = 26
SLOT_MIN_OFF = 42
SLOT_MAX_OFF = 50
SLOT_FF_OFF = 58
SLOT_KEY2_OFF = 60

SLOT_GAP1 = (5, 9)      # 4 个 00
SLOT_GAP2 = (13, 26)    # 13 个 00

# ── 新版记录布局（1.0.0.3392 实测）────────────────────────────
V2_NAME_OFF = 4         # name 紧跟 name_len（旧版是 8）
V2_SLOT_BASE = 25       # 槽区起点 = 25 + name_len
V2_TAIL = 32            # 记录尾部固定 32B
V2_ALIGN_MIN = 0.90     # 整除率低于此值 → 判定不是新版，回退旧版解析

# ── 合理性上下限 ────────────────────────────────────────────────
# 上限定 0x8000_0000 的依据（docs/M9-B区归因.md）：
#   普通物品 key < 3,000,000，但 Knowledge / Mercenary 类 key 落在 0x7fffffxx 高位段。
#   旧代码用 3,000,000 卡上限，实测把 A 区 51 个 Knowledge 槽静默丢弃 —— 用户删不掉
#   且毫无提示。已修。
# 下限定 1（不收 0）：key=0 的槽出现在 DropSet_Friendly_Donate/Talk/Threat，是占位
#   而非真实物品，收进来只会多出 item_0 这种垃圾条目。
KEY_MAX = 0x8000_0000
RATE_MAX = 10 ** 8

# ── 官方索引布局 ────────────────────────────────────────────────
# u16 记录总数 + 每条 8B (u32 dropset_key, u32 body_offset)，按 offset 递增
IDX_COUNT_OFF = 0
IDX_REC_SIZE = 8
IDX_BODY_OFF = 4        # 一条记录内 body_offset 的位置

# ── 记录头布局 ──────────────────────────────────────────────────
REC_KEY_OFF = 0
REC_NAMELEN_OFF = 4
REC_NAME_OFF = 4        # 新版；旧版为 8，record_name() 会自动两种都试
REC_NAME_OFF_LEGACY = 8


def _slot_head_ok(b, i):
    """自检 1：头部特征。"""
    if b[i] != 0x01:
        return False
    a, c = SLOT_GAP1
    if b[i + a:i + c] != b'\x00' * (c - a):
        return False
    p, q = SLOT_GAP2
    return b[i + p:i + q] == b'\x00' * (q - p)


def record_name(ds, off):
    """取 off 处记录的表名（ASCII）；取不到返回 None。

    两版记录头的字段顺序**整个错开 4 字节**，name_len 与 name 的位置都不同：
        新版(1.0.0.3392): name_len @+0, name @+4, dropset_key @+4+n
        旧版:             key @+0,      name_len @+4, name @+8
    两种都试，谁先解出合法 ASCII 名字用谁。
    写死任何一个都会在换版时**静默**解析出 0 张表 —— 本次排障时正是这样：
    0 表 0 槽，日志里只有一行 warn，看起来像"游戏没数据"而不是"格式变了"。
    """
    if off + 8 > len(ds):
        return None
    for nl_off, nm_off in ((0, REC_NAME_OFF), (REC_NAMELEN_OFF, REC_NAME_OFF_LEGACY)):
        nl = struct.unpack_from('<I', ds, off + nl_off)[0]
        if not (1 <= nl <= 200):
            continue
        if off + nm_off + nl > len(ds):
            continue
        s = ds[off + nm_off:off + nm_off + nl]
        if not all(32 <= c < 127 for c in s):
            continue
        return s.decode('ascii')
    return None


def index_all(ds, hdr):
    """官方索引 → 全量 [(key, off, end)]。索引不可用/格式不符返回 None。

    这是**全量定位器**：官方索引直接给出每条记录在 body 里的精确起点，
    彻底不需要"顺序扫描找 DropSet_"那种脆弱代码。
    """
    if not hdr:
        return None
    try:
        n = struct.unpack_from('<H', hdr, IDX_COUNT_OFF)[0]
        if not (0 < n <= 0xFFFF) or 2 + IDX_REC_SIZE * n > len(hdr):
            return None
        pairs = [struct.unpack_from('<II', hdr, 2 + IDX_REC_SIZE * i)
                 for i in range(n)]
        pairs.sort(key=lambda x: x[1])
        if not all(pairs[i][1] < pairs[i + 1][1] for i in range(n - 1)):
            return None
        if not all(0 <= o < len(ds) for _k, o in pairs):
            return None
        out = []
        for i, (k, o) in enumerate(pairs):
            end = pairs[i + 1][1] if i + 1 < n else len(ds)
            out.append((k, o, end))
        return out
    except Exception:
        return None


def v2_slots(raw, name_len, base_off):
    """新版：槽区起点 25+n，64B 定长，槽数 = (len - 25 - n - 32)/64。

    返回 (槽列表, aligned, stat)
      aligned=False → 长度不整除，该表不是标准新版布局（调用方应兜底）
      stat          → 自检统计（head/ff/krep/range/break）
    """
    stat = {'head': 0, 'ff': 0, 'krep': 0, 'range': 0, 'break': 0}
    rem = len(raw) - V2_SLOT_BASE - name_len - V2_TAIL
    if rem < 0 or rem % SLOT_SIZE:
        return [], False, stat
    cnt = rem // SLOT_SIZE
    start = V2_SLOT_BASE + name_len
    out = []
    for j in range(cnt):
        i = start + SLOT_SIZE * j
        if i + SLOT_SIZE > len(raw):
            break
        ok_head = raw[i] == 0x01
        ok_ff = raw[i + SLOT_FF_OFF:i + SLOT_FF_OFF + 2] == b'\xff\xff'
        k1 = struct.unpack_from('<I', raw, i + SLOT_KEY_OFF)[0]
        k2 = struct.unpack_from('<I', raw, i + SLOT_KEY2_OFF)[0]
        rate = struct.unpack_from('<Q', raw, i + SLOT_RATE_OFF)[0]
        if ok_head:
            stat['head'] += 1
        if ok_ff:
            stat['ff'] += 1
        if k1 == k2:
            stat['krep'] += 1
        if 0 < k1 < KEY_MAX and 0 < rate < RATE_MAX:
            stat['range'] += 1
        # 100% 命中的特征才当过滤器；一旦不过就停在最后一个好槽，不往后猜。
        # ⚠️ break 必须带 why：否则一张表若在**第一个**槽就 break，
        #    结果是"0 槽"却没有任何告警 —— 又是一次静默降级。
        if not (ok_head and ok_ff):
            stat['break'] += 1
            stat['why'] = 'head!=01' if not ok_head else 'ff-fail'
            break
        if not (0 < k1 < KEY_MAX and 0 < rate < RATE_MAX):
            stat['break'] += 1
            stat['why'] = 'key=%d/rate=%d 越界' % (k1, rate)
            break
        out.append({
            'key': k1, 'off': base_off + i, 'rate': rate,
            'qty': struct.unpack_from('<I', raw, i + SLOT_QTY_OFF)[0],
            'qty_min': struct.unpack_from('<Q', raw, i + SLOT_MIN_OFF)[0],
            'qty_max': struct.unpack_from('<Q', raw, i + SLOT_MAX_OFF)[0],
        })
    return out, True, stat


def scan_slots(b, start, end, strict=True):
    """在 [start, end) 内扫槽。返回 (槽列表, 自检统计)。

    strict=True  → 额外要求 `ff ff` 标记（主路径，推荐）
    strict=False → 只要头部特征 + 合理性（降级用）

    ⚠️ 为什么只把 ff ff 当过滤器，不把 key 重复也当过滤器（实测结论）：
       · ff ff        → A 区 4074/4074 全命中，可以放心当过滤器
       · key 重复一致 → 4071/4074，那 3 个例外经查是**合法槽**
         （DropSet_GreyWolf_Camp_Join_Marius / DropSet_JoinGraymane_Damian /
          DropSet_JoinGraymane_Kliff），若拿它当过滤器会静默丢掉真槽。
       教训：**用 100% 命中的特征做过滤，用非 100% 的特征做报告。**

    新版只在"长度不整除"的少数表上用本函数兜底；主路径走 v2_slots 定长步进。
    """
    if end <= start or end > len(b) or start < 0:
        return [], {'head': 0, 'ff': 0, 'krep': 0, 'range': 0, 'break': 0}
    stat = {'head': 0, 'ff': 0, 'krep': 0, 'range': 0, 'break': 0}
    out = []
    i = start
    while i + SLOT_SIZE <= end:
        if not _slot_head_ok(b, i):
            i += 1
            continue
        stat['head'] += 1
        ok_ff = b[i + SLOT_FF_OFF:i + SLOT_FF_OFF + 2] == b'\xff\xff'
        k1 = struct.unpack_from('<I', b, i + SLOT_KEY_OFF)[0]
        k2 = struct.unpack_from('<I', b, i + SLOT_KEY2_OFF)[0]
        ok_krep = k1 == k2
        if ok_ff:
            stat['ff'] += 1
        if ok_krep:
            stat['krep'] += 1
        rate = struct.unpack_from('<Q', b, i + SLOT_RATE_OFF)[0]
        ok_range = 0 < k1 < KEY_MAX and 0 < rate < RATE_MAX
        if ok_range:
            stat['range'] += 1
        take = ok_range and (not strict or ok_ff)
        if take:
            out.append({
                'key': k1, 'off': i, 'rate': rate,
                'qty': struct.unpack_from('<I', b, i + SLOT_QTY_OFF)[0],
                'qty_min': struct.unpack_from('<Q', b, i + SLOT_MIN_OFF)[0],
                'qty_max': struct.unpack_from('<Q', b, i + SLOT_MAX_OFF)[0],
            })
            i += SLOT_SIZE          # 命中就按已知步距跳，不在槽体内部找假头
            continue
        i += 1
    return out, stat


def index_records(ds, hdr):
    """（兼容旧接口）官方索引 → (全量记录 [(key, offset)], body 长度)。

    ⚠️ 旧语义是「A 区记录 + A 区结束偏移」，靠 `'(' in name` 分区。
    新版根本没有 A/B 分区概念，那个判据只会静默丢掉 92% 的记录。
    现在返回**全量**记录，a_end 退化为 body 长度（保留字段只为兼容调用方）。
    """
    bounds = index_all(ds, hdr)
    if bounds is None:
        return None
    return [(k, o) for k, o, _e in bounds], len(ds)


def _absorb(tabs, dups, name, slots):
    tab = tabs.setdefault(name, {})
    for s in slots:
        if s['key'] in tab:
            dups.setdefault((name, s['key']), []).append((s['off'], s['rate']))
        else:
            tab[s['key']] = (s['off'], s['rate'])


def _parse_v2(ds, bounds, strict=True):
    """新版解析：官方索引切记录 → 只认 DropSet_* 表 → v2_slots 定长步进。"""
    diag = {'mode': 'v2', 'strict': strict, 'head': 0, 'ff': 0, 'krep': 0,
            'range': 0, 'n_rec': len(bounds), 'n_tbl': 0, 'aligned': 0,
            'unaligned': 0, 'break': 0, 'warn': []}
    tabs, dups = {}, {}
    for _k, o, end in bounds:
        name = record_name(ds, o)
        if not name:
            continue
        # 令牌记录（Item(...) / Knowledge(...) / Mercenary(...) …）不是表。
        # ⚠️ 判据只能是「名字里有没有 '('」，不能写成 startswith('DropSet_')：
        # 实测有 23 条表记录不以 DropSet_ 开头（DropSetRaging_Tempests_Common
        # 少了下划线，还有 Purchase_Reward_* / Drops_Reward_* / Drop_Trade_*）。
        # 用前缀过滤会静默漏掉它们 —— 而这些表正是要靠 SCOPE_RULES 判定
        # "不是掉落物"的对象：表都没解析出来，作用域判断根本无从谈起。
        if '(' in name:
            continue
        diag['n_tbl'] += 1
        raw = ds[o:end]
        slots, aligned, st = v2_slots(raw, len(name), o)
        if aligned:
            diag['aligned'] += 1
        else:
            # 少数表长度不整除（实测 37/1186）：退回扫描兜底，宁可少收也不静默丢表
            diag['unaligned'] += 1
            slots, st = scan_slots(ds, o + V2_SLOT_BASE + len(name), end,
                                   strict=strict)
        for kk in ('head', 'ff', 'krep', 'range', 'break'):
            diag[kk] += st.get(kk, 0)
        if st.get('break'):
            # 槽自检中断必须显式进 warn：这是"表解析了一半"的唯一可见信号，
            # 否则用户只会看到某些掉落莫名消失，日志里什么都查不到。
            diag['warn'].append(
                '%s：第 %d 槽自检未过（%s），已停在上一个好槽，本表可能少收槽'
                % (name, len(slots) + 1, st.get('why', '?')))
        _absorb(tabs, dups, name, slots)
    if not tabs:
        diag['warn'].append('新版路径解析出 0 张表')
    return tabs, dups, diag


def parse_tables(ds, hdr, strict=True):
    """解析掉落表。返回 (tabs, dups, diag)

    tabs: {表名: {key: (off, rate)}}
    dups: {(表名, key): [(off, rate)]}  同表重复槽
    diag: 自检与降级诊断（供日志/报告用）

    分派：先按新版布局试，整除率达标才采信；否则回退旧版扫描路径。
    """
    bounds = index_all(ds, hdr)
    v2warn = []
    if bounds is not None:
        tabs, dups, diag = _parse_v2(ds, bounds, strict=strict)
        ratio = (diag['aligned'] / float(diag['n_tbl'])) if diag['n_tbl'] else 0.0
        diag['align_ratio'] = ratio
        if ratio >= V2_ALIGN_MIN and tabs:
            return tabs, dups, diag
        # 回退时把新版路径的诊断带上，否则排障时看不到"新版为什么没被采信"
        v2warn = list(diag.get('warn') or [])
        v2warn.append(
            '新版结构自检未达标（整除率 %.2f < %.2f，表 %d 张），回退旧版解析'
            % (ratio, V2_ALIGN_MIN, diag['n_tbl']))

    diag = {'mode': 'legacy', 'strict': strict,
            'head': 0, 'ff': 0, 'krep': 0, 'range': 0, 'break': 0, 'warn': v2warn}
    tabs, dups = {}, {}
    idx = index_records(ds, hdr)
    if idx is not None:
        recs, a_end = idx
        for i, (_k, o) in enumerate(recs):
            name = record_name(ds, o)
            if not name:
                continue
            nxt = recs[i + 1][1] if i + 1 < len(recs) else a_end
            start = o + REC_NAME_OFF + len(name)
            slots, st = scan_slots(ds, start, nxt, strict=strict)
            for kk in ('head', 'ff', 'krep', 'range'):
                diag[kk] += st[kk]
            _absorb(tabs, dups, name, slots)
        if tabs:
            return tabs, dups, diag
        diag['warn'].append('索引路径解析出 0 张表')

    # ── 最后兜底：扫到 A_END 并按步长跳 ────────────────────────
    from game_data import A_END          # 只在降级时用（经验边界值，见 M3/M9）
    a_end = min(A_END, len(ds))
    if len(ds) < A_END:
        diag['warn'].append('dropsetinfo 长度 %d 小于预期 %d' % (len(ds), A_END))
    diag['mode'] = 'legacy'
    found = []
    pos = 0
    while pos + 4 <= a_end:
        n = struct.unpack_from('<I', ds, pos)[0]
        if 1 <= n <= 200 and pos + 4 + n <= a_end:
            s = ds[pos + 4:pos + 4 + n]
            if all(32 <= c < 127 for c in s):
                found.append((pos, n, s.decode('ascii')))
                pos += 4 + n
                continue
        pos += 1
    for j, (p, n, name) in enumerate(found):
        fstart = p + 4 + n
        fend = found[j + 1][0] if j + 1 < len(found) else a_end
        slots, st = scan_slots(ds, fstart, fend, strict=False)
        for kk in ('head', 'ff', 'krep', 'range'):
            diag[kk] += st[kk]
        _absorb(tabs, dups, name, slots)
    return tabs, dups, diag


# ── 作用域规则（数据表，不是 if）────────────────────────────────

ScopeRule = namedtuple('ScopeRule', 'code kind value why')

SCOPE_RULES = (
    ScopeRule('PURCHASE', 'prefix', 'Purchase_Reward',
              '预购 / 版本包奖励：玩家花钱买的内容，不是掉落'),
    ScopeRule('PROMO', 'prefix', 'Drops_Reward', '活动发放奖励：不是掉落'),
    ScopeRule('PROMO_BILIBILI', 'prefix', 'Bilibili_Drops_Reward',
              '渠道推广奖励：不是掉落'),
    # Drops_ 前缀整族不算掉落物。
    # 起因是 Drops_Ocean_Parrot_PetBox（海洋鹦鹉宠物箱，单槽 rate=100%），
    # 它的 item_key=30082 不在 iteminfo —— 宠物类不是普通物品。
    # ⚠️ 本条要放在 PROMO 之后：Drops_Reward_* 由 PROMO 先命中，
    #    match_rule 返回第一条，归类语义不会被本条改写。
    ScopeRule('DROPS_SYS', 'prefix', 'Drops_',
              '系统发放（宠物箱等）：非击杀掉落，不算掉落物'),
    ScopeRule('TRADE', 'prefix', 'Drop_Trade_', '交易物：不是掉落'),
    ScopeRule('FAVOR_ITEM', 'suffix', '_FavoriteItem_Reward',
              '好感度回礼：送礼的回赠，不是掉落'),
    ScopeRule('FAVOR_CONTRIB', 'suffix', '_FavoriteContribution_Reward',
              '好感度贡献回礼：同上是回赠不是掉落'),
    ScopeRule('FRIENDLY', 'prefix', 'DropSet_Friendly_',
              '友好交互（赠送/交谈/威胁）获得，不是击杀掉落'),
    # 以下三族都是"系统触发发放"，不是逐怪掉落，不在本工具作用域
    ScopeRule('CONTRIBUTION', 'prefix', 'DropSet_Contribution',
              '阵营贡献发放：交贡献后的系统奖励，不是掉落'),
    ScopeRule('DROP_CONTRIBUTION', 'prefix', 'DropSet_DropContribution',
              '掉落式贡献点发放：系统按机制发放，非逐怪掉落（外审 P1-1）'),
    ScopeRule('REBLOCKADE', 'prefix', 'DropSet_Reblockade',
              '营地肃清/贸易/贡献发放：按事件触发，不是逐怪掉落'),
    ScopeRule('QUEST_MISSION', 'prefix', 'DropSet_Quest_',
              '任务奖励：不是掉落'),
    ScopeRule('QUEST_MISSION2', 'prefix', 'Dropset_Mission_',
              '任务奖励（小写 s 变体）：不是掉落'),
    ScopeRule('MISSION', 'prefix', 'DropSet_Mission_',
              '任务奖励：不是掉落'),
)


def match_rule(tname):
    """命中的第一条规则；没命中返回 None。"""
    if not isinstance(tname, str) or not tname:
        return None
    for r in SCOPE_RULES:
        if r.kind == 'prefix' and tname.startswith(r.value):
            return r
        if r.kind == 'suffix' and tname.endswith(r.value):
            return r
    return None


def is_nondrop_table(tname):
    return match_rule(tname) is not None


def is_reward_table(tname):
    r = match_rule(tname)
    return r is not None and r.code not in ('FAVOR_ITEM', 'FAVOR_CONTRIB')
