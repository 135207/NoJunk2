# -*- coding: utf-8 -*-
"""charinfo.py —— 运行时破解 characterinfo，提供 NPC 官方分类（零联网）。

背景：nojunk_core.classify() 只按掉落表名关键词推断分类，是自造体系；本模块
在运行时从游戏 PAZ 解包 characterinfo（gamedata/characterinfo.staticinfobody），
锚定法解析每个 NPC 的官方描述串（desc，格式：
大陆_种族_性别_年龄_职业_武器_类型_阵营[附加标签]），
直接按官方标签（类型/阵营/附加）映射 (kind, sub)，作为唯一分类源。

零联网铁律：本模块属于发行模块，禁止 import urllib/requests/http；
联网只允许开发期脚本。解析失败时返回空映射，调用方回退旧分类逻辑。
"""
import os
import json
import struct
import re
import bisect

# 从 PAZ 解包时用到 cd_paz（延迟导入，避免无游戏环境 / 静态检查误伤）
def _read_characterinfo(game_dir):
    """读取 characterinfo 明文 bytes（gamedata/characterinfo.staticinfobody）；失败返回 b''。

    game_dir 为游戏本体根目录（含 0008/0.pamt 特征）。零联网。
    """
    try:
        import cd_paz
    except Exception:
        return b''
    try:
        pamt_dir = os.path.join(game_dir, '0008')
        es = cd_paz.parse_pamt(os.path.join(pamt_dir, '0.pamt'))
        return cd_paz.read_paz_file(es, 'gamedata/characterinfo.staticinfobody',
                                    pamt_dir) or b''
    except Exception:
        return b''

# ── 官方 desc 标签（类型/阵营/附加）────────────────────────
# desc 格式：大陆_种族_性别_年龄_职业_武器_类型_阵营[附加标签...]
# 官方类型标签：Tier/Elite/Boss/Empty（Tier 后跟数字如 Tier_2）
# 官方阵营标签：NPC/Player/Monster/Animal/Boss/Friendly/Enemy/Ally/
#               Neutral/Criminal/Domestic/Wild/All（出现于类型标签之后）
# 附加标签：Wild/Small/Unique/Friendly/Domestic/Battle/Neutral/Enemy 等
# 韩文职业/武器标签：탑승물(坐骑)/안장(鞍座)/마갑(马甲)/펫(宠物)/길잡이(向导)
_TYPE_KW = ('Tier', 'Elite', 'Boss', 'Empty')
_CAMP_KW = ('NPC', 'Player', 'Monster', 'Animal', 'Boss', 'Friendly',
            'Enemy', 'Ally', 'Neutral', 'Criminal', 'Domestic', 'Wild', 'All',
            'Nature')
_RIDE_KW = ('탑승물', '안장', '마갑', '펫', '길잡이')
_HUMAN_RACE = ('인간', '오크', '드워프', '고블린', '엘프', '유령', '반란군')


def _parse_desc(desc):
    """解析 desc 官方字段 -> (类型标签, 阵营标签, 附加标签列表)。

    类型标签是首个 Tier/Elite/Boss/Empty 前缀字段（可能带 <> 包裹或数字后缀，
    如 <Empty>、Tier_2）；阵营标签是类型标签后第一个官方阵营词（跳过纯数字）；
    其后为附加标签。无法定位类型或阵营时返回 None。
    """
    if not desc:
        return None
    parts = desc.split('_')
    for i, p in enumerate(parts):
        core = p.strip('<>')
        if not core.startswith(_TYPE_KW):
            continue
        j = i + 1
        while j < len(parts) and parts[j].isdigit():
            j += 1
        if j < len(parts) and parts[j] in _CAMP_KW:
            return core, parts[j], parts[j + 1:]
    return None


def real_class(desc):
    """desc 官方标签 -> (kind, sub)。desc 为空或解析失败返回 None。

    官方标签直读映射（唯一分类源），优先级：
      1. Boss（类型/阵营/附加标签）→ hostile|boss
      2. 坐骑/宠物/向导（韩文职业标签）→ friendly|nodrop
      3. 家养/友好/中立（阵营 Animal/All + 附加 Domestic/Friendly/Neutral）
         → friendly|nodrop
      4. 友好阵营（Friendly/Ally/Player/NPC）→ friendly|nodrop
      5. 野生动物（阵营 Animal/Nature 其余）→ hostile|beast
      6. 敌对阵营（Monster/Enemy/Criminal）→ 人形 hostile|humanoid，
         非人形 hostile|other
    解析失败返回 None，调用方回退旧 classify 推断（安全降级）。
    """
    pr = _parse_desc(desc)
    if pr is None:
        return None
    typ, camp, extras = pr
    joined = desc
    # 1. Boss 最高优先
    if typ == 'Boss' or camp == 'Boss' or any('Boss' in e for e in extras) \
            or '중간보스' in joined or '거대보스' in joined:
        return ('hostile', 'boss')
    # 2. 坐骑/宠物/向导（韩文职业标签）→ 友善
    if any(k in joined for k in _RIDE_KW):
        return ('friendly', 'nodrop')
    # 3. 家养/友好/中立动物（Animal 阵营 + 附加）→ 友善
    if camp == 'Animal' and any(e in extras for e in ('Domestic', 'Friendly', 'Neutral')):
        return ('friendly', 'nodrop')
    # 4. 友好阵营 → 友善
    if camp in ('Friendly', 'Ally', 'Player', 'NPC'):
        return ('friendly', 'nodrop')
    if camp == 'All' and 'Neutral' in extras:
        return ('friendly', 'nodrop')
    if 'Neutral' in extras or 'Friendly' in extras or 'Domestic' in extras \
            or 'Ally' in extras or 'Player' in extras or 'NPC' in extras:
        return ('friendly', 'nodrop')
    # 5. 野生动物/自然物 → beast
    if camp in ('Animal', 'Nature'):
        return ('hostile', 'beast')
    # 6. 敌对阵营：人形 / 非人形
    if camp in ('Monster', 'Enemy', 'Criminal'):
        # P2-7：种族字段不硬取 split[1]（字段错位时拿到性别/职业会误分类），
        # 改为全 desc 搜索种族标签
        if any(r in desc for r in _HUMAN_RACE):
            return ('hostile', 'humanoid')
        return ('hostile', 'other')
    # 7. 其余阵营（All 非 Neutral 等）→ 敌对 other
    return ('hostile', 'other')


