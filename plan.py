# -*- coding: utf-8 -*-
"""plan.py —— 变更计划：把「用户的选择」变成「要动哪些槽」。纯函数，无 I/O。

这是整个新引擎**最重要的一个模块**。

为什么它单独存在（也是这个 MOD 历史上最大的坑）：
    「要动哪些槽」这个判断，以前被抄在 4 个地方：
      preview_changes（显示数字）/ apply_changes（写盘）/
      compute_patch_changes（导出补丁）/ restore_item（恢复后重建）
    每次改规则都要改 4 处，漏一处就是**静默不一致**——用户看到的数字
    和实际写入的东西对不上，而且不报错。实测已发生 4 次（提率判据漂移
    三次 + 导出绕过不可行判定）。

    现在只有这里算一次。预览 / 写盘 / 导出 / 恢复全部消费同一份计划。

设计约束：
  · 纯函数：同样的输入必须给出同样的计划，不读文件、不碰时钟、不碰全局状态
  · 每个不写的槽都必须带原因码（见 dropset.SCOPE_RULES 与 REASON_*）
    —— 这让"静默"在结构上不可能：跳过必有记录，界面/日志/测试都消费它
  · 提率永不产生删除语义（，见 _boost_plan 注释）
"""

W = 1000000                 # 提率目标满权重

# ── 原因码：跳过一个槽时**必须**填一个 ──────────────────────────
REASON_GWL = 'GWL'                  # 全局白名单
REASON_EWL = 'EWL'                  # 敌人白名单
REASON_NONDROP = 'NONDROP'          # 该表不是掉落物
REASON_INFEASIBLE = 'INFEASIBLE'    # 该表提率数学不可行（Σp>100% 只是成因之一；
                                    # 另一类是目标是唯一掉落物、p<1 不可达。
                                    # **具体成因看表级 infeasible_reason**，
                                    # 这里只有一个码，别照着旧注释以为只可能是 Σp>100%）
REASON_BOOSTED = 'BOOSTED'          # 该物品正在提率，不参与删除（互斥）

# ── 提率不可行的**成因**（给 UI 文案用：两类成因的行动指引完全不同）──
INFEAS_SOLE_DROP = 'SOLE_DROP'          # 目标是表里唯一掉落物 → 概率恒 100%，
                                        # 用户要的 p<1 写不出来（不是他设错了）
INFEAS_SUM_OVER_100 = 'SUM_OVER_100'    # Σp>100%，或 others 预算撑不出目标占比


def _sole_drop_conflict(lst, cur, fixed_w):
    """是否属于「目标是表里唯一掉落物、p<1 不可达」这一类不可行。

    必须同时满足：others 无预算（cur<=0）**且** 没有满权重固定槽
    （fixed_w==0）—— 只满足前者时，表里还有别的候选物品，目标并不是"独苗"，
    那时不可行的成因是"塞不下/预算不足"（SUM_OVER_100），成因不同、
    给用户的行动指引也不同，绝不能混为一谈。

    _boost_plan 的守卫与 build_plan 的成因归类**共用这一个判据**——
    两处各写一遍迟早漂移（这个模块的历史教训就是判据被抄成 4 份）。
    ⚠️ 加 fixed_w 不会改变 feasible 的结果：fixed_w 非 0 时必然 >=W，
       下面 `avail<=0` 分支同样返回 False，两条路殊途同归。
    """
    if cur > 0 or fixed_w > 0:
        return False
    return sum(p for _ik, _o, p in lst) < 1.0 - 1e-9


