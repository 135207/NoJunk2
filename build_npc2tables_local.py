# -*- coding: utf-8 -*-
"""build_npc2tables_local.py —— 游戏更新后的映射重建（纯本地，零联网）。

流水线（省力方案）：
  ① 重新解析 dropsetinfo → 新表清单 + 每张表的内容指纹
  ② 老敌人：沿用旧映射；表还在但指纹变了 → 标 changed
  ③ 新敌人（旧映射没有的）：名字匹配专表 + desc 推断共享表，
     全部标 calibrated=False（未校准——等 questlog 跟上后反哺校验）
  ④ 输出新 npc2tables.pkl + 对拍报告（控制台 + rebuild_report.md）

用法：python build_npc2tables_local.py <游戏目录> [--dry]
"""
import hashlib
import json
import os
import pickle
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

OLD_PKL = os.path.join(HERE, 'npc2tables.pkl')
REPORT = os.path.join(HERE, 'rebuild_report.md')

# desc 韩文种族 → 种族共享表后缀（表不存在就跳过，宽容）
RACE2COMMON = {
    '인간': 'DropSet_Human_Common',
    '드워프': 'DropSet_Dwarf_Common',
    '오크': 'DropSet_Orc_Common',
    '고블린': 'DropSet_Goblin_Common',
    '트롤': 'DropSet_Troll_Common',
}


def load_new_tables(game_dir):
    """新版掉落表：{表名: 指纹}。指纹 = 表内容 sha1（变一点就不一样）。"""
    import cd_paz
    import game_data
    g8 = os.path.join(game_dir, '0008')
    es = cd_paz.parse_pamt(os.path.join(g8, '0.pamt'))
    ds = cd_paz.read_paz_file(es, 'gamedata/dropsetinfo.staticinfobody', g8)
    gd = game_data.GameData.__new__(game_data.GameData)
    gd.ds = ds
    gd.tab_dups = {}
    # 与运行时同口径：也读官方索引 staticinfoheader，否则走旧扫描路径
    # （实测会少 1 张表 DropSet_Item_Kuku_Pot），指纹对不上就会误判
    # 「表消失了」。类级默认值 hdr=b'' 已在 GameData 上，读不到自动降级。
    try:
        gd.hdr = cd_paz.read_paz_file(
            es, 'gamedata/dropsetinfo.staticinfoheader', g8)
    except Exception:
        gd.hdr = b''
    tabs = gd._parse_tables()
    fp = {}
    for tname, slots in tabs.items():
        h = hashlib.sha1()
        for k, (off, r) in sorted(slots.items()):
            h.update(struct.pack('<IQ', k, r))
        fp[tname] = h.hexdigest()[:16]
    return tabs, fp


def infer_new_enemy(char_name, desc, table_names):
    """新敌人推断：专表名字匹配 + desc 种族共享表。返回 (tables, 依据备注)。"""
    tables = []
    notes = []
    # a) 专表：char_name 里的关键 token 对 DropSet_Character_*
    #    NHM_Bleed_Bandits_TwoHandFlamethrower_1 → 试 Bandits/Bandit
    toks = [t for t in char_name.replace('-', '_').split('_') if len(t) > 3]
    for t in toks:
        # 09：rstrip('s') 会把 Boss→Bo、Glass→Gla；只去"末尾单个 s"（ss 结尾不动）
        singular = t[:-1] if t.endswith('s') and not t.endswith('ss') else t
        hit = False
        for cand in ('DropSet_Character_%s' % t,
                     'DropSet_Character_%s' % singular):
            if cand in table_names:
                tables.append(cand)
                notes.append('专表名字匹配: %s' % cand)
                hit = True
        # 10：首个命中即停——名字里越靠前的 token 越具体（Bleed_Bandits → Bandits），
        # 继续堆叠后面的 token 会把不相关专表误挂上来
        if hit:
            break
    # b) desc 种族 → 共享表
    if desc:
        for kor, tab in RACE2COMMON.items():
            if kor in desc and tab in table_names:
                tables.append(tab)
                notes.append('desc 种族 %s → %s' % (kor, tab))
    # c) 派系：char_name token 直接对 DropSet_Faction_*（Bandits→Bandit 等）
    #    外审 P1-6：旧版走 real_class 的 sub，但 sub 值域只有
    #    boss/nodrop/beast/humanoid/other——faction_ 分支永不成立，是死逻辑。
    for t in toks:
        singular = t[:-1] if t.endswith('s') and not t.endswith('ss') else t
        for cand in ('DropSet_Faction_%s' % t, 'DropSet_Faction_%s' % singular):
            if cand in table_names:
                tables.append(cand)
                notes.append('派系名字匹配: %s' % cand)
                break
        else:
            continue
        break
    # 去重保序
    seen = set()
    out = []
    for t in tables:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out, notes