# ── characterinfo.pabgb 锚定解析（构建期验证过的同一逻辑）──────────
def _scan_desc_offsets(b):
    """扫描全部 len-prefixed 韩文描述串，返回 [(offset, ln, text)]。"""
    mix_re = re.compile(rb'(?:[\xea-\xed][\x80-\xbf]{2}|[A-Za-z0-9<>_\s./-]){12,200}')
    out = []
    for m in mix_re.finditer(b):
        seg = m.group()
        if b'_' not in seg:
            continue
        s = seg.decode('utf-8', 'replace')
        if not any('\uac00' <= ch <= '\ud7af' for ch in s):
            continue
        off = m.start()
        # F5：off>=4 判定必须在解包之前（off<4 时 off-4 为负 → struct.error）
        if off < 4:
            continue
        ln = struct.unpack_from('<I', b, off - 4)[0]
        if 16 <= ln <= 300 and b[off:off + ln] == seg[:ln]:
            out.append((off, ln, s))
    out.sort()
    return out


def _is_ascii_name_at(b, q):
    """q 处为 u32(id)，其后是 len-prefixed ASCII 模板名则返回名字。"""
    if q + 8 > len(b):
        return None
    nl = struct.unpack_from('<I', b, q + 4)[0]
    if not (2 <= nl <= 60) or q + 8 + nl > len(b):
        return None
    nb = b[q + 8:q + 8 + nl]
    if all(32 <= c < 127 for c in nb) and any(chr(c).isalpha() for c in nb):
        return nb.decode('ascii')
    return None


def _desc_after(desc_list, desc_offs, off, fwd=800):
    """off 之后 800 字节内最近的 desc。

    desc_list 为 [(off, ln, text)]（已按 off 排序）；desc_offs 为其 off 的预建列表，
    避免每次调用重复构建 O(n) 列表。
    """
    i = bisect.bisect_left(desc_offs, off)
    if i >= len(desc_list):
        return None
    d = desc_list[i]
    return d if d[0] - off <= fwd else None


def _parse_fast(b, targets):
    """锚定法：逐目标 id 在 b 中 find u32，校验模板名 + 后随 desc（构建期验证逻辑）。

    比全量扫描 u32 窗口快得多：1000 个 id × b.find(C 层实现) 毫秒级。
    返回 {str(nid): {'char_name','desc'}}。
    """
    found = {}
    if not b or not targets:
        return found
    desc_list = sorted(_scan_desc_offsets(b))
    desc_offs = [d[0] for d in desc_list]
    pack = struct.pack
    for nid in targets:
        key = str(nid)
        q = b.find(pack('<I', nid))
        while q >= 0:
            nm = _is_ascii_name_at(b, q)
            if nm:
                d = _desc_after(desc_list, desc_offs, q + 8)
                if d:
                    found[key] = {'char_name': nm, 'desc': d[2]}
                    break
            q = b.find(pack('<I', nid), q + 1)
    return found


def build_real_classes(game_dir, npc_ids):
    """运行时破解：返回 {npc_id: {'char_name','desc','kind','sub'}}。

    失败（无游戏 / 解包失败 / 解析为空）返回空 dict，调用方安全回退。
    npc_ids 可为 str 或 int 的可迭代对象。
    """
    b = _read_characterinfo(game_dir)
    if not b:
        return {}
    targets = set()
    for x in npc_ids:
        try:
            targets.add(int(x))
        except (TypeError, ValueError):
            continue
    if not targets:
        return {}
    parsed = _parse_fast(b, targets)
    out = {}
    for nid, info in parsed.items():
        rc = real_class(info.get('desc'))
        if rc:
            out[nid] = {'char_name': info.get('char_name'),
                        'desc': info.get('desc'), 'kind': rc[0], 'sub': rc[1]}
    return out


def load_static(data_dir):
    """从发行数据文件加载固化映射（构建期生成，运行时破解的兜底）。

    数据文件 data/npc_realclass.json：{npc_id: {'char_name','desc','kind','sub'}}。
    文件缺失 / 损坏返回空 dict。零联网。
    """
    try:
        fp = os.path.join(data_dir, 'npc_realclass.json')
        with open(fp, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}