def _boost_plan(lst, cur, fixed_w):
    """提率权重解算（唯一实现）。

    从 nojunk_core 迁来——按新分层它属于计划层（core → plan 单向依赖）。
    nojunk_core 保留同名引用以兼容既有调用与 121 条用例。

    输入：lst = [(ik, off, p)] 该表提率物品；cur = others 可缩放槽权重和；
          fixed_w = 满权重固定槽权重和（入分母不缩放）。
    输出：(w_of {ik: 写入权重}, new_sum others 目标总和, feasible bool)。

    口径要点（历史教训，别改回去）：
      · Σp<=1 且 others 有预算 → 精确命中；new_sum>=0 才缩 others
      · 预算为负 → 不可行：others **保持原值不动**（new_sum=-1 哨兵）
      · Σp>1 → 不可行（旧实现会返回按比例算出的 w_of，已统一为 {}）
      · 提率**永不产生删除语义**：删除只能来自用户显式勾选
      · 不可行分支一律 ({}, -1, False)：绝不返回算了一半的 w_of
        —— feasible=False 却带权重，是"漏判一次就写坏盘"的接口雷
      · Σp==1.0（「设为必掉」）必须可行：守卫放宽到 new_sum>=0 的原因见下
      · others 无预算（cur<=0）：Σp>=1 可行；Σp<1 判不可行
        —— 目标是唯一掉落物时概率恒为 100%，p<1 写不出来（见下方注释）
    """
    if not lst:
        return {}, -1, False
    sum_p = sum(p for _ik, _o, p in lst)
    p_max = max(p for _ik, _o, p in lst)
    if sum_p <= 0 or p_max <= 0:
        return {}, -1, False
    avail = max(0, W - fixed_w)
    if sum_p <= 1.0 and cur > 0:
        w_of = {ik: max(1, int(W * p / p_max)) for ik, _o, p in lst}
        new_sum = int(W * (1.0 - sum_p) / p_max) - fixed_w
        if new_sum >= 0:
            return w_of, min(new_sum, 99_000_000), True
        return {}, -1, False
    # others 无预算（cur<=0）时的口径（ 修复，）：
    #   游戏用**归一化权重**：概率 = 该槽权重 / 全表权重之和。
    #   cur<=0 且没有满权重固定槽时，提率目标是表里唯一可选掉落物，
    #   无论写什么权重，概率恒为 100% —— Σp<1 数学上不可达。
    #   旧实现在这里落到下面的 avail 分支：w=int(avail*p/sum_p) 但 new_sum
    #   是 -1 哨兵（不缩 others，因为无 others 可缩），实测
    #   _boost_plan([(1,0,0.3)],0,0) → w=1000000 → 实际概率 100%，误差 70%。
    #   必须判不可行交给上层报 REASON_INFEASIBLE，绝不能静默写错的数字。
    #   Σp>=1（用户设必掉）是可达的：必掉本来就要求 100%，保留原行为。
    #   ⚠️ 固定槽权重恒 >=W，所以 fixed_w 非 0 时 avail<=0 已在下面判 False；
    #      本分支不会误伤"只有满权重固定槽"的表。
    if _sole_drop_conflict(lst, cur, fixed_w):
        return {}, -1, False
    if avail <= 0:
        return {}, -1, False
    # Σp>100%：数学不可行。判据沿用 `sum_p <= 1.0`（不用容差），保证
    # feasible 的取值与旧实现**逐位一致**，只有返回值被收紧。
    # 旧实现在这里照样返回算了一半的 w_of，于是出现"feasible=False 却带着
    # 权重"的自相矛盾：调用方只要有一处漏判 feasible，就会把不可行的权重
    # 写进盘。统一口径见下条 docstring —— 不可行分支一律 ({}, -1, False)。
    if sum_p > 1.0:
        return {}, -1, False
    w_of = {ik: max(1, int(avail * p / sum_p)) for ik, _o, p in lst}
    return w_of, -1, True