def _pkl_path():
    """重建结果写到哪 —— 必须**持久**，且与运行时读取位置对得上。

    ⚠️ 踩过的两个坑（都是只在打包后才暴露，本地 100% 测不出来）：
      ① 写本文件的 HERE → 打包后 = _MEIPASS，与运行时 res_dir() 不是一处
      ② 写 res_dir() → 打包后 res_dir() 实测就是 _MEIPASS（临时解压目录
         C:\\Users\\..\\Temp\\_MEIxxx），**关掉程序就蒸发**
    正确位置是 user_dir()（exe 所在目录，持久且可写）。运行时
    npc2tables_path() 也是「用户目录优先、内置兜底」，所以两者能对上。
    """
    try:
        import nojunk_core as _core
        return os.path.join(_core.user_dir(), 'npc2tables.pkl')
    except Exception:
        return OLD_PKL


def rebuild_npc2tables(game_dir, dry=False, say=None, min_keep_ratio=0.5):
    """重建「NPC → 掉率表」映射（可被界面调用）。

    返回一个统计 dict：
      n_tables / n_old / n_kept / n_changed / n_missing_tab /
      n_new / n_map / report_lines / written / backup

    安全约定（对应「问题 = 静默伤害用户」这条定义）：
      · 写盘前先备份旧 pkl（可回退）
      · 结果与旧映射相比缩减超过一半 → **拒绝写盘**并抛异常，
        宁可不动，也不能把一份残缺映射默默换上去（那会让大量敌人
        变成「暂无对应表」，用户的勾选静默失效）
      · 每一步都通过 say() 回调出声，界面能看到进度
    """
    say = say or (lambda m: None)
    # 读、写两条路径必须分开：
    #   IN  = npc2tables_path()：运行时实际在用的那份（内置兜底 or 上次重建的）
    #   OUT = user_dir()：持久且可写
    # 混成一个会出事：exe 目录里第一次没有 pkl → 「无旧映射」从零重建，
    # 实测只得到 401 个敌人（内置那份有 770+），用户点一下重建反而变差，
    # 而且 old 为空时安全闸门不生效，照样写盘 —— 又一次静默伤害。
    OUT = _pkl_path()
    IN = OUT
    try:
        import nojunk_core as _c
        IN = _c.npc2tables_path()
    except Exception:
        pass
    say('   旧映射来源: %s' % IN)

    say('① 解析新版 dropsetinfo…')
    tabs, fp = load_new_tables(game_dir)
    say('   新表 %d 张' % len(tabs))
    if not tabs:
        raise RuntimeError('解析出 0 张掉落表，拒绝重建')


    old = {}
    if os.path.exists(IN):
        # 复审 P2（本轮）：原来用裸 pickle.load —— 运行时 Core 读同一个
        # npc2tables.pkl 已经走白名单 (_SafeUnpickler)，构建端却是裸的。
        # pkl 落在可写目录、和 exe 同处，构建脚本不能成为链路上的短板。
        try:
            import nojunk_core as _core
            old = _core._safe_load_pkl(IN)
        except ImportError:
            with open(IN, 'rb') as _f:
                old = pickle.load(_f)
        say('② 旧映射 %d 个敌人' % len(old))
    else:
        say('② 无旧映射（全新构建）')

    # 敌人清单（id → char_name/desc）：走现有 realclass 管线
    say('   解析 characterinfo（真实职业/desc）…')
    import charinfo
    # id 宇宙 = npc_infokey.json ∪ 旧映射。已知边界：DLC 新增的全新 id
    # 不在此列（_parse_fast 只解析指定 id）——v1 接受，报告里注明
    ids = set(old.keys())
    ik_fp = os.path.join(HERE, 'data', 'npc_infokey.json')
    if os.path.exists(ik_fp):
        with open(ik_fp, encoding='utf-8') as _f:
            ids.update(json.load(_f).keys())
    realclass = charinfo.build_real_classes(game_dir, sorted(ids))
    say('   characterinfo 敌人 %d 个（id 宇宙 %d）' % (len(realclass), len(ids)))

    new_map = {}
    kept = changed = missing_tab = 0
    new_enemies = []
    for nid, info in realclass.items():
        nid = str(nid)
        if nid in old:
            ent = dict(old[nid])
            new_tabs = []
            bad = []
            for row in ent['tables']:
                tname = row[0]
                if tname in fp:
                    new_tabs.append(row)
                else:
                    bad.append(tname)
            if bad:
                changed += 1
                ent['tables'] = new_tabs
                ent['notes'] = '更新后消失的表: %s' % bad
                missing_tab += len(bad)
            if not ent['tables']:
                ent['dead'] = True  # 08：全部表消失，UI 应显示"已失效"而非置灰
            new_map[nid] = ent
            kept += 1
        else:
            # 友方/无掉落类（玩家、坐骑、商人）不进映射——挂了表也是空耗
            if info.get('kind') == 'friendly':
                continue
            tabs_inf, notes = infer_new_enemy(
                info.get('char_name', ''), info.get('desc', ''), fp)
            new_map[nid] = {
                'name': info.get('char_name') or '?', 'en': info.get('char_name', ''),
                'tables': [(t, None, 0, False) for t in tabs_inf],
                'n_drops': 0, 'calibrated': False,
                'notes': '; '.join(notes) or '无推断依据',
            }
            new_enemies.append(nid)

    say('③ 老敌人沿用 %d（表消失 %d 张），新敌人 %d 个' % (kept, missing_tab, len(new_enemies)))

    # 报告
    lines = ['# 映射重建报告', '',
             '- 新表总数: %d' % len(tabs),
             '- 老敌人沿用: %d' % kept,
             '- 老敌人表消失: %d 张' % missing_tab,
             '- 新敌人: %d（全部标 calibrated=False，未校准）' % len(new_enemies), '']
    if new_enemies:
        lines.append('## 新敌人推断明细')
        for nid in new_enemies[:200]:
            e = new_map[nid]
            lines.append('- %s %s：%s（%s）' % (
                nid, e['en'] or e['name'],
                [t[0] for t in e['tables']], e['notes']))
    # ── 安全闸门：结果比旧映射缩水太多就别写 ──────────────────
    # 一份残缺映射会让大量敌人变成「暂无对应表」，用户存的勾选静默失效
    # ——正是本报告定义的「问题」。宁可不动，也不能默默换上去。
    # ⚠️ 闸门必须在**写报告之前**：否则报告里写着「重建完成」，
    #    实际被拒绝 —— 又是一处静默误导。
    backup = ''
    written = False
    reject = ''
    if not dry:
        if not new_map:
            reject = '重建结果为空，已拒绝写盘'
        elif old and len(new_map) < len(old) * min_keep_ratio:
            reject = ('重建结果缩水过多（旧 %d → 新 %d，低于 %.0f%%），'
                      '已拒绝写盘。旧映射保持原样。'
                      % (len(old), len(new_map), min_keep_ratio * 100))
    if reject:
        lines.append('')
        lines.append('## ⚠️ 未写盘')
        lines.append(reject)
        try:
            open(REPORT, 'w', encoding='utf-8').write('\n'.join(lines))
        except Exception:
            pass
        say('（已拒绝写盘，报告记录原因）')
        raise RuntimeError(reject)
    if not dry:
        open(REPORT, 'w', encoding='utf-8').write('\n'.join(lines))
        say('报告 → %s' % REPORT)
    else:
        say('（dry 模式：报告仅打印不写盘）')

    if not dry:
        if os.path.exists(OUT):
            import shutil, time
            stamp = time.strftime('%Y%m%d-%H%M%S')
            bak = OUT + '.bak-%s' % stamp
            n = 1
            while os.path.exists(bak):
                n += 1
                bak = OUT + '.bak-%s-%d' % (stamp, n)
            shutil.copy2(OUT, bak)
            backup = bak
            say('旧映射备份 → %s' % bak)
        # 复审 P3（本轮）：tmp 带 pid —— 与 nojunk_core 写盘同口径，
        # 双实例并发重建时固定名会互相截断，产出一个嵌合的坏 pkl
        tmp = OUT + '.tmp%d' % os.getpid()
        with open(tmp, 'wb') as f:
            pickle.dump(new_map, f)
        os.replace(tmp, OUT)
        written = True
        say('④ 新映射已写 %s（%d 个敌人）' % (OUT, len(new_map)))
    else:
        say('④ --dry 不写盘')

    return {
        'n_tables': len(tabs), 'n_old': len(old), 'n_kept': kept,
        'n_changed': changed, 'n_missing_tab': missing_tab,
        'n_new': len(new_enemies), 'n_map': len(new_map),
        'report_lines': lines, 'written': written, 'backup': backup,
    }


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    dry = '--dry' in sys.argv
    if not args:
        print('用法: python build_npc2tables_local.py <游戏目录> [--dry]')
        sys.exit(2)
    try:
        r = rebuild_npc2tables(args[0], dry=dry, say=print)
    except Exception as ex:
        print('重建失败: %s' % ex)
        sys.exit(1)
    print('完成: 表 %d / 沿用 %d / 新敌人 %d / 映射 %d'
          % (r['n_tables'], r['n_kept'], r['n_new'], r['n_map']))


if __name__ == '__main__':
    main()