class ChangePlan:
    """一次变更的完整计划。

    delete    [(tname, off, old_rate, item_key)]       要清零的槽
    boost     [(tname, off, old_rate, new_rate, key)]  要提权的槽
    skipped   [(tname, off, item_key, reason)]         不动的槽（必有原因码）
    infeasible[(tname, targets)]                       数学不可行的提率
    infeasible_reason {tname: INFEAS_*}                该表不可行的**成因**
    infeasible_blockers {tname: [物品 key]}             占住额度的满权重槽属于哪些物品
    affected  set[gid]                                 真正受影响的敌人组

    infeasible_reason 是纯增量字段（不动 infeasible 的元组结构）：
    两类成因给用户的行动指引完全不同——
      SOLE_DROP    ："它已经是表里唯一掉落物（必然 100%），没法再调低"
      SUM_OVER_100 ："你设的几项加总超过 100%（或表里塞不下）"
    消费方（UI 文案）没跟上时只是沿用旧的中性说法，不会破坏任何行为。

    infeasible_blockers：表里那些 r>=W 的槽属于哪些物品。它们把额度占满，
    是"预算不足"这类不可行的物理原因。按**占额度降序**排（最该删的在前），
    权重相同再按 key 字符串升序 —— 后者只为纯函数性（_gate_plan P2）。

    ⚠️ 这些是**游戏原版的必定掉落物**：告诉用户"删掉它们即可生效"时，
       必须同时说明代价（删后它们不再必掉），否则是在推用户做破坏性操作。
    ⚠️ 只在 fixed_w>0 时存在；是否真是主因看 infeasible_reason（Σp>1 时
       主因是用户自己设的加总，不是这些槽）。
    ⚠️ UI 展示要设上限（前 3 个 + "等 N 个"）：真实数据里有 41 个满权重
       槽的表，全列会淹没弹窗。

    ⚠️ 实测（，1140 张表）：blocker 槽共 1148 个，权重**恒为
       W=1000000、无一例外**；同物品多槽累加 >1W 的只有 1 张表。
       所以"占额度降序"在真实数据上**几乎不产生顺序差异**，实际次序由
       二级 key 升序决定。保留降序是为那 1 张表、以及游戏日后改权重分布
       时仍然正确——别把它当成已经生效的"最该删的排第一"。
       （顺带：83.1% 的含 blocker 表只有 1 个 blocker，96.3% 在 3 个以内，
        所以上限 + 排序的选择面本来就很窄。）

    ⚠️ 口径边界：只有 **r>=W 的槽**才计入 blockers。同一物品若部分槽满
       权重、部分 r<W，那些 r<W 的槽走 others 分支（可缩放），不属于
       blocker —— 这是有意的，不是漏分支。

    ── 接线进度（ ）——读代码前先看这里，别猜：
       plan.py   ✅ 已实现（本文件，含权重降序排序）
       core      ✅ 已透传为 st._boost_infeasible_blockers（纯增量 + getattr 默认值）
       UI        ⏳ 待渲染（名字走 gd.item_name()，零 lang 键）
       在 UI 渲染落地前，**用户还看不到它**；plan/core 两侧的行为不受影响。
    """

    def __init__(self):
        self.delete = []
        self.boost = []
        self.skipped = []
        self.infeasible = []
        self.infeasible_reason = {}
        self.infeasible_blockers = {}
        self.affected = set()

    # ── 派生统计（预览/日志/报告都从这里取，保证口径一致）────────
    @property
    def n_delete(self):
        return len(self.delete)

    @property
    def n_boost(self):
        # ：'#dup'（重复槽清零）和 '*'（others 缩放）不是用户提率，
        # 计入会让预览的"提率 N 键"虚高
        return len({k for _t, _o, _or, _nr, k in self.boost
                    if not k.endswith('#dup') and k != '*'})

    @property
    def n_slots(self):
        return len(self.delete) + len(
            {(t, o) for t, o, _a, _b, _c in self.boost}
            - {(t, o) for t, o, _r, _k in self.delete})

    def as_dict(self):
        return {'slots': self.n_slots, 'deleted': self.n_delete,
                'boosts': self.n_boost, 'enemies': len(self.affected),
                'skipped': len(self.skipped),
                'infeasible': len(self.infeasible),
                'by_reason': self._by_reason()}

    def _by_reason(self):
        d = {}
        for _t, _o, _k, r in self.skipped:
            d[r] = d.get(r, 0) + 1
        return d


def build_plan(gd, npc2tables, enemies, item_slots, selection, slot_allowed):
    """算出变更计划。

    gd           : 有 tabs / slots_of 的掉落表容器
    npc2tables   : {npc_id: {'tables': [(表名, ...)]}}
    enemies      : [{'gid', 'npc_ids', 'tables'}]
    item_slots   : {item_key: [(tname, off, rate)]}
    selection    : 需有 gdel / checked / boost / gwl / ewl
    slot_allowed : 函数 (tname) -> bool（是否属于本工具的作用域）
    """
    plan = ChangePlan()
    gdel = getattr(selection, 'gdel', None) or set()
    checked = getattr(selection, 'checked', None) or {}
    boost = getattr(selection, 'boost', None) or {}
    gwl = getattr(selection, 'gwl', None) or set()
    ewl = getattr(selection, 'ewl', None) or {}

    gid2ids = {e['gid']: e['npc_ids'] for e in enemies}
    # ：rate 反查索引一次建好（避免每槽 O(n) 线性扫）
    _rate_idx = {}
    for _k, _lst in item_slots.items():
        for _t, _o, _r in _lst:
            _rate_idx.setdefault((_t, _o), _r)
    # 表名 -> 拥有该表的敌人组。敌人白名单只保护自己名下的槽（P4 彻底版）
    tbl2enemies = {}
    for e in enemies:
        for t in e['tables']:
            tbl2enemies.setdefault(t, set()).add(e['gid'])

    seen = set()                       # (tname, off) 去重：一个槽只写一次
    del_slots = {}                     # (tname, off) -> old_rate

    def mark_delete(tname, off, rate, k):
        if (tname, off) in seen:
            return
        seen.add((tname, off))
        del_slots[(tname, off)] = rate
        plan.delete.append((tname, off, rate, k))

    def mark_skip(tname, off, k, reason):
        plan.skipped.append((tname, off, k, reason))

    # ── ① 全局删除 ──────────────────────────────────────────────
    for k in gdel:
        if k in gwl:
            continue
        if k in boost:
            # 与 ② 路径同口径（）：互斥跳过必须有记录，否则用户
            # 看不到"为什么我勾了却没删"，成为无迹可查的幽灵勾选
            plan.skipped.append(('', -1, k, REASON_BOOSTED))
            continue                    # 提率与删除互斥
        try:
            ik = int(k)
        except (TypeError, ValueError):
            continue
        for tname, off, rate in item_slots.get(ik, []):
            owners = tbl2enemies.get(tname)
            if owners and any(k in (ewl.get(gid) or []) for gid in owners):
                mark_skip(tname, off, k, REASON_EWL)
                continue
            if not slot_allowed(tname):
                mark_skip(tname, off, k, REASON_NONDROP)
                continue
            mark_delete(tname, off, rate, k)

    # ── ② 按敌人勾选删除 ────────────────────────────────────────
    for (gid, k), v in checked.items():
        if not v or k in gwl:
            continue
        if k in boost:
            # ：互斥跳过必须有记录（REASON_BOOSTED 启用——
            # 否则预设套用时留下幽灵勾选，预览计数虚高且无迹可查）
            plan.skipped.append(('', -1, k, REASON_BOOSTED))
            continue
        if k in (ewl.get(gid) or []):
            continue
        try:
            ik = int(k)
        except (TypeError, ValueError):
            continue
        hit = False
        for nid in gid2ids.get(gid, []):
            for t in (npc2tables.get(nid, {}).get('tables') or []):
                tname = t[0]
                if not slot_allowed(tname):
                    continue
                for ent in gd.slots_of(tname, ik):
                    if not ent:
                        continue
                    off, rate = ent[0], ent[1]
                    if (tname, off) in seen:
                        continue
                    seen.add((tname, off))
                    del_slots[(tname, off)] = rate
                    plan.delete.append((tname, off, rate, k))
                    hit = True
        if hit:
            plan.affected.add(gid)

    # ── ③ 提率 ──────────────────────────────────────────────────
    by_tab = {}
    for k, p in boost.items():
        if k in gwl:
            continue                    # 白名单优先于提率（门禁 F10）
        try:
            ik = int(k)
        except (TypeError, ValueError):
            continue
        if p <= 0:
            continue
        for tname, tabx in gd.tabs.items():
            if not slot_allowed(tname):
                continue
            if ik in tabx:
                by_tab.setdefault(tname, []).append((ik, tabx[ik][0], min(p, 1.0)))

    for tname, lst in by_tab.items():
        tab = gd.tabs.get(tname, {})
        boost_iks = {ik for ik, _o, _p in lst}
        boost_offs = {o for _ik, o, _p in lst}
        others, fixed_w, blockers = [], 0, {}
        # ⚠️ others 必须读「①② 清零之后的现场值」，不是原始值（M2 已写明）。
        #    被删除的槽此时权重是 0，因此自动排除在 others 之外；
        #    若按原始值算，缩放基数会变大 → 提率结果与写盘不一致。
        for kk, (o, _orig) in tab.items():
            if o in boost_offs:
                continue
            if (tname, o) in del_slots:      # 已在本计划里被删除 → 现场值为 0
                continue
            cur_r = _rate_at(item_slots, tname, o, _rate_idx)
            if cur_r is None:
                continue
            if 0 < cur_r < W:
                others.append((o, cur_r))
            elif cur_r >= W:
                fixed_w += cur_r
                # 占住额度的物品，UI 要能点名。按 key 累加权重：同一物品的
                # 多个槽都占额度，合计才代表"它占了多少"
                blockers[kk] = blockers.get(kk, 0) + cur_r
        # 同表重复槽也要计入（），且**必须两个分支都处理**：
        #   0 < r < W → others（可缩放）
        #   r >= W    → fixed_w（固定权重，入分母不缩放 —— ：
        #               旧条件把 W 槽整个排除出解算，实测 798 张表含 W 槽，
        #               "设为必掉"只到 50%）
        # ⚠️ 我第一版只抄了 others 分支、漏了 fixed_w —— 结果满权重的重复槽
        #    没占住空间，把本该"预算不足判不可行"的表误判成可行，改了一堆
        #    不该改的槽。写"消除重复"的模块时更要小心别抄漏分支。
        for (tn, kk), dups in (getattr(gd, 'tab_dups', None) or {}).items():
            if tn != tname or kk in boost_iks:
                continue
            for o, r in dups:
                if (tname, o) in del_slots:
                    continue
                if 0 < r < W:
                    others.append((o, r))
                elif r >= W:
                    fixed_w += r
                    # 同上：dups 分支别漏（抄漏分支是老毛病）
                    blockers[kk] = blockers.get(kk, 0) + r
        cur = sum(r for _o, r in others)
        w_of, new_sum, feasible = _boost_plan(lst, cur, fixed_w)
        if not feasible:
            plan.infeasible.append((tname, sorted(str(i) for i in boost_iks)))
            # 成因归类：与 _boost_plan 的守卫共用 _sole_drop_conflict，
            # 不在这里另写一份判据（判据抄成多份是这个模块的历史病根）
            plan.infeasible_reason[tname] = (
                INFEAS_SOLE_DROP if _sole_drop_conflict(lst, cur, fixed_w)
                else INFEAS_SUM_OVER_100)
            # 排序口径：**占额度多的排前面**（那通常是最该删的那个），
            #   权重相同再按 key 字符串升序 —— 后者是为了纯函数性
            #   （同输入两次必须完全一致，_gate_plan P2 盯这条）。
            #   ⚠️ UI 展示要设上限（前 3 个 + "等 N 个"），所以这个顺序
            #      直接决定用户先看到谁、先删谁，不能按 key 随便排。
            if fixed_w > 0:
                plan.infeasible_blockers[tname] = [
                    str(k) for k, _w in
                    sorted(blockers.items(), key=lambda x: (-x[1], str(x[0])))]
            for ik, o, _p in lst:
                mark_skip(tname, o, str(ik), REASON_INFEASIBLE)
            continue
        for ik, o, _p in lst:
            new_r = w_of.get(ik, W)
            # 只记录"真的变了"的写入。目标权重算出来等于原值时（例如该表
            # 本来就只有这一个满权重槽），写进去字节不变 —— 计划里不该留
            # 这种"改了等于没改"的条目，否则预览数字会虚高。
            if new_r != tab[ik][1]:
                plan.boost.append((tname, o, tab[ik][1], new_r, str(ik)))
        # 提率物品的**重复槽清零**：游戏里同 key 多槽会重复计入权重
        # （七轮 P1：不清零的话，实测 50% 变成 14%）。这一步旧实现有，别漏。
        for ik, _o, _p in lst:
            for o, r in (getattr(gd, 'tab_dups', None) or {}).get((tname, ik), []):
                if r != 0:
                    plan.boost.append((tname, o, r, 0, str(ik) + '#dup'))
        if new_sum >= 0:
            scale = (new_sum / cur) if cur > 0 else 0
            for o, r in others:
                new_r = max(1, int(r * scale))
                if new_r != r:
                    plan.boost.append((tname, o, r, new_r, '*'))

    return plan


def _rate_at(item_slots, tname, off, _idx=None):
    """从 item_slots 反查某槽的当前权重（提需要读原值）。
    ：旧版每次全表线性扫，多表提率时 O(槽数×物品数) 卡顿。
    传入 _idx（build_plan 建一次的 (tname,off)->rate 索引）则 O(1)。"""
    if _idx is not None:
        return _idx.get((tname, off))
    for _k, lst in item_slots.items():
        for t, o, r in lst:
            if t == tname and o == off:
                return r
    return None
