# -*- coding: utf-8 -*-
"""nojunk_ui.py —— NoJunk 重做版 · GUI（tkinter，标准库零依赖）

设计原则（重做的原因，昨天丢过一次 bind）：
  1. **所有 bind 集中在下面 BIND_TABLE**，_bindcheck.py 逐条校验，少一条就报错
  2. 状态全在 AppState，GUI 只显示 + 收集意图，不自己存副本
  3. 一个方法一件事，方法名即意图
  4. 点击语义符合自然习惯：点勾选列才切换，点其他列只选中

交互（已和用户逐条确认过的"答案"）：
  - 敌人列表平铺三列：敌人 / 分类 / 删·总；分类用下拉筛（带计数）
  - 掉落表点第一列循环 ☐不删 → ☑要删 → ⊠保护此敌人 → ☐；点其他列只选中
  - 右键掉落行：加入该敌人白名单 / 设为必掉 / 自定义掉率… / 取消
  - 颜色：红=删 蓝=单怪保护 绿=全局保护
  - 预设两套分开：按敌人页=敌人预设，全局页=全局预设；数字键 1-9 按当前页套用
"""
import os, sys, json, threading, queue
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox, filedialog, simpledialog

import nojunk_core as core
import plan

# ── bind 清单：_bindcheck.py 会逐条校验，一条不许少 ───────────────
BIND_TABLE = [
    ('root',       '<Key>',                 '_on_digit_key'),
    ('lang_box',   '<<ComboboxSelected>>',  '_on_lang'),
    ('cat_box',    '<<ComboboxSelected>>',  '_on_cat_filter'),
    ('sub_box',    '<<ComboboxSelected>>',  '_on_sub_filter'),
    ('epreset_box', '<<ComboboxSelected>>', '_apply_enemy_preset'),
    ('gpreset_box', '<<ComboboxSelected>>', '_apply_global_preset'),
    ('enemy_tree', '<Button-1>',            '_on_enemy_click'),
    ('enemy_tree', '<Button-3>',            '_on_enemy_right'),
    ('drop_tree',  '<Button-1>',            '_on_drop_click'),
    ('drop_tree',  '<Button-3>',            '_on_drop_right'),
    ('g_tree',     '<Button-1>',            '_on_global_click'),
    ('g_tree',     '<Button-3>',            '_on_global_right'),
    # ：nb 的 notebook 切换原先没登记，BIND_TABLE 的注释却写着
    # "一条不许少" —— 名不副实会让这个表失去作为清单的意义。补齐。
    ('nb',         '<<NotebookTabChanged>>', '_on_tab'),
]
LAMBDA_BINDS = [
    ('drop_tree',  '<space>'),
    ('drop_tree',  '<Return>'),
    ('g_tree',     '<space>'),      # ：补登记
]

SYM = {'none': '☐', 'del': '☑', 'wl': '⊠', 'gwl': '⊞', 'boost': '★',
       'ext': '—'}
TAG = {'del': 'dele', 'wl': 'wl', 'gwl': 'gw', 'boost': 'boo', 'ext': 'ext'}

# 官方 desc 标签分桶白名单（_parse_desc 输出：类型标签 / 阵营标签 / 附加标签）
_TAG_TYPE = ('Tier', 'Elite', 'Boss', 'Empty')
_TAG_CAMP = ('NPC', 'Player', 'Monster', 'Animal', 'Boss', 'Friendly', 'Enemy',
             'Ally', 'Neutral', 'Criminal', 'Domestic', 'Wild', 'All', 'Nature')

# 掉落列表分类父行 iid 分隔符（普通物品行 iid 为 'gid|itemkey'）
_CATROW = '|__cat__|'


class App:
    def __init__(self, root, st=None):
        self.root = root
        self.st = st or core.AppState()
        self.st.langs = core.load_langs()
        # 必须先加载配置再读 lang：AppState 构造时已 load_cfg（幂等），
        # 此处再调一次是为了兜底外部传入的 st；顺序必须早于 cfg.get('lang')。
        self.st.load_cfg()
        self.st.lang = (self.st.cfg.get('lang')
                        or core.detect_os_lang())
        if self.st.lang not in self.st.langs:
            self.st.lang = 'en'
        self.st.game_dir = self.st.cfg.get('game_dir') or core.auto_find_game()
        self.root.title('NoJunk2 %s — Crimson Desert' % core.VERSION)
        self._init_style()
        self._build_ui()
        if self.st.game_dir and os.path.isdir(self.st.game_dir):
            self._load_game()
        # ⚠️ 必须把自己带到前台：从 pythonw/命令行启动的窗口默认**不是活动窗口**，
        #    此时 Tk 既不重绘也不处理事件 —— 用户看到的就是
        #    「界面不动，点一下鼠标才突突突全刷出来」（用户实测报过这个）。
        #    focus_force 只在启动时做一次（任何程序启动都该这样），不是反复抢焦点。
        try:
            self.root.update_idletasks()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    # ════════════════════════════════════════════════════════
    #  视觉体系（ 重设计：clam 主题 + 统一色板/间距/字体，
    #  不再沿用旧版散装样式）
    # ════════════════════════════════════════════════════════

    C = {'bg': '#f5f6f8', 'panel': '#ffffff', 'border': '#d5d9e0',
         'text': '#1f2329', 'muted': '#6b7280', 'accent': '#1565c0',
         'accent_dk': '#0d47a1', 'danger': '#c62828', 'danger_dk': '#8e0000',
         'ok': '#2e7d32', 'warn': '#e65100', 'row_alt': '#eef1f5'}

    def _init_style(self):
        s = ttk.Style(self.root)
        try:
            s.theme_use('clam')
        except Exception:
            pass
        c = self.C
        self.root.configure(bg=c['bg'])
        base = ('Segoe UI', 11)
        s.configure('.', font=base, background=c['bg'], foreground=c['text'],
                    bordercolor=c['border'])
        s.configure('TFrame', background=c['bg'])
        s.configure('TLabel', background=c['bg'])
        s.configure('TLabelframe', background=c['bg'], bordercolor=c['border'])
        s.configure('TLabelframe.Label', background=c['bg'],
                    foreground=c['muted'], font=('Segoe UI', 10, 'bold'))
        s.configure('TButton', padding=(12, 6))
        s.configure('TCheckbutton', background=c['bg'])
        s.configure('TNotebook', background=c['bg'])
        s.configure('TNotebook.Tab', padding=(16, 7), font=('Segoe UI', 11))
        s.configure('Treeview', rowheight=30, font=('Segoe UI', 11),
                    fieldbackground=c['panel'],
                    background=c['panel'], bordercolor=c['border'])
        s.configure('Treeview.Heading', font=('Segoe UI', 10, 'bold'),
                    padding=(6, 5))
        s.map('Treeview', background=[('selected', '#dbe7f6')],
              foreground=[('selected', c['text'])])
        s.configure('Muted.TLabel', foreground=c['muted'])
        s.configure('Card.TFrame', background=c['panel'])
        # 主操作/危险按钮字体（tk.Button 用）
        self.f_main = tkfont.Font(family='Segoe UI', size=12, weight='bold')
        self.f_danger = tkfont.Font(family='Segoe UI', size=10, weight='bold')

    # ════════════════════════════════════════════════════════
    #  界面骨架
    # ════════════════════════════════════════════════════════

    def _build_ui(self):
        st = self.st
        # 重建后默认回到第一页（敌人页），防止切语言后 cur_tab 残留旧值
        st.cur_tab = 0
        DEFAULT_GEOM = '1500x860'
        g = st.cfg.get('win_geometry')
        try:
            if not g or int(g.split('x')[0]) < 1200:
                g = DEFAULT_GEOM
        except Exception:
            g = DEFAULT_GEOM
        # 小屏兜底：默认宽超出屏幕时收窄
        try:
            sw = self.root.winfo_screenwidth()
            dw = int(g.split('x')[0])
            if sw and dw > sw - 40:
                g = '%dx%s' % (max(1120, sw - 40), g.split('x')[1])
        except Exception:
            pass
        self.root.geometry(g)
        self.root.minsize(1080, 600)
        # 退出时记忆窗口尺寸/位置（_on_close 写入配置），下次启动恢复
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

        # 顶栏：游戏目录一行（输入框吃满剩余宽度），右侧语言角落
        top = ttk.Frame(self.root)
        top.pack(fill='x', padx=10, pady=(10, 6))
        ttk.Label(top, text=st.t('gamedir')).pack(side='left')
        self.dir_var = tk.StringVar(value=st.game_dir)
        ttk.Entry(top, textvariable=self.dir_var).pack(
            side='left', fill='x', expand=True, padx=(6, 6))
        ttk.Button(top, text=st.t('browse'), command=self._pick_dir).pack(side='left')
        self.load_btn = ttk.Button(top, text=st.t('load'), command=self._load_game)
        self.load_btn.pack(side='left', padx=(6, 0))
        # 一键重建映射：游戏更新后点一下即可，不用开命令行。
        # 低频但关键 —— 不重建的话敌人会整片变成「暂无对应表」，
        # 用户存的勾选静默失效。
        self.rebuild_btn = ttk.Button(top, text=st.t('rebuild_map'),
                                      command=self._rebuild_map)
        self.rebuild_btn.pack(side='left', padx=(6, 0))
        # 撤销上次应用：备份本来每次写盘都有，加个入口几乎是白捡。
        # 以前只有「恢复原版」一个极端选项，中间没有退路。
        self.undo_btn = ttk.Button(top, text=st.t('undo_last'),
                                   command=self._undo_last)
        self.undo_btn.pack(side='left', padx=(6, 0))
        # 一键自检：生成可复制的诊断报告（出问题时报给作者看）
        ttk.Button(top, text=st.t('selfcheck'),
                   command=self._self_check).pack(side='left', padx=(6, 0))

        # 右上角角落：语言下拉 + 低频入口（语言目录 / 关于）
        corner = ttk.Frame(top)
        corner.pack(side='right', padx=(14, 0))
        ttk.Button(corner, text=st.t('lang_open_dir'),
                   command=self._open_lang_dir).pack(side='right', padx=(6, 0))
        ttk.Button(corner, text=st.t('about'),
                   command=self._about).pack(side='right', padx=(12, 0))
        self.lang_codes = sorted(st.langs, key=lambda c: (c != 'en', c))
        self.lang_box = ttk.Combobox(corner, width=13, state='readonly',
                                     values=[st.langs[c]['name'] for c in self.lang_codes])
        self.lang_box.set(st.langs[st.lang]['name'])
        self.lang_box.pack(side='right')
        self.lang_box.bind('<<ComboboxSelected>>', self._on_lang)
        ttk.Label(corner, text=st.t('lang_label'),
                  style='Muted.TLabel').pack(side='right', padx=(0, 4))

        # 数字键 1-9 = 套用预设（BIND_TABLE 登记项）
        self.root.bind('<Key>', self._on_digit_key)

        # 底部状态栏：先 pack(side='bottom')，标签页再占剩余空间
        sbar = ttk.Frame(self.root, relief='groove', borderwidth=1)
        sbar.pack(fill='x', side='bottom', padx=10, pady=(0, 8))
        self.status = ttk.Label(sbar, text=st.t('ready'), foreground='#444',
                                padding=(8, 4))
        self.status.pack(side='left', fill='x', expand=True)
        ttk.Label(sbar, text=st.t('prio_note'), style='Muted.TLabel',
                  padding=(8, 4)).pack(side='right')

        # 标签页
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill='both', expand=True, padx=10, pady=(0, 8))
        self.nb.bind('<<NotebookTabChanged>>', self._on_tab)
        self.tab1 = ttk.Frame(self.nb)
        self.tab2 = ttk.Frame(self.nb)
        self.nb.add(self.tab1, text='  ' + st.t('tab_enemy') + '  ')
        self.nb.add(self.tab2, text='  ' + st.t('tab_global') + '  ')
        self._build_enemy_tab(self.tab1)
        self._build_global_tab(self.tab2)

    # ── 按敌人页 ────────────────────────────────────────────

    def _build_enemy_tab(self, p):
        st = self.st
        # 敌人预设（只存勾选 + 敌人白名单，与全局预设分开）
        ef = ttk.Frame(p)
        ef.pack(fill='x', pady=(0, 4))
        ttk.Label(ef, text=st.t('epreset_label')).pack(side='left', padx=(0, 4))
        self.epreset_var = tk.StringVar()
        self.epreset_box = ttk.Combobox(ef, textvariable=self.epreset_var,
                                        width=16, state='readonly',
                                        values=st.enemy_preset_names())
        self.epreset_box.pack(side='left')
        # 选择即套用（与数字键 1-9 语义一致）；不绑的话用户选了预设毫无反应（反馈过「预设没有作用」）
        self.epreset_box.bind('<<ComboboxSelected>>', self._apply_enemy_preset)
        ttk.Button(ef, text=st.t('preset_apply'),
                   command=self._apply_enemy_preset).pack(side='left', padx=4)
        ttk.Button(ef, text=st.t('preset_save'),
                   command=self._save_enemy_preset).pack(side='left')
        ttk.Button(ef, text=st.t('preset_del'),
                   command=self._del_enemy_preset).pack(side='left', padx=4)
        ttk.Button(ef, text=st.t('preset_export'),
                   command=lambda: self._export_presets('enemy')).pack(side='left', padx=(8, 0))
        ttk.Button(ef, text=st.t('preset_import'),
                   command=lambda: self._import_presets('enemy')).pack(side='left', padx=4)
        # 帮助按钮：弹出预设使用流程说明（替代原黄色快捷键提示标签）
        ttk.Button(ef, text='?', width=3,
                   command=self._show_preset_help).pack(side='left', padx=(10, 0))

        paned = tk.PanedWindow(p, orient='horizontal', sashwidth=4)
        paned.pack(fill='both', expand=True)
        # 左：敌人列表（平铺，不做树形嵌套）
        lf = ttk.LabelFrame(paned, text=st.t('enemies'))
        paned.add(lf, minsize=420, stretch='always')
        sf = ttk.Frame(lf)
        sf.pack(fill='x', padx=4, pady=(4, 3))
        ttk.Label(sf, text=st.t('search')).pack(side='left', padx=(0, 4))
        self.search_var = tk.StringVar()
        # 搜索防抖：快速输入不每键全量重刷，停顿 250ms 后才刷新一次
        self.search_var.trace_add('write', self._on_search_enemy)
        # 显式保存引用（别用 winfo_children()[-1] 这种隐式索引 —— 加个按钮就取错了）
        self.search_entry = ttk.Entry(sf, textvariable=self.search_var)
        self.search_entry.pack(side='left', fill='x', expand=True)
        ttk.Button(sf, text='×', width=3,
                   command=lambda: self.search_var.set('')).pack(side='right')

        # 官方标签筛选：chip 平铺改弹出面板（旧版平铺换行，窗口稍窄就截断
        # 文字——用户截图实证）。筛选状态以 st.chip 为权威，按钮显示计数。
        fl = ttk.Frame(lf)
        fl.pack(fill='x', padx=4, pady=(0, 4))
        self._chip_vars = {}
        self.chip_btn = ttk.Button(fl, text=self._chip_btn_text(),
                                   command=self._open_filter)
        self.chip_btn.pack(side='left')
        ttk.Button(fl, text=st.t('chip_clear'), width=9,
                   command=self._chip_clear).pack(side='left', padx=(6, 0))

        # 纵向 + 横向滚动条：拆组标签/长敌人名被截断时可横向拖动（响应式兜底）
        etw = ttk.Frame(lf)
        etw.pack(fill='both', expand=True)
        self.enemy_tree = ttk.Treeview(etw, columns=('n', 'ik', 'g', 'd'),
                                       show='headings', height=30)
        for c, w, k in (('n', 280, 'enemies'), ('ik', 110, 'col_infokey'),
                        ('g', 130, 'col_group'), ('d', 72, 'col_dtotal')):
            self.enemy_tree.heading(c, text=st.t(k))
            self.enemy_tree.column(c, width=w,
                                   anchor='center' if c != 'n' else 'w')
        esb = ttk.Scrollbar(etw, orient='vertical', command=self.enemy_tree.yview)
        ehsb = ttk.Scrollbar(etw, orient='horizontal', command=self.enemy_tree.xview)
        self.enemy_tree.configure(yscrollcommand=esb.set, xscrollcommand=ehsb.set)
        self.enemy_tree.grid(row=0, column=0, sticky='nsew')
        esb.grid(row=0, column=1, sticky='ns')
        ehsb.grid(row=1, column=0, sticky='ew')
        etw.rowconfigure(0, weight=1)
        etw.columnconfigure(0, weight=1)
        self.enemy_tree.bind('<Button-1>', self._on_enemy_click)
        self.enemy_tree.bind('<Button-3>', self._on_enemy_right)
        # ⚠️ 左栏必须给 minsize：PanedWindow 不设的话会把左栏压成一条线，
        #    敌人名被挤成一两个字一行（用户截图实测过）。
        #    sashpos 没用 —— 它会被后续布局重算覆盖，只有 minsize 是硬约束。
        #    注意：ttk.PanedWindow 在本环境不支持 pane minsize，用经典 tk.PanedWindow。

        # 右：掉落列表
        rf = ttk.LabelFrame(paned, text=st.t('drops'))
        paned.add(rf, minsize=640, stretch='always')
        # 置顶开关放在列表【上方】的工具条 —— 放在底部根本没人看见
        tbar = ttk.Frame(rf)
        tbar.pack(fill='x', padx=4, pady=(4, 0))
        self.pin_del = tk.BooleanVar(value=False)
        self.pin_wl = tk.BooleanVar(value=False)
        ttk.Checkbutton(tbar, text=st.t('pin_del'), variable=self.pin_del,
                        command=self._on_enemy_click).pack(side='left')
        ttk.Checkbutton(tbar, text=st.t('pin_wl'), variable=self.pin_wl,
                        command=self._on_enemy_click).pack(side='left', padx=10)
        dtw = ttk.Frame(rf)
        # pack 推迟到底部行之后（见 bot 处注释）：expand 列表最后 pack
        self.drop_tree = ttk.Treeview(dtw, columns=('c', 'name', 'cat', 'p', 'prob', 'qty', 'type', 'table'),
                                      show='headings', height=28)
        for c, w, k in (('c', 56, 'col_check'), ('name', 270, 'col_name'),
                        ('cat', 130, 'col_cat'), ('p', 100, 'col_rate'),
                        ('prob', 92, 'col_prob'), ('qty', 72, 'col_qty'),
                        ('type', 72, 'col_type'), ('table', 190, 'col_table')):
            self.drop_tree.heading(c, text=st.t(k))
            self.drop_tree.column(c, width=w,
                                  anchor='center' if c != 'name' else 'w')
        # 表头排序：点表头按列升序，再点切降序（状态存 self._d_sort，切语言保留）
        if not hasattr(self, '_d_sort'):
            self._d_sort = None
        for c in ('c', 'name', 'cat', 'p', 'prob', 'qty', 'type', 'table'):
            self.drop_tree.heading(c, command=lambda c=c: self._d_sort_by(c))
        try:
            self.drop_tree.tag_configure('dele', foreground='#c62828')
            self.drop_tree.tag_configure('wl', foreground='#1565c0')
            self.drop_tree.tag_configure('gw', foreground='#2e7d32')
            self.drop_tree.tag_configure('boo', foreground='#e65100')
            self.drop_tree.tag_configure('catg', foreground='#6a6a6a')
            self.drop_tree.tag_configure('ext', foreground='#9aa1ab')
        except Exception:
            pass
        dsb = ttk.Scrollbar(dtw, orient='vertical', command=self.drop_tree.yview)
        dhsb = ttk.Scrollbar(dtw, orient='horizontal', command=self.drop_tree.xview)
        self.drop_tree.configure(yscrollcommand=dsb.set, xscrollcommand=dhsb.set)
        self.drop_tree.grid(row=0, column=0, sticky='nsew')
        dsb.grid(row=0, column=1, sticky='ns')
        dhsb.grid(row=1, column=0, sticky='ew')
        dtw.rowconfigure(0, weight=1)
        dtw.columnconfigure(0, weight=1)
        self.drop_tree.bind('<Button-1>', self._on_drop_click)
        self.drop_tree.bind('<Button-3>', self._on_drop_right)
        self.drop_tree.bind('<space>', lambda e: self._toggle_current_drop())
        self.drop_tree.bind('<Return>', lambda e: self._toggle_current_drop())

        bot = ttk.Frame(rf)
        # ⚠️ pack 顺序陷阱：expand=True 的列表必须先于底部行 pack 的话，
        # 窗口不够高时底部按钮行会被压到 0 高（截图实证：整排消失）。
        # 所以底部行先以 side='bottom' 占位，列表最后 expand 填剩余。
        bot.pack(fill='x', side='bottom', pady=(4, 0))
        # 第一行：常规批量操作（低频语义放一起）
        row1 = ttk.Frame(bot)
        row1.pack(fill='x')
        ttk.Button(row1, text=st.t('sel_all'), command=lambda: self._sel_all(True)).pack(side='left')
        ttk.Button(row1, text=st.t('sel_none'), command=lambda: self._sel_all(False)).pack(side='left', padx=4)
        ttk.Button(row1, text=st.t('sel_cons'), command=self._sel_consumables).pack(side='left', padx=4)
        ttk.Button(row1, text=st.t('sel_wl'), command=self._sel_enemy_wl).pack(side='left', padx=4)
        ttk.Button(row1, text=st.t('sel_must'), command=self._sel_must_drop).pack(side='left', padx=4)
        # 第二行：危险操作（红色、拉开间距）→ 核心操作「应用删除」（主按钮，最右、加宽加粗）
        row2 = ttk.Frame(bot)
        row2.pack(fill='x', pady=(6, 0))
        ttk.Button(row2, text=st.t('export_patch'),
                   command=self._export_patch).pack(side='left')
        self.restore_btn = tk.Button(row2, text='⚠ ' + st.t('restore_vanilla'),
                                     fg='#ffffff', bg='#c62828', activebackground='#8e0000',
                                     activeforeground='#ffffff', bd=0, padx=12, pady=3,
                                     font=self.f_danger, cursor='hand2',
                                     command=self._restore_vanilla)
        self.restore_btn.pack(side='right', padx=(10, 0))
        self.apply_btn = tk.Button(row2, text=st.t('apply'), fg='#ffffff', bg='#1565c0',
                                   activebackground='#0d47a1', activeforeground='#ffffff',
                                   bd=0, padx=26, pady=4, font=self.f_main, cursor='hand2',
                                   command=self._apply)
        self.apply_btn.pack(side='right')

        note = ttk.Frame(rf)
        note.pack(fill='x', side='bottom', pady=(0, 2))
        # 图例：一行紧凑（色块即符号色；提率列复用 col_target 文案，不新增键）
        leg = ttk.Frame(note)
        leg.pack(anchor='w', padx=8)
        ttk.Label(leg, text=st.t('legend_del'), foreground=self.C['danger']).pack(side='left')
        ttk.Label(leg, text=st.t('legend_wl'), foreground=self.C['accent']).pack(side='left', padx=(16, 0))
        ttk.Label(leg, text=st.t('legend_gwl'), foreground=self.C['ok']).pack(side='left', padx=(16, 0))
        ttk.Label(leg, text='%s %s' % (SYM['boost'], st.t('col_target')),
                  foreground=self.C['warn']).pack(side='left', padx=(16, 0))
        ttk.Label(note, text=st.t('gear_note'),
                  style='Muted.TLabel').pack(anchor='w', padx=8)
        # 列表最后 pack：底部行已用 side='bottom' 占位，剩余空间全给列表
        dtw.pack(fill='both', expand=True, padx=4, pady=4)

    # ── 全局页 ──────────────────────────────────────────────

    def _build_global_tab(self, p):
        st = self.st
        # 全局预设（只存全局删除 + 提率 + 价格阈值）——放顶部，不放底部（会被挤掉）
        pf = ttk.Frame(p)
        pf.pack(fill='x', padx=2, pady=(0, 4))
        ttk.Label(pf, text=st.t('gpreset_label')).pack(side='left', padx=(0, 4))
        self.gpreset_var = tk.StringVar()
        self.gpreset_box = ttk.Combobox(pf, textvariable=self.gpreset_var,
                                        width=16, state='readonly',
                                        values=st.global_preset_names())
        self.gpreset_box.pack(side='left')
        # 选择即套用（与数字键 1-9 语义一致）
        self.gpreset_box.bind('<<ComboboxSelected>>', self._apply_global_preset)
        ttk.Button(pf, text=st.t('preset_apply'),
                   command=self._apply_global_preset).pack(side='left', padx=4)
        ttk.Button(pf, text=st.t('preset_save'),
                   command=self._save_global_preset).pack(side='left')
        ttk.Button(pf, text=st.t('preset_del'),
                   command=self._del_global_preset).pack(side='left', padx=4)
        ttk.Button(pf, text=st.t('preset_export'),
                   command=lambda: self._export_presets('global')).pack(side='left', padx=(8, 0))
        ttk.Button(pf, text=st.t('preset_import'),
                   command=lambda: self._import_presets('global')).pack(side='left', padx=4)
        # 帮助按钮：弹出预设使用流程说明（与敌人页一致）
        ttk.Button(pf, text='?', width=3,
                   command=self._show_preset_help).pack(side='left', padx=(10, 0))

        # 第 2 行：价格 / 分类 / 搜索 / 置顶 —— 按语义分 Frame 分组，不再全挤一行
        top = ttk.Frame(p)
        top.pack(fill='x', pady=(0, 4))
        f_price = ttk.Frame(top)
        f_price.pack(side='left', padx=(2, 10))
        ttk.Label(f_price, text=st.t('price_below')).pack(side='left')
        self.price_var = tk.StringVar()
        ttk.Entry(f_price, textvariable=self.price_var, width=8).pack(side='left', padx=3)
        ttk.Button(f_price, text=st.t('price_pick'), command=self._apply_price).pack(side='left')

        f_cat = ttk.Frame(top)
        f_cat.pack(side='left', padx=(2, 10))
        ttk.Label(f_cat, text=st.t('category')).pack(side='left', padx=(0, 4))
        self.cat_var = tk.StringVar()
        # 首项「全部」= 不过滤；不设默认值的话 readonly 下拉会空着，误以为筛选失效
        self.cat_var.set(st.t('gf_all'))
        self.cat_box = ttk.Combobox(f_cat, textvariable=self.cat_var, width=13,
                                    state='readonly',
                                    values=[st.t('gf_all')] + [self._cat_label(c) for c in self._main_cats()])
        self.cat_box.pack(side='left')
        # ⚠️ 类别筛选：不绑事件的话选了类别列表毫无反应（用户实测报过「点类别掉落物不变化」）
        self.cat_box.bind('<<ComboboxSelected>>', self._on_cat_filter)
        # 官方子类（subCategory）二级筛选
        self.sub_var = tk.StringVar()
        self.sub_var.set(st.t('gf_all'))
        self.sub_box = ttk.Combobox(f_cat, textvariable=self.sub_var, width=13,
                                    state='readonly',
                                    values=[st.t('gf_all')] + [self._sub_name_label(s)
                                                               for s in self._sub_cats()])
        self.sub_box.pack(side='left', padx=(4, 0))
        self.sub_box.bind('<<ComboboxSelected>>', self._on_sub_filter)
        ttk.Button(f_cat, text=st.t('cat_to_wl'), command=lambda: self._cat_bulk('wl')).pack(side='left', padx=4)
        ttk.Button(f_cat, text=st.t('cat_to_del'), command=lambda: self._cat_bulk('del')).pack(side='left')

        f_search = ttk.Frame(top)
        f_search.pack(side='left', padx=(2, 10))
        ttk.Label(f_search, text=st.t('search_item')).pack(side='left', padx=(0, 3))
        self.gsearch_var = tk.StringVar()
        # 搜索防抖：快速输入不每键全量重刷，停顿 250ms 后才刷新一次
        self.gsearch_var.trace_add('write', self._on_search_global)
        ttk.Entry(f_search, textvariable=self.gsearch_var, width=16).pack(side='left')
        ttk.Button(f_search, text='×', width=3,
                   command=lambda: self.gsearch_var.set('')).pack(side='left')

        f_pin = ttk.Frame(top)
        f_pin.pack(side='right')
        # 置顶开关：复用敌人页创建的同一组变量（两个页勾选状态保持一致）
        # ⚠️ 不能在这里再 new 一个 BooleanVar —— 那会覆盖敌人页的，两边就不同步了
        ttk.Checkbutton(f_pin, text=st.t('pin_del'), variable=self.pin_del,
                        command=self._refresh_global).pack(side='left')
        ttk.Checkbutton(f_pin, text=st.t('pin_wl'), variable=self.pin_wl,
                        command=self._refresh_global).pack(side='left', padx=6)
        # 奖励类放行开关（默认保护 + 显式放行 + 二次确认）
        # 默认不勾 = 预购/版本/推广/交易 的槽一律不动
        self.include_nondrop = tk.BooleanVar(value=False)
        ttk.Checkbutton(f_pin, text=st.t('nondrop_include'),
                        variable=self.include_nondrop,
                        command=self._on_include_nondrop).pack(side='left', padx=6)

        gtw = ttk.Frame(p)
        # pack 推迟到底部行之后（同敌人页：底部行先 side='bottom' 占位）
        self.g_tree = ttk.Treeview(gtw, columns=('dl', 'wl', 'name', 'price', 'cat', 'n'),
                                   show='headings', height=26)
        for c, w, k in (('dl', 56, 'col_del'), ('wl', 60, 'col_wl'),
                        ('name', 300, 'col_name'),
                        ('price', 76, 'col_price'), ('cat', 120, 'category'),
                        ('n', 96, 'col_enemy_count')):
            self.g_tree.heading(c, text=st.t(k))
            self.g_tree.column(c, width=w,
                               anchor='center' if c not in ('name', 'cat') else 'w')
        try:
            self.g_tree.tag_configure('dele', foreground='#c62828')
            self.g_tree.tag_configure('wl', foreground='#1565c0')
        except Exception:
            pass
        gsb = ttk.Scrollbar(gtw, orient='vertical', command=self.g_tree.yview)
        ghsb = ttk.Scrollbar(gtw, orient='horizontal', command=self.g_tree.xview)
        self.g_tree.configure(yscrollcommand=gsb.set, xscrollcommand=ghsb.set)
        self.g_tree.grid(row=0, column=0, sticky='nsew')
        gsb.grid(row=0, column=1, sticky='ns')
        ghsb.grid(row=1, column=0, sticky='ew')
        gtw.rowconfigure(0, weight=1)
        gtw.columnconfigure(0, weight=1)
        self.g_tree.bind('<Button-1>', self._on_global_click)
        self.g_tree.bind('<Button-3>', self._on_global_right)
        self.g_tree.bind('<space>', lambda e: self._toggle_current_global())
        for c in ('dl', 'wl', 'name', 'price', 'cat', 'n'):
            self.g_tree.heading(c, command=lambda c=c: self._g_sort_by(c))

        bot = ttk.Frame(p)
        bot.pack(fill='x', side='bottom', pady=4)
        ttk.Button(bot, text=st.t('wl_clear'), command=self._wl_clear).pack(side='left')
        ttk.Button(bot, text=st.t('wl_default'), command=self._wl_default).pack(side='left', padx=4)
        ttk.Button(bot, text=st.t('clear_all'), command=self._clear_all).pack(side='left', padx=4)
        ttk.Button(bot, text=st.t('export_patch'), command=self._export_patch).pack(side='left', padx=(12, 0))
        # 右：危险操作（红色、拉开间距）→ 核心操作「应用删除」（主按钮，最右、加宽加粗）
        self.g_restore_btn = tk.Button(bot, text='⚠ ' + st.t('restore_vanilla'),
                                       fg='#ffffff', bg='#c62828', activebackground='#8e0000',
                                       activeforeground='#ffffff', bd=0, padx=12, pady=3,
                                       font=self.f_danger, cursor='hand2',
                                       command=self._restore_vanilla)
        self.g_restore_btn.pack(side='right', padx=(14, 6))
        self.g_apply_btn = tk.Button(bot, text=st.t('apply'), fg='#ffffff', bg='#1565c0',
                                     activebackground='#0d47a1', activeforeground='#ffffff',
                                     bd=0, padx=26, pady=4, font=self.f_main, cursor='hand2',
                                     command=self._apply)
        self.g_apply_btn.pack(side='right')
        # 列表最后 pack：底部行已占位，剩余空间全给列表
        gtw.pack(fill='both', expand=True, padx=4, pady=4)

    # ════════════════════════════════════════════════════════
    #  刷新（只读 state，不写 state）
    # ════════════════════════════════════════════════════════

    def _cat_label(self, key):
        """官方主类显示名：优先语言包翻译，缺键回退官方英文原文。"""
        if not key:
            return '-'
        t = self.st.t('cat_name_%s' % key)
        return t if t != 'cat_name_%s' % key else key

    def _cat_key(self, label):
        """下拉显示名 → 官方 mainCategory key。
        显示名可能被语言包翻译过，必须反查，不能直接拿去和原始 key 比较。"""
        if not label or label == self.st.t('gf_all'):
            return ''
        for key in self._main_cats():
            if self._cat_label(key) == label:
                return key
        return None     # 自审 P2-6：反查不到不能把翻译文本当官方 key 比

    def _main_cats(self):
        """官方 mainCategory 全集（来自固化 item_cats.json），缺失回退内置 8 类。"""
        ic = getattr(self.st, 'item_cats', None)
        if ic:
            # ：加 isinstance 守卫——值不是 dict 时 v.get 会
            # AttributeError 崩穿 Tk 回调
            return sorted({v.get('main') or '-' for v in ic.values()
                           if isinstance(v, dict) and v.get('main')})
        return ('materials', 'consumables', 'accessories', 'collectibles',
                'documents', 'equipment', 'abyss', 'misc')

    def _sub_cats(self):
        ic = getattr(self.st, 'item_cats', None)
        if ic:
            return sorted({v.get('sub') or '-' for v in ic.values()
                           if isinstance(v, dict) and v.get('sub')})
        return []

    def _sub_key(self, label):
        """子类下拉显示名 → 官方 subCategory 原文（显示名可能被翻译过，须反查）。"""
        if not label or label == self.st.t('gf_all'):
            return ''
        ic = getattr(self.st, 'item_cats', None) or {}
        for s in sorted({v.get('sub') for v in ic.values()
                         if isinstance(v, dict) and v.get('sub')}):
            if self._sub_name_label(s) == label:
                return s
        return None     # 自审 P2-6：同上

    def _is_cat_row(self, row):
        return bool(row) and _CATROW in row

    # ── 官方标签 chip 筛选器 ─────────────────────────────
    def _chip_tags(self):
        """从敌人组聚合官方标签并集，按 阵营/类型/附加 三桶分类。"""
        buckets = {'type': [], 'camp': [], 'extra': []}
        seen = set()
        for e in self.st.enemies:
            for t in (e.get('tags') or ()):
                if t in seen:
                    continue
                if t in _TAG_TYPE:
                    buckets['type'].append(t)
                elif t in _TAG_CAMP:
                    buckets['camp'].append(t)
                else:
                    buckets['extra'].append(t)
                seen.add(t)
        for b in buckets:
            buckets[b].sort()
        return buckets

    # ── 标签筛选（弹出面板式，替代平铺 chip —— 平铺在窄窗口必截断）────

    def _chip_btn_text(self):
        n = len(self.st.chip or ())
        base = self.st.t('chip_hint')
        return '%s (%d)' % (base, n) if n else base

    def _open_filter(self):
        """弹出标签筛选面板：三桶（阵营/类型/附加）分组 + 滚动，勾选即生效。"""
        st = self.st
        if getattr(self, '_filter_win', None):
            try:
                self._filter_win.lift()
                return
            except Exception:
                pass
        win = tk.Toplevel(self.root)
        self._filter_win = win
        win.title(st.t('chip_hint'))
        win.transient(self.root)
        win.configure(bg=self.C['bg'])
        win.geometry('420x480')
        win.minsize(340, 300)

        head = ttk.Frame(win)
        head.pack(fill='x', padx=10, pady=(10, 4))
        ttk.Button(head, text=st.t('chip_clear'),
                   command=self._chip_clear).pack(side='left')

        # 可滚动内容区（标签多时滚轮滚动，不会再截断）
        canvas = tk.Canvas(win, bg=self.C['bg'], highlightthickness=0)
        vsb = ttk.Scrollbar(win, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        inner = ttk.Frame(canvas)
        inner.bind('<Configure>', lambda _e: canvas.configure(
            scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=inner, anchor='nw')
        canvas.pack(side='left', fill='both', expand=True, padx=(10, 0))
        vsb.pack(side='right', fill='y', padx=(0, 6))
        # 自审 P2-5：滚轮只在筛选面板内生效（旧版 bind_all 全局劫持主列表滚轮）
        def _wheel(e):
            try:
                if not str(e.widget).startswith(str(win)):
                    return
            except Exception:
                return
            canvas.yview_scroll(-1 * (e.delta // 120), 'units')
        canvas.bind_all('<MouseWheel>', _wheel)

        labels = {'camp': st.t('chip_camp'), 'type': st.t('chip_type'),
                  'extra': st.t('chip_extra')}
        tags = self._chip_tags()
        self._chip_vars = {}
        for b in ('camp', 'type', 'extra'):
            items = tags.get(b) or []
            if not items:
                continue
            ttk.Label(inner, text=labels[b],
                      font=('Segoe UI', 10, 'bold'),
                      foreground=self.C['muted']).pack(anchor='w',
                                                       pady=(10, 3))
            for t in items:
                var = tk.BooleanVar(value=t in st.chip)
                self._chip_vars[t] = var
                ttk.Checkbutton(inner, text=self._tag_label(t), variable=var,
                                command=self._on_chip).pack(anchor='w',
                                                            padx=(10, 0))
        if not self._chip_vars:
            ttk.Label(inner, text='-',
                      style='Muted.TLabel').pack(anchor='w', pady=8)

        def _closed(_e=None):
            # bind_all 是全局的，关窗必须解绑，否则滚轮事件会打到已销毁的 canvas
            try:
                win.unbind_all('<MouseWheel>')
            except Exception:
                pass
            self._filter_win = None
            win.destroy()
        win.protocol('WM_DELETE_WINDOW', _closed)

    def _refresh_chips(self):
        """重新加载 / 切语言后：更新筛选按钮计数；弹出面板若开着则重建内容。"""
        if not hasattr(self, 'chip_btn'):
            return
        self.chip_btn.config(text=self._chip_btn_text())
        if getattr(self, '_filter_win', None):
            try:
                # ：强毁前必须解绑滚轮——否则切语言/重载时开着面板，
                # bind_all 残留的回调打到已销毁 canvas 报 TclError
                self._filter_win.unbind_all('<MouseWheel>')
                self._filter_win.destroy()
            except Exception:
                pass
            self._filter_win = None
            self._open_filter()

    def _on_chip(self, _evt=None):
        st = self.st
        st.chip = {t for t, v in self._chip_vars.items() if v.get()}
        if hasattr(self, 'chip_btn'):
            self.chip_btn.config(text=self._chip_btn_text())
        self._refresh_enemies()

    def _chip_clear(self):
        st = self.st
        st.chip.clear()
        for v in self._chip_vars.values():
            v.set(False)
        if hasattr(self, 'chip_btn'):
            self.chip_btn.config(text=self._chip_btn_text())
        self._refresh_enemies()

    def _on_enemy_right(self, evt):
        """敌人右键：查看官方分类 desc 原文（本地固化数据，零联网）。"""
        if getattr(self, '_loading', False):
            return
        row = self.enemy_tree.identify_row(evt.y)
        if not row:
            return
        self.enemy_tree.selection_set(row)
        g = self.st.group_of(row)
        if not g:
            return
        desc = (g.get('desc') or '').strip()
        if desc and desc != '-':
            messagebox.showinfo(
                self.st.t('menu_desc'),
                '%s\n\n%s: %s\n\n%s' % (self._enemy_label(g),
                                        self.st.t('col_infokey'), g.get('infokey') or '-',
                                        desc))
        else:
            self.status.config(text=self.st.t('desc_none'))
        return 'break'

    def _sub_label(self, kind, sub):
        return '%s·%s' % (self.st.t('cat_%s' % kind), self.st.t('sub_%s' % sub))

    def _tag_label(self, t):
        """官方标签显示名：随界面语言翻译（一页不混两种语言），缺键回退原文。"""
        if not t:
            return '-'
        tr = self.st.t('tag_%s' % t)
        return tr if tr != 'tag_%s' % t else t

    def _sub_name_label(self, sub):
        """官方物品子类显示名：随界面语言翻译，缺键回退原文。"""
        if not sub:
            return ''
        tr = self.st.t('sub_name_%s' % sub)
        return tr if tr != 'sub_name_%s' % sub else sub

    def _official_label(self, e):
        """敌人「类别」列官方标签展示：优先 characterinfo desc 解析出的官方标签
        （阵营/类型/附加，与筛选器同源），全部随界面语言翻译；
        无官方标签时返回 None 由调用方回退。"""
        tags = e.get('tags') or set()
        if not tags:
            return None
        order = []
        for t in tags:
            if t in _TAG_CAMP:
                order.append((0, t))
            elif t in _TAG_TYPE:
                order.append((1, t))
            else:
                order.append((2, t))
        order.sort()
        return ' '.join(self._tag_label(t) for _, t in order)

    def _name_groups(self):
        """name -> [enemy组] 映射（缓存；敌人数据在加载/切语言时才变）。
        _enemy_label 每次刷新被调用 N 次，原来每次都全量扫 st.enemies，
        千级敌人下就是 O(n²)，点一次勾选卡一下。"""
        m = getattr(self, '_name_groups_cache', None)
        if m is None:
            m = {}
            for e in self.st.enemies:
                m.setdefault(e['name'], []).append(e)
            self._name_groups_cache = m
        return m

    def _enemy_label(self, e):
        """敌人树显示名：同名拆组时带表签名摘要 + 组序号，用户能肉眼分辨是哪一组。
        唯一组直接显示原名（保持旧观感）；多组时附加
        「[组序号/总数 · 表摘要]」，表摘要取自组的表签名（截断防挤爆列）。"""
        label = e['name']
        if len(e['npc_ids']) > 1:
            label = '%s (%d)' % (label, len(e['npc_ids']))
        same = self._name_groups().get(e['name'], [e])
        if len(same) > 1:
            idx = same.index(e) + 1
            sig = e.get('sig') or ''
            sig = (sig[:16] + '…') if len(sig) > 16 else sig
            label = '%s [%d/%d · %s]' % (label, idx, len(same), sig)
        return label

    def _refresh_enemies(self):
        st = self.st
        if getattr(self, '_loading', False):
            return
        kw = self.search_var.get().strip().lower()
        chips = set(st.chip or ())
        # 记住选中：重建后要恢复，否则选中丢失 → 触发 _on_enemy_click → 把右侧清空
        keep = list(self.enemy_tree.selection())
        self.enemy_tree.delete(*self.enemy_tree.get_children())

        # 每行一次的统计先全量预计算（O(checked) 一次），原来每行全扫一遍 checked
        done_of = {}
        for (gid, _k), v in st.checked.items():
            if v:
                done_of[gid] = done_of.get(gid, 0) + 1

        # 按名字排序：同名（含拆组变体）必须排在一起，避免"闪耀箭头二"散落在远处
        ordered = sorted(st.enemies,
                         key=lambda e: (e['name'].lower(), e['gid'].lower()))
        for e in ordered:
            if chips and not chips.issubset(e.get('tags') or ()):
                continue
            hay = (e['name'] + ' ' + e['gid'] + ' ' + ' '.join(e['npc_ids'])).lower()
            if kw and kw not in hay:
                continue
            label = self._enemy_label(e)
            if st.ewl.get(e['gid']):
                label = '🔒 ' + label
            # 「未校准」：挂表是重建时推断的、未经 questlog 校验。
            # 不标的话用户会当成已确认的正常敌人去勾（M8 要求提示）。
            if st.enemy_uncalibrated(e['gid']):
                label = '⚠' + st.t('uncalibrated') + ' ' + label
            done = done_of.get(e['gid'], 0)
            self.enemy_tree.insert('', 'end', iid=e['gid'], values=(
                label, e.get('infokey') or '-',
                self._official_label(e) or self._sub_label(e['kind'], e['sub']),
                '%d/%d' % (done, e['n_drops']) if done else e['n_drops']))
        if keep:
            still = [s for s in keep if self.enemy_tree.exists(s)]
            if still:
                self._suspend_sel = True
                try:
                    self.enemy_tree.selection_set(still)
                finally:
                    self._suspend_sel = False

    def _pin_rank(self, mark):
        """置顶排序权重：越小越靠前。两个开关可同时开（删除优先于白名单）。"""
        r = 0
        if getattr(self, 'pin_del', None) and self.pin_del.get() and mark == 'del':
            r -= 2
        if getattr(self, 'pin_wl', None) and self.pin_wl.get() and mark in ('wl', 'gwl'):
            r -= 1
        return r

    def _on_enemy_click(self, evt=None):
        """点敌人行（或代码调用）→ 刷新右侧掉落列表。
        有点击事件就先选中该行；没有就按当前 selection 来。"""
        if getattr(self, '_suspend_sel', False):
            return
        if evt is not None:
            row = self.enemy_tree.identify_row(evt.y)
            if row:
                self._suspend_sel = True
                try:
                    self.enemy_tree.selection_set(row)
                finally:
                    self._suspend_sel = False
        for row in self.enemy_tree.selection():
            g = self.st.group_of(row)
            if not g:
                continue
            self.drop_tree.delete(*self.drop_tree.get_children())
            is_unknown = (g['sub'] == 'unknown' and g['kind'] == 'hostile')
            # 先按「置顶」权重排序（稳定排序：同组内保持原名序）
            rows = []
            for d in g['drops_list']:
                k = str(d['id'])
                m = self.st.mark_of(g['gid'], k)
                rows.append((self._pin_rank(m), d, k, m))
            rows.sort(key=lambda x: x[0])
            if getattr(self, '_d_sort', None):
                col, rev = self._d_sort
                rows.sort(key=lambda x: self._d_key(g, x[1], x[2], x[3], col),
                          reverse=rev)
            rows.sort(key=lambda x: x[0])          # 置顶权重最后再稳定排（同组保持列序）
            # 官方物品分类（mainCategory·subCategory）分组：item_cats 固化优先，
            # 缺失回退 '-'（不联网）。父行只做分组标题，不参与勾选/操作。
            groups = {}
            for _r, d, k, m in rows:
                ci = (self.st.item_cats or {}).get(k, {})
                main = ci.get('main') or '-'
                sub = ci.get('sub') or ''
                gkey = main if not sub else '%s|%s' % (main, sub)
                groups.setdefault(gkey, []).append((_r, d, k, m))
            for gkey in sorted(groups, key=lambda x: (x == '-', x)):
                main, _, sub = gkey.partition('|')
                grp = groups[gkey]
                # 分组标题随界面语言翻译（一页不混两种语言），缺键回退官方原文
                ml = self._cat_label(main)
                gname = ml if not sub else '%s·%s' % (ml, self._sub_name_label(sub))
                self.drop_tree.insert('', 'end', iid='%s%s%s' % (g['gid'], _CATROW, gkey),
                                      tags=('catg',), values=(
                    '', '[%s] (%d)' % (gname, len(grp)), '', '', '', '', '', ''))
                for _r, d, k, m in grp:
                    tag = TAG.get(m, '')
                    # 自定义掉率优先显示：boost 值（0~1 → 百分比，100% = 必掉）；
                    # 提率物品追加原始掉率（该敌人 d.p，无则省略原值）
                    if k in self.st.boost:
                        bp = self.st.boost[k]
                        rate = '100%' if bp >= 1.0 else '%.0f%%' % (bp * 100)
                        p0 = d.get('p')
                        if isinstance(p0, (int, float)):
                            rate += self.st.t('rate_orig_paren') % ('%.0f%%' % (p0 * 100))
                    else:
                        rate = ('%.0f%%' % (d.get('p') * 100)) if isinstance(d.get('p'), (int, float)) else '-'
                    # 官方实际掉率（questlog 固化，与内部权重并存，不改写盘）
                    pb = d.get('prob')
                    prob = ('%.0f%%' % (pb * 100)) if isinstance(pb, (int, float)) else '-'
                    nm = self._item_display(k, d.get('name') or k)
                    # 非击杀来源（好感度回礼/偷窃/宝箱等）：物品不在该组的映射表里，
                    # 勾了也清不到 —— 置灰 + 表列写明原因，防呆（用户实测踩过）
                    ext = not self.st.drop_in_enemy(g, k)
                    # 「未校准」的敌人：挂表是重建时按名字推断的、未经 questlog 校验。
                    # 表可能是猜的 → 勾了可能改错表 → 一律置灰不可勾（M8 + 用户定调）。
                    if not ext and self.st.enemy_uncalibrated(g['gid']):
                        ext = True
                    # 表列：标注共享表（>=5 个敌人组共用），删它的槽会影响所有共用者
                    tab_parts = []
                    for t in g['tables']:
                        if not (str(k).isdigit() and int(k) in self.st.gd.tabs.get(t, {})):
                            continue
                        owners = self.st.table_group_count(t)
                        tab_parts.append(t[:30] + (self.st.t('shared_mark') % owners
                                                   if owners >= 5 else ''))
                    tab_txt = ' / '.join(tab_parts) or self.st.t('drop_external')
                    if ext:
                        row_tag = 'ext'
                        sym = SYM['ext']
                    else:
                        row_tag = tag
                        sym = SYM[m]
                    self.drop_tree.insert('', 'end', iid='%s|%s' % (g['gid'], k),
                                          tags=(row_tag,) if row_tag else (), values=(
                        sym, nm, gname, rate, prob, d.get('q', '-'),
                        self.st.t('type_fixed') if (d.get('type') or '') == 'fixed' else self.st.t('type_random'),
                        tab_txt))
            self._sync_count()
            # unknown 敌人无掉落表：计数提示会被 _sync_count 覆盖，最后再置为专属提示
            if is_unknown:
                self.status.config(text=self.st.t('no_table_tip'))
            return

    def _refresh_global(self):
        st = self.st
        if getattr(self, '_loading', False):
            return
        if not hasattr(self, 'g_tree'):
            return
        kw = self.gsearch_var.get().strip().lower()
        # 类别过滤：cat_var 选中某类则只显示该类；「全部」= 不过滤
        cat_key = ''
        cv = self.cat_var.get().strip()
        if cv and cv != st.t('gf_all'):
            cat_key = self._cat_key(cv)
        # 子类过滤：sub_var 显示名反查回官方原文再比较（显示名可能被翻译）
        sub_key = ''
        sv = self.sub_var.get().strip()
        if sv and sv != st.t('gf_all'):
            sub_key = self._sub_key(sv)
        key, rev = st.gsort
        agg = self._global_items()
        rows = []
        for k, a in agg.items():
            if cat_key and a['cat'] != cat_key:
                continue
            if sub_key and a.get('sub') != sub_key:
                continue
            if kw and kw not in self._item_display(k, a['name']).lower():
                continue
            rows.append((k, a))

        def kf(item):
            k, a = item
            # L13：dl/wl 列按标记状态排（布尔→0/1）
            if key == 'dl':
                return (k in st.gdel, a['name'])
            if key == 'wl':
                return (k in st.gwl, a['name'])
            v = a.get(key)
            if key == 'price':
                return (v is None, v if v is not None else 0, a['name'])
            if key == 'n':
                return (-(v or 0), a['name'])
            return (str(v or ''), a['name'])

        rows.sort(key=kf, reverse=rev)
        # 置顶：稳定排序，先按用户选的列排，再按置顶权重排（同组内仍按原列序）
        def pinof(item):
            k, _a = item
            r = 0
            if getattr(self, 'pin_del', None) and self.pin_del.get() and k in st.gdel:
                r -= 2
            if getattr(self, 'pin_wl', None) and self.pin_wl.get() and k in st.gwl:
                r -= 1
            return r
        rows.sort(key=pinof)
        self.g_tree.delete(*self.g_tree.get_children())
        for k, a in rows:
            name = self._item_display(k, a['name'])
            # 提率物品加 ★ 前缀并显示数值，一眼可辨（搜索/排序仍用原名）；
            # 追加原始掉率：该物品所有 item_slots 槽位原始 rates 换算百分比的去重集合，
            # 唯一显示「原x%」，多值显示「原x%~y%」（最小~最大），无槽位则省略原值
            if k in st.boost:
                name = '★ %s (%d%%' % (name, round(st.boost[k] * 100))
                op = self._orig_rate_text(k)
                if op:
                    name += self.st.t('rate_orig_slash') % op
                name += ')'
            self.g_tree.insert('', 'end', iid=k, values=(
                SYM['del'] if k in st.gdel else SYM['none'],
                SYM['wl'] if k in st.gwl else SYM['none'],
                name,
                a['price'] if a['price'] is not None else '-',
                self._cat_label(a['cat']),
                a['n']))
        self._sync_count()

    def _global_items(self):
        """物品维度聚合：{item_key: {name, price, cat, n}}
        ⚠️ 必须缓存：每次调用要读 2 个 JSON + 遍历全部敌人，
           不缓存的话点一轮按钮就重算几十次，界面会明显卡。"""
        if getattr(self, '_agg_cache', None) is not None:
            return self._agg_cache
        st = self.st
        agg = {}
        prices = {}
        pf = os.path.join(core.res_dir(), 'data', 'prices.json')
        if os.path.exists(pf):
            try:
                prices = json.load(open(pf, encoding='utf-8'))
            except Exception:
                pass
        cats = {}
        cf = os.path.join(core.res_dir(), 'data', 'itemcats.json')
        if os.path.exists(cf):
            try:
                cats = json.load(open(cf, encoding='utf-8'))
            except Exception:
                pass
        ic = st.item_cats or {}

        def _num(v):
            """价格必须是数字：prices.json 里混进字符串会让 _apply_price 的
            `a['price'] <= p` 抛 TypeError、排序时 str/int 比较也抛——
            都在 Tk 回调里，pythonw 打包版就是「点了没反应」（，）。"""
            return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

        for e in st.enemies:
            for d in e['drops_list']:
                k = str(d['id'])
                ci = ic.get(k, {}) if isinstance(ic.get(k), dict) else {}
                a = agg.setdefault(k, {'name': d.get('name') or k,
                                       'price': _num(prices.get(k)),
                                       'cat': ci.get('main') or cats.get(k, ''),
                                       'sub': ci.get('sub') or '',
                                       'n': 0})
                a['n'] += 1
        # 补充 75 段食材大中小档：itemcats 有分类、dropsetinfo 有槽位、但 questlog
        # 掉落未收录的条目（如 750008 葡萄等）。name 先用 id 字符串占位，实际显示
        # 走 _item_display 名库链路（drop_names / npc_drops / hash_map）。
        # 只补 75 前缀段，禁止全量并入 dropsetinfo（会造成 1000+ 未命名条目刷屏）。
        slots = st.item_slots
        for k in cats:
            ks = str(k)
            if ks.startswith('75') and ks not in agg:
                try:
                    if int(ks) in slots:
                        ci = ic.get(ks, {}) if isinstance(ic.get(ks), dict) else {}
                        agg[ks] = {'name': ks, 'price': _num(prices.get(ks)),
                                   'cat': ci.get('main') or cats.get(k, ''),
                                   'sub': ci.get('sub') or '', 'n': 0}
                except (TypeError, ValueError):
                    continue
        self._agg_cache = agg
        return agg

    def _invalidate_agg(self):
        """敌人数据变了（加载/切语言/重新分组）就要丢掉聚合缓存。"""
        self._agg_cache = None
        self._name_groups_cache = None

    def _sync_count(self):
        c = self.st.counts()
        parts = []
        if c['del']:
            parts.append(self.st.t('cnt_del') % c['del'])
        if c['boost']:
            parts.append(self.st.t('cnt_boost') % c['boost'])
        if c['enemy']:
            parts.append(self.st.t('cnt_enemy') % c['enemy'])
        if c['wl']:
            parts.append(self.st.t('cnt_wl') % c['wl'])
        self.status.config(text='  '.join(parts) or self.st.t('ready'))

    # ════════════════════════════════════════════════════════
    #  事件（bind 表里登记的）
    # ════════════════════════════════════════════════════════

    # ── 搜索防抖（快速输入不每键全量重刷）────────────────────

    def _on_search_enemy(self, *_):
        if getattr(self, '_search_after', None):
            self.root.after_cancel(self._search_after)
        self._search_after = self.root.after(250, self._flush_search_enemy)

    def _flush_search_enemy(self):
        self._search_after = None
        if hasattr(self, 'enemy_tree'):
            self._refresh_enemies()

    def _on_search_global(self, *_):
        if getattr(self, '_gsearch_after', None):
            self.root.after_cancel(self._gsearch_after)
        self._gsearch_after = self.root.after(250, self._flush_search_global)

    def _flush_search_global(self):
        self._gsearch_after = None
        if hasattr(self, 'g_tree'):
            self._refresh_global()

    def _on_lang(self, _evt=None):
        # 加载期禁止切语言：_rebuild_ui 会在主线程跑解析 1.4MB 语言包 +
        # 全量重排三个列表，与后台加载线程抢同一批字典（st.qlog/enemies）
        if getattr(self, '_loading', False):
            return
        st = self.st
        name = self.lang_box.get()
        code = next((c for c in st.langs if st.langs[c]['name'] == name), None)
        if not code or code == st.lang:
            return
        st.lang = code
        st.cfg['lang'] = code
        st.save_cfg(self._set_status)
        if st.gd:
            # 十 挂账：load_qlog 无 try——语言包损坏时切语言炸回调
            try:
                st.qlog = st.load_qlog()
                st.enemies = st._group_by_name()
            except Exception as ex:
                self.status.config(text='语言包加载失败: %s' % ex)
            self._invalidate_agg()      # 敌人数据变了，聚合缓存必须丢（否则全局列表还是旧语言名）
        # U-1 评估：报告称「切语言后 type_names/others 陈旧」——本代码库无此
        # 两属性（分类名走 item_cats+语言键，与界面语言联动由 _rebuild_ui 完成），
        # 按不存在驳回，仅记录。
        self._rebuild_ui()

    def _on_include_nondrop(self, _evt=None):
        """放行越界槽（非击杀来源：购买奖励/好感度回礼/交易…）。
        这是破坏开关，勾上要给明确反馈，不能静默。"""
        st = self.st
        st.include_nondrop = bool(self.include_nondrop.get())
        self._refresh_global()
        self.status.config(text=st.t('nondrop_include_on') if st.include_nondrop
                           else st.t('nondrop_include_off'))

    def _on_cat_filter(self, _evt=None):
        """全局页主类下拉：按官方 mainCategory 过滤；同时把子类候选收窄到该主类。"""
        self._refresh_sub_box()
        self._refresh_global()

    def _refresh_sub_box(self):
        """按当前主类重建子类下拉候选（主类=全部时给全部子类）。"""
        if not hasattr(self, 'sub_box'):
            return
        st = self.st
        cat = self._cat_key(self.cat_var.get().strip())
        ic = getattr(st, 'item_cats', None) or {}
        if ic:
            subs = sorted({v.get('sub') for v in ic.values()
                           if isinstance(v, dict) and v.get('sub')
                           and (not cat or v.get('main') == cat)})
        else:
            subs = self._sub_cats()
        self.sub_var.set(st.t('gf_all'))
        self.sub_box.configure(values=[st.t('gf_all')] + [self._sub_name_label(s)
                                                          for s in subs])

    def _on_sub_filter(self, _evt=None):
        """全局页子类下拉：按官方 subCategory 过滤；选「全部」恢复不过滤。"""
        self._refresh_global()

    def _on_drop_click(self, evt):
        """点第一列（勾选列）→ 循环三态；点其他列 → 只选中该行。
        提率物品不可勾选删除（语义冲突），点击给状态提示。"""
        if getattr(self, '_loading', False):
            return
        row = self.drop_tree.identify_row(evt.y)
        if not row:
            return
        if self._is_cat_row(row):
            # 分类父行只做分组标题，不参与勾选（原来点它会造出垃圾 checked 键）
            self.drop_tree.selection_set(row)
            return
        if self.drop_tree.identify_column(evt.x) != '#1':
            self.drop_tree.selection_set(row)
            return
        gid, k = row.rsplit('|', 1)
        if k in self.st.gwl:
            return
        if k in self.st.boost:
            self.status.config(text=self.st.t('boost_no_toggle') % k)
            return
        ge = self.st.group_of(gid)
        if ge is not None and not self.st.drop_in_enemy(ge, k):
            self.status.config(text=self.st.t('drop_external_warn'))
            return
        r = self.st.cycle_drop(gid, k)
        if r == 'uncal':
            self.status.config(text=self.st.t('drop_uncal_warn'))
            return
        self._save_sel()
        m = self.st.mark_of(gid, k)
        self.drop_tree.set(row, 'c', SYM[m])
        self.drop_tree.item(row, tags=(TAG[m],) if m in TAG else ())
        self._refresh_enemies()

    def _on_drop_right(self, evt):
        if getattr(self, '_loading', False):
            return
        row = self.drop_tree.identify_row(evt.y)
        if not row or self._is_cat_row(row):
            return
        gid, k = row.rsplit('|', 1)
        self.drop_tree.selection_set(row)
        g = self.st.group_of(gid)
        disp = self._enemy_label(g) if g else gid
        inwl = k in set(self.st.ewl.get(gid, []))
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label=(self.st.t('menu_unwl') if inwl else self.st.t('menu_wl')) % disp,
                      command=lambda: self._toggle_enemy_wl(gid, k))
        m.add_command(label=self.st.t('menu_must'), command=lambda: self._set_must(k))
        m.add_command(label=self.st.t('menu_prob'), command=lambda: self._ask_prob(k))
        m.add_command(label=self.st.t('menu_cancel'), command=lambda: self._clear_one(gid, k))
        m.add_command(label=self.st.t('menu_restore'),
                      command=lambda: self._restore_one(gid, k))
        m.tk_popup(evt.x_root, evt.y_root)
        return 'break'

    def _on_global_click(self, evt):
        # ：加载期间必须拒绝操作。全局页的 g_tree 不像
        # enemy_tree/drop_tree 那样在 _load_game 里被清空，所以加载中右侧
        # 仍显示旧物品；此时点击会去改 st.gdel / st.checked，而后台线程
        # 正在遍历同一批字典（_migrate_legacy_keys / _group_by_name）
        # → RuntimeError: dictionary changed size during iteration。
        if getattr(self, '_loading', False):
            return
        row = self.g_tree.identify_row(evt.y)
        if not row:
            return
        col = self.g_tree.identify_column(evt.x)
        if col == '#1':
            # 与空格路径同一保护口径：敌人白名单物品拒绝加入全局删除
            if row not in self.st.gdel and row in self._ewl_items():
                self.status.config(text=self.st.t('ewl_block_one') % self._item_name(row))
                return
            self.st.toggle_global(row, 'del')
        elif col == '#2':
            self.st.toggle_global(row, 'wl')
        else:
            self.g_tree.selection_set(row)
            return
        self._save_sel()
        self._refresh_global()

    def _on_global_right(self, evt):
        """全局页右键：弹上下文菜单（不再直接切换保护，避免与左键语义混淆、防误触）。"""
        if getattr(self, '_loading', False):
            return 'break'          # 同 _on_global_click：加载中拒绝操作
        row = self.g_tree.identify_row(evt.y)
        if not row:
            return 'break'
        self.g_tree.selection_set(row)
        st = self.st
        m = tk.Menu(self.root, tearoff=0)
        if row in st.gdel:
            m.add_command(label=st.t('gmenu_undel'),
                          command=lambda: self._g_set(row, 'undel'))
        else:
            m.add_command(label=st.t('gmenu_del'),
                          command=lambda: self._g_set(row, 'del'))
        if row in st.gwl:
            m.add_command(label=st.t('gmenu_unwl'),
                          command=lambda: self._g_set(row, 'unwl'))
        else:
            m.add_command(label=st.t('gmenu_wl'),
                          command=lambda: self._g_set(row, 'wl'))
        m.add_command(label=st.t('menu_cancel'),
                      command=lambda: self._g_clear(row))
        m.add_command(label=st.t('menu_restore'),
                      command=lambda: self._restore_one(None, row))
        m.tk_popup(evt.x_root, evt.y_root)
        return 'break'

    def _g_set(self, row, which):
        """全局页右键菜单：设删除/保护（互斥，并清除提率）或仅移出对应标记。"""
        st = self.st
        if which == 'del':
            if row in self._ewl_items():
                self.status.config(text=st.t('ewl_block_one') % self._item_name(row))
                return
            st.gdel.add(row)
            st.boost.pop(row, None)
            st.gwl.discard(row)
        elif which == 'wl':
            st.gwl.add(row)
            st.gdel.discard(row)
            st.boost.pop(row, None)
        elif which == 'undel':
            st.gdel.discard(row)
        elif which == 'unwl':
            st.gwl.discard(row)
        self._save_sel()
        self._refresh_global()

    def _g_clear(self, row):
        """全局页右键菜单：取消（不删不保，清除提率）。"""
        st = self.st
        st.gdel.discard(row)
        st.gwl.discard(row)
        st.boost.pop(row, None)
        self._save_sel()
        self._refresh_global()

    def _on_digit_key(self, evt):
        """数字键 1-9 套用预设；输入框聚焦时不触发。"""
        if getattr(self, '_loading', False):
            return                  # 自审 P1-4：加载期间禁改状态（后台线程在写 st）
        try:
            w = self.root.focus_get()
            if w is not None and w.winfo_class() in ('TEntry', 'TCombobox', 'TSpinbox',
                                                     'Button', 'TButton', 'Checkbutton',
                                                     'TCheckbutton', 'TRadiobutton'):
                return
            n = int(evt.char)
        except Exception:
            return
        if 1 <= n <= 9:
            if self.st.cur_tab == 1:
                names = self.st.global_preset_names()
                if n - 1 < len(names):
                    self._apply_global_preset(name=names[n - 1])
            else:
                names = self.st.enemy_preset_names()
                if n - 1 < len(names):
                    self._apply_enemy_preset(name=names[n - 1])
            return 'break'

    def _on_tab(self, _evt=None):
        try:
            self.st.cur_tab = self.nb.index(self.nb.select())
        except Exception:
            self.st.cur_tab = 0

    # ════════════════════════════════════════════════════════
    #  按钮动作
    # ════════════════════════════════════════════════════════

    def _pick_dir(self):
        d = filedialog.askdirectory(parent=self.root)
        if d:
            self.dir_var.set(d)

    def _load_game(self):
        """异步加载：解析 29MB 数据要 ~20 秒，绝不能在主线程里跑，
        否则窗口完全不重绘 —— 用户看到界面"卡住"，随便点一下屏幕才"突"地刷出来。

        ⚠️ tkinter 的 after() **不是线程安全的**（后台线程调用会
        RuntimeError: main thread is not in main loop），所以用队列 + 主线程轮询。"""
        #  P2-1：重入守卫（快捷键/程序化调用/切语言复活按钮都可达）
        if getattr(self, '_loading', False):
            return
        gd = self.dir_var.get().strip()
        if not gd or not os.path.isdir(gd):
            messagebox.showerror(self.st.t('err_title'), self.st.t('need_game'))
            return
        # ：把本次加载的目录钉住——加载期间用户可能改输入框，
        # 回调再读输入框会造成「运行态 A / 配置记 B」的分叉（实测复现）
        self._loading_dir = gd
        self.status.config(text=self.st.t('loading'))
        # L11：加载期间后台线程在写 st.qlog/enemies，界面必须静默，
        # 否则会读到「新 gd + 旧 enemies」的混搭组合
        self._loading = True
        self.load_btn.config(state='disabled')
        self.apply_btn.config(state='disabled')
        self.lang_box.config(state='disabled')
        if hasattr(self, 'g_apply_btn'):
            self.g_apply_btn.config(state='disabled')
        # 可见反馈：加载这 1~2 秒里界面别空着，否则用户以为坏了（还会去点鼠标）
        if hasattr(self, 'enemy_tree'):
            self.enemy_tree.delete(*self.enemy_tree.get_children())
            self.enemy_tree.insert('', 'end', iid='__loading__',
                                   values=(self.st.t('loading'), '', '', ''))
            if hasattr(self, 'drop_tree'):
                self.drop_tree.delete(*self.drop_tree.get_children())
        self._load_q = queue.Queue()
        threading.Thread(target=self._load_game_bg, args=(gd,), daemon=True).start()
        self._poll_after = self.root.after(100, self._poll_load)

    def _load_game_bg(self, gd):
        try:
            n = self.st.load_game(gd)
            err = None
        except Exception as ex:
            n, err = 0, ex
        self._load_q.put((n, err))          # 只碰队列，不碰 Tk

    def _poll_load(self):
        """队列轮询。每条消息处理后必须重排下一次轮询——
        否则 say() 连发多条时中间消息处理完就 return，后续终止信号
        （__rebuild_ok__）永远无人消费，按钮永久 disabled。
          修复。"""
        self._poll_after = None
        try:
            while True:
                n, err = self._load_q.get_nowait()
                done = self._poll_handle(n, err)
                if done:
                    return
        except queue.Empty:
            pass
        self._poll_after = self.root.after(100, self._poll_load)

    def _poll_handle(self, n, err):
        """处理一条队列消息。返回 True 表示终止型（不再轮询）。"""
        if n == '__retry__':
            self._loading = False
            self._set_actions_enabled(True)
            if err:
                err()
            return True
        if n == '__retry_err__':
            self._loading = False
            self._set_actions_enabled(True)
            self.status.config(text=self.st.t('ready'))
            messagebox.showerror(self.st.t('err_title'), '%s' % err)
            return True
        if n == '__rebuild_say__':
            self.status.config(text=err)
            return False                # 非终止型，继续轮询
        if n == '__rebuild_ok__':
            self._rebuild_done(err, None)
            return True
        if n == '__rebuild_err__':
            self._rebuild_done(None, err)
            return True
        self._load_game_done(getattr(self, '_loading_dir',
                                     self.dir_var.get().strip()), n, err)
        return True

    def _load_game_done(self, gd, n, err):
        # 自审 P2-8：函数体任何一步抛异常都不能让按钮永久 disabled
        try:
            self._load_game_done_body(gd, n, err)
        except Exception as ex:
            core.log_exc('_load_game_done', ex)
        finally:
            self._loading = False
            for w in (self.load_btn, self.apply_btn, self.lang_box,
                      getattr(self, 'g_apply_btn', None)):
                if w is not None:
                    try:
                        w.config(state='normal' if w is not self.lang_box
                                 else 'readonly')
                    except Exception:
                        pass

    def _rebuild_map(self):
        """游戏更新后一键重建「NPC → 掉率表」映射。

        后台线程跑（要解析 characterinfo，几十秒），进度打到状态栏；
        跑完弹结果，并提示重新加载。旧 pkl 会先备份，缩水过多则拒绝写盘。
        """
        # 两道守卫：① 加载期不跑（_load_q/_poll_load 与加载共用，重复排轮询
        #   会打乱消息消费）；② 重入守卫——按钮 disabled 挡不住键盘/程序化
        #   调用，连起两条线程就是两个进程同时写 npc2tables.pkl。
        if getattr(self, '_loading', False) or getattr(self, '_rebuilding', False):
            return
        st = self.st
        gd = st.cfg.get('game_dir') or ''
        if not gd or not os.path.isdir(gd):
            messagebox.showerror(st.t('err_title'), st.t('need_game'))
            return
        if not messagebox.askyesno(st.t('info_title'),
                                   st.t('rebuild_confirm')):
            return
        # _load_q 只在 _load_game 里创建：从未加载过就直接点「重建映射」，
        # 后台线程会在 say() 里 AttributeError，结果永远回不来
        # → 按钮永久灰（现在还会连带把 _rebuilding 永久卡住）
        if getattr(self, '_load_q', None) is None:
            self._load_q = queue.Queue()
        self._rebuilding = True
        self.rebuild_btn.config(state='disabled')
        self.status.config(text=st.t('rebuild_running'))

        def say(m):
            # 后台线程不能直接碰 Tk → 排到队列，由 _poll_load 消化
            self._load_q.put(('__rebuild_say__', str(m)[:90]))

        def work():
            try:
                import build_npc2tables_local as rb
                r = rb.rebuild_npc2tables(gd, dry=False, say=say)
                self._load_q.put(('__rebuild_ok__', r))
            except Exception as ex:
                self._load_q.put(('__rebuild_err__',
                                  '%s: %s' % (type(ex).__name__, ex)))

        threading.Thread(target=work, daemon=True).start()
        self._poll_after = self.root.after(100, self._poll_load)

    def _rebuild_done(self, r, err):
        st = self.st
        # 与 _rebuild_map 的 _rebuilding 置位配对；成功/失败都必须复位，
        # 否则一次失败后「重建映射」永久不可点
        self._rebuilding = False
        self.rebuild_btn.config(state='normal')
        if err:
            self.status.config(text=st.t('rebuild_fail'))
            messagebox.showerror(st.t('err_title'),
                                 st.t('rebuild_fail') + '\n\n' + err)
            core.log('映射重建失败: %s' % err)
            return
        self.status.config(text=st.t('rebuild_done'))
        core.log('映射重建完成: 表 %d / 沿用 %d / 新敌人 %d / 映射 %d'
                 % (r['n_tables'], r['n_kept'], r['n_new'], r['n_map']))
        messagebox.showinfo(
            st.t('info_title'),
            st.t('rebuild_result') % (r['n_tables'], r['n_kept'],
                                      r['n_new'], r['n_map'])
            + ('\n\n' + st.t('rebuild_backup') % r['backup']
               if r.get('backup') else '')
            + '\n\n' + st.t('rebuild_reload'))

    def _undo_last(self):
        # 加载期禁止：撤销会覆写 overlay，且 st.gd 正被后台线程重建
        if getattr(self, '_loading', False):
            return
        st = self.st
        if not messagebox.askyesno(st.t('info_title'), st.t('undo_confirm')):
            return
        ok, info = st.undo_last_apply()
        if not ok:
            messagebox.showinfo(st.t('info_title'), st.t(info or 'undo_none'))
            return
        messagebox.showinfo(st.t('info_title'), st.t('undo_done'))
        self._invalidate_agg()
        # P1：以前只丢聚合缓存就完了，列表与计数停留在撤销前——界面看着
        # 和应用后一样，实际磁盘已经回滚。与 apply/restore 成功后的口径一致
        # 补一次全量重绘。
        # ⚠️ 诚实说明：undo_last_apply 只回滚磁盘 overlay，内存里的
        # st.checked/gdel/boost 并未回退，所以刷新后界面仍是「撤销前」的勾选
        # 状态。要真正一致需要核心提供 overlay→选择的反向载入（核心层范围，
        #  UI 层不伪造），这里只保证界面与内存状态同步、不留陈旧渲染。
        self._refresh_global()
        self._refresh_enemies()
        if self.enemy_tree.selection():
            self._on_enemy_click()
        self._sync_count()          # 收尾把计数打回状态栏（与 _apply 末尾同口径）

    def _self_check(self):
        """生成自检报告并保存到 exe 同目录，同时弹窗告知路径。
        用户复制粘贴这份文本给我，省掉来回问答。"""
        # 加载期禁止：自检会遍历 st.enemies/checked，与后台线程撞车
        if getattr(self, '_loading', False):
            return
        st = self.st
        try:
            txt = st.self_check()
        except Exception as ex:
            messagebox.showerror(st.t('err_title'), '%s' % ex)
            return
        path = os.path.join(core.user_dir(), 'nojunk2_selfcheck.txt')
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(txt)
        except Exception as ex:
            messagebox.showerror(st.t('err_title'), '%s' % ex)
            return
        messagebox.showinfo(st.t('info_title'),
                            st.t('selfcheck_saved') % path)
        self.status.config(text=st.t('selfcheck'))

    def _warn_if_stale_overlay(self):
        """磁盘上的 overlay 过期 → 当场说清楚，别让用户以为是自己不会用。

        事故原型：2026-10-30 游戏更新到 1.0.0.3424，用户 mods/NoJunk/ 里
        还躺着 10-28 生成的旧补丁（2,674,515 字节，新版是 2,823,997）。
        工具照常加载、照常让他点应用，进游戏毫无变化 —— 用户第一反应
        "这破工具坏了"。其实只是那个文件早就作废了。
        """
        st = self.st
        info = getattr(st, 'stale_overlay', None)
        if not info:
            return
        why = st.t('ov_stale_size' if info.get('reason') == 'size'
                   else 'ov_stale_content')
        txt = why % (info.get('size', 0), info.get('want', 0))
        if info.get('backup_n'):
            txt += '\n\n' + (st.t('ov_stale_bak') % info['backup_n'])
        txt += '\n\n' + st.t('ov_stale_fix')
        messagebox.showwarning(st.t('info_title'), txt)

    def _warn_if_stale(self):
        """存了的选择在当前数据里匹配不上 → 明确告诉用户，不留静默。

        事故原型（ 用户实测）：1.0.1 的 nojunk2.json /
        _game_cache.pkl 被 1.0.2 读到后，工具照常显示勾选、点应用也报成功，
        但一个字节都没写 —— 用户只能发现「MOD 没用」，无从定位。
        """
        st = self.st
        try:
            r = st.stale_selection()
        except Exception:
            return
        if not r['groups']:
            return
        total_lost = r['lost'] + r['items']
        if not total_lost:
            return
        old_cfg = (r['schema'] != core.CFG_SCHEMA)
        parts = []
        if r['lost']:
            parts.append(st.t('stale_groups') % (r['lost'], r['groups']))
        if r['items']:
            parts.append(st.t('stale_items') % r['items'])
        why = st.t('stale_why_old') if old_cfg else st.t('stale_why_data')
        messagebox.showwarning(
            st.t('info_title'),
            st.t('stale_head') + '\n\n' + '\n'.join(parts) + '\n\n' + why
            + '\n\n' + st.t('stale_fix'))
        log_line = ('存的选择匹配不上: 组 %d/%d 丢失, 物品 %d 个, 配置结构版本 %s'
                    % (r['lost'], r['groups'], r['items'], r['schema']))
        core.log(log_line)

    def _load_game_done_body(self, gd, n, err):
        self._loading = False
        self.load_btn.config(state='normal')
        self.apply_btn.config(state='normal')
        self.lang_box.config(state='readonly')
        if hasattr(self, 'g_apply_btn'):
            self.g_apply_btn.config(state='normal')
        if err:
            self.status.config(text=self.st.t('ready'))
            # 自审 P1-3：错误分支必须清掉 __loading__ 占位行（否则永久残留可点）
            if hasattr(self, 'enemy_tree'):
                try:
                    self.enemy_tree.delete('__loading__')
                except Exception:
                    pass
            messagebox.showerror(self.st.t('err_title'), '%s' % err)
            return
        self.st.cfg['game_dir'] = gd
        self.st.save_cfg(self._set_status)
        # 游戏更新提示：指纹变了就提醒重建映射（否则敌人整片"暂无对应表"）
        if self.st.game_changed_since_last():
            self.st.save_cfg(self._set_status)
            messagebox.showwarning(self.st.t('info_title'),
                                   self.st.t('game_updated_hint'))
        # 跨版本防护：加载完立刻查「存了的选择是否一个都匹配不上」。
        # 匹配不上 = 版本换了/游戏更新了/数据陈旧，此时工具会静默什么都不做，
        # 用户只会看到「MOD 没用」。必须当场说清楚。
        self._warn_if_stale()
        # 再查一层：磁盘上已经躺着的 overlay 是不是过期了。
        # 上一版游戏留下的补丁在新版上长度/偏移全作废，但工具不查、
        # 用户也看不出来 —— 点应用像是成功，进游戏毫无变化。
        self._warn_if_stale_overlay()
        self._invalidate_agg()
        # 分类候选来自 item_cats（load_game 才填充）：加载后必须重建主/子类下拉，
        # 否则下拉停留在启动时的空/兜底候选
        self.cat_box.configure(
            values=[self.st.t('gf_all')] + [self._cat_label(c) for c in self._main_cats()])
        self._refresh_sub_box()
        self._refresh_enemies()
        self._refresh_chips()
        self._refresh_global()
        # 自动选中第一个敌人 —— 打开工具右侧不该是空白
        kids = self.enemy_tree.get_children()
        if kids:
            self._suspend_sel = True
            try:
                self.enemy_tree.selection_set(kids[0])
            finally:
                self._suspend_sel = False
            self._on_enemy_click()
        self.status.config(text=self.st.t('loaded') % n)
        # 强制重绘：update() 有重入风险（会处理嵌套事件、可能触发其他回调），
        # update_idletasks 只刷绘制队列，够用且安全
        self.root.update_idletasks()
        if hasattr(self, 'search_entry'):
            self.search_entry.focus_set()

    def _sel_all(self, v):
        # 加载期禁止：会整片改 st.checked，后台线程正在遍历同一批字典
        if getattr(self, '_loading', False):
            return
        g = self._cur_group()
        if not g:
            return
        if v and not messagebox.askyesno(
                self.st.t('info_title'),
                self.st.t('sel_all_confirm') % (self._enemy_label(g), g['n_drops'])):
            return
        if v and self.st.enemy_uncalibrated(g['gid']):
            self.status.config(text=self.st.t('drop_uncal_warn'))
            return
        for d in g['drops_list']:
            k = str(d['id'])
            if v and k in self.st.gwl:
                continue
            if v and k in self.st.boost:
                continue          # 提率物品不参与删除勾选（与 cycle_drop 互斥一致）
            if v and not self.st.drop_in_enemy(g, k):
                continue          # 非击杀来源（回礼/偷窃等）：勾了也清不到，防呆跳过
            self.st.checked[(g['gid'], k)] = v
        self._save_sel()
        self._refresh_enemies()
        self._on_enemy_click()
        self._sync_count()

    def _sel_consumables(self):
        if getattr(self, '_loading', False):
            return
        g = self._cur_group()
        if not g:
            return
        for d in g['drops_list']:
            k = str(d['id'])
            # F9/U-5：与 _sel_all 口径一致——跳过全局白名单/提率/非击杀来源
            if k in self.st.gwl or k in self.st.boost:
                continue
            if self._cat_of(k) in ('consumables', 'materials'):
                if self.st.drop_in_enemy(g, k):
                    self.st.checked[(g['gid'], k)] = True
        self._save_sel()
        self._refresh_enemies()
        self._on_enemy_click()

    def _sel_enemy_wl(self):
        """把当前勾选的设为「仅此敌人白名单」。"""
        if getattr(self, '_loading', False):
            return
        g = self._cur_group()
        if not g:
            return
        n = 0
        for (gid, k), v in list(self.st.checked.items()):
            if v and gid == g['gid']:
                self.st.checked[(gid, k)] = False
                self.st.ewl[gid] = sorted(set(self.st.ewl.get(gid, [])) | {k})
                self.st.gdel.discard(k)
                self.st.boost.pop(k, None)
                n += 1
        if not n:
            messagebox.showinfo(self.st.t('info_title'), self.st.t('no_sel'))
            return
        self._save_sel()
        self._refresh_enemies()
        self._on_enemy_click()
        self.status.config(text=self.st.t('ewl_done') % (self._enemy_label(g), n))

    def _sel_must_drop(self):
        if getattr(self, '_loading', False):
            return
        g = self._cur_group()
        if not g:
            return
        n = 0
        for (gid, k), v in list(self.st.checked.items()):
            if v and gid == g['gid']:
                self.st.checked[(gid, k)] = False
                self.st.boost[k] = 1.0
                self.st.gdel.discard(k)
                self.st.gwl.discard(k)
                n += 1
        if not n:
            messagebox.showinfo(self.st.t('info_title'), self.st.t('no_sel'))
            return
        self._save_sel()
        self._refresh_global()
        self._refresh_enemies()
        self._on_enemy_click()
        self.status.config(text=self.st.t('must_done') % (self._enemy_label(g), n))

    def _toggle_enemy_wl(self, gid, k):
        wl = set(self.st.ewl.get(gid, []))
        if k in wl:
            wl.discard(k)
        else:
            wl.add(k)
            self.st.checked[(gid, k)] = False
        if wl:
            self.st.ewl[gid] = sorted(wl)
        else:
            self.st.ewl.pop(gid, None)
        self._save_sel()
        self._refresh_enemies()
        self._on_enemy_click()

    def _set_must(self, k):
        self.st.set_prob(k, 1.0)
        self._save_sel()
        self._refresh_global()
        self._refresh_enemies()
        self._on_enemy_click()
        self.status.config(text=self.st.t('must_one') % self._item_name(k))

    def _ask_prob(self, k):
        cur = self.st.boost.get(k)
        v = simpledialog.askstring(self.st.t('prob_title'),
                                   self.st.t('prob_prompt') % self._item_name(k),
                                   initialvalue=('' if cur is None else '%.0f' % (cur * 100)),
                                   parent=self.root)
        if v is None or v.strip() == '':
            self.st.boost.pop(k, None)
            self.st.gdel.discard(k)
        else:
            try:
                p = float(v.strip().rstrip('%')) / 100.0
            except Exception:
                messagebox.showerror(self.st.t('err_title'), self.st.t('prob_bad'))
                return
            import math as _math
            if not _math.isfinite(p):     # 十：'nan'/'inf' 字符串可注入
                messagebox.showerror(self.st.t('err_title'), self.st.t('prob_bad'))
                return
            #  ：>100% 直接报错——旧版显示 200%
            # 写盘时才截断，重启后归一 100%，显示与实际不一致
            if p > 1.0:
                messagebox.showerror(self.st.t('err_title'), self.st.t('prob_bad'))
                return
            # 敌人白名单优先：0 = 删除，受保护物品拒绝加入全局删除（P4）
            if p <= 0 and k in self._ewl_items():
                self.status.config(text=self.st.t('ewl_block_one') % self._item_name(k))
                return
            self.st.set_prob(k, p)
        self._save_sel()
        self._refresh_global()
        self._refresh_enemies()
        self._on_enemy_click()
        p = self.st.boost.get(k)
        if p is not None:
            self.status.config(text=self.st.t('prob_done') % (self._item_name(k), round(p * 100)))

    def _clear_one(self, gid, k):
        self.st.checked[(gid, k)] = False
        self.st.gdel.discard(k)
        self.st.boost.pop(k, None)
        self._save_sel()
        self._refresh_global()
        self._refresh_enemies()
        self._on_enemy_click()

    def _orig_rate_text(self, k):
        """全局页物品原始掉率文本：item_slots 各槽位 rates 换算百分比的去重集合。
        唯一返回「x%」，多值返回「x%~y%」（最小~最大），无槽位返回空串。"""
        try:
            slots = self.st.item_slots.get(int(k), [])
        except (TypeError, ValueError):
            return ''
        pcts = sorted({round(r * 100.0 / plan.W) for _t, _o, r in slots if r > 0})
        if not pcts:
            return ''
        if len(pcts) == 1:
            return '%d%%' % pcts[0]
        return '%d%%~%d%%' % (pcts[0], pcts[-1])

    def _orig_text_for(self, k, gid):
        """确认框里的原始掉率文本：敌人页优先取当前敌人掉落项 p，否则回退全局槽位换算。"""
        if gid:
            g = self.st.group_of(gid) if hasattr(self.st, 'group_of') else None
            if g:
                for d in g['drops_list']:
                    if str(d['id']) == str(k) and isinstance(d.get('p'), (int, float)):
                        return '%.0f%%' % (d['p'] * 100)
        ot = self._orig_rate_text(k)
        return ot or '-'

    def _handle_write_error(self, r, retry):
        """写盘错误统一处理：err_game_changed 不再是硬阻断——弹风险提示，
        用户确认后以当前游戏版本为准继续（所有修改有备份，可一键恢复原版）。
        返回 True 表示用户确认并已重试成功/失败已定，调用方直接 return。"""
        if not (isinstance(r, tuple) and r[0] == 'err_game_changed'):
            return False
        st = self.st
        actual = r[1]
        if not messagebox.askyesno(
                st.t('info_title'),
                st.t('game_changed_warn') % actual):
            return True                      # 用户取消：已处理，不重试
        # ① override 改单次进程内放行（不永久落盘——旧版写进配置后永久失效）
        # ② 确认后必须强制重解析：缓存作废、load_game 重跑，再重试。
        #    旧版直接用旧 gd.ds 的旧偏移 retry = 拿旧地图写新文件。
        # ：原实现漏了加载态——后台线程马上要重写
        # st.qlog / st.enemies / st.checked / st.item_slots，而界面照旧可点。
        # 用户在重解析这 1~20 秒里点一下勾选，就是「主线程改字典 / 后台线程
        # 遍历同一个字典」→ RuntimeError: dictionary changed size during
        # iteration，后台线程静默死掉、队列永远不投递，界面卡在「加载中」。
        # 与首次加载同一口径：置 _loading + 禁用按钮，由 _poll_load 统一复位。
        st._paz_override_once = actual
        import threading
        self.status.config(text=st.t('loading'))
        self._loading = True
        self._set_actions_enabled(False)
        threading.Thread(target=lambda: self._reload_and_retry(retry),
                         daemon=True).start()
        # ：必须重新启动轮询，否则这条 __retry__ 永远没人消费。
        # _poll_load 只在 queue.Empty 时重排自己（第 1360 行），取到一条就停止
        # ——首次加载早已结束，轮询早已停了。
        # 修复前：用户点了「仍要继续」后什么都没发生（不重解析、不重试、不报错，
        #   静默失败，用户以为改上了）；加上 _loading 之后会变成界面永久卡在
        #   「加载中」+ 按钮永久灰。两者都不能接受，故此处补一次 after。
        self._poll_after = self.root.after(100, self._poll_load)
        return True

    def _set_actions_enabled(self, on):
        """加载态开/关：置位与复位走同一处，避免漏掉某个按钮后永久 disabled。"""
        for w, normal in ((self.load_btn, 'normal'),
                          (self.apply_btn, 'normal'),
                          (self.lang_box, 'readonly'),
                          (getattr(self, 'g_apply_btn', None), 'normal')):
            if w is None:
                continue
            try:
                w.config(state=normal if on else 'disabled')
            except Exception:
                pass

    def _reload_and_retry(self, retry):
        """版本确认后的强制重解析+重试（后台线程，只碰队列不碰 Tk）。

        自审 P1-1/P1-2：旧版在后台线程直接 root.after（违反本文件
        1304 行的线程铁律），且重解析失败照样 retry（拿旧 gd 写新文件）。
        改为队列投递，由主线程 _poll_load 消费；失败投递错误信号不 retry。"""
        st = self.st
        try:
            cf = os.path.join(core.user_dir(), '_game_cache.pkl')
            if os.path.exists(cf):
                os.remove(cf)
            st.load_game(st.game_dir)
            self._load_q.put(('__retry__', retry))
        except Exception as ex:
            core.log_exc('版本确认后重解析', ex)
            self._load_q.put(('__retry_err__', str(ex)))

    def _restore_one(self, nm, k):
        """右键菜单「恢复原样」：把单个物品的全部自定义清除并写盘（其他物品保留）。"""
        st = self.st
        has_custom = (k in st.boost or k in st.gdel
                      or any(v for (enm, kk), v in st.checked.items()
                             if kk == k and v))
        if not has_custom:
            messagebox.showinfo(st.t('info_title'), st.t('restore_item_none'))
            return
        cur = ('100%' if st.boost.get(k, 0) >= 1.0
               else '%.0f%%' % (st.boost[k] * 100)) if k in st.boost else '-'
        orig = self._orig_text_for(k, nm)
        if not messagebox.askyesno(st.t('info_title'),
                                   st.t('restore_item_confirm') % (cur, orig)):
            return
        ok, r = st.restore_item(k)
        if not ok:
            if self._handle_write_error(r, lambda: self._restore_one(nm, k)):
                return
            if isinstance(r, tuple):
                key, arg = r
                messagebox.showerror(st.t('err_title'), st.t(key) % arg)
            else:
                messagebox.showerror(st.t('err_title'), st.t(r))
            return
        self._save_sel()
        self._refresh_global()
        self._refresh_enemies()
        if self.enemy_tree.selection():
            self._on_enemy_click()
        self._sync_count()
        self.status.config(text=st.t('restore_item_done') % (r or '-'))

    def _toggle_current_drop(self):
        if getattr(self, '_loading', False):
            return 'break'              # ：键盘路径也要守卫
        for row in self.drop_tree.selection():
            if self._is_cat_row(row):
                continue
            gid, k = row.rsplit('|', 1)
            if k in self.st.gwl:
                continue
            if k in self.st.boost:
                self.status.config(text=self.st.t('boost_no_toggle') % k)
                continue
            r = self.st.cycle_drop(gid, k)
            if r == 'uncal':
                self.status.config(text=self.st.t('drop_uncal_warn'))
                continue
            m = self.st.mark_of(gid, k)
            ge = self.st.group_of(gid)
            if ge is not None and not self.st.drop_in_enemy(ge, k):
                # 非击杀来源（回礼/偷窃等）：置灰提示，不允许勾选
                self.drop_tree.set(row, 'c', SYM['ext'])
                self.status.config(text=self.st.t('drop_external_warn'))
                continue
            self.drop_tree.set(row, 'c', SYM[m])
            self.drop_tree.item(row, tags=(TAG[m],) if m in TAG else ())
        self._save_sel()
        self._refresh_enemies()
        self._sync_count()
        return 'break'

    def _toggle_current_global(self):
        if getattr(self, '_loading', False):
            return 'break'              # ：键盘路径也要守卫
        st = self.st
        ewl = self._ewl_items()
        skip = 0
        for row in self.g_tree.selection():
            if row not in st.gdel and row in ewl:
                skip += 1
                continue
            self.st.toggle_global(row, 'del')
        self._save_sel()
        self._refresh_global()
        if skip:
            self.status.config(text=st.t('ewl_skip_n') % skip)
        return 'break'

    # ── 全局页按钮 ──────────────────────────────────────────

    def _apply_price(self):
        if getattr(self, '_loading', False):
            return
        st = self.st
        v = self.price_var.get().strip()
        import math as _math
        if not v:
            st.price_max = -1          # 清空输入 = 清除价格过滤（旧行为保留）
            #  ：空输入时 p 从未赋值，后面 agg 循环和
            # price_done 消息都引用 p → UnboundLocalError 崩进日志。
            self._save_sel()
            self._refresh_global()
            self.status.config(text=st.t('price_done') % (0, '-'))
            return
        else:
            try:
                p = float(v)
                # 十 + ：nan/inf 与非法值/≤0 同一出口：
                # 提示 + 清空输入框 + 不改动现有 price_max（旧版反馈被 NaN
                # 修复弄丢过——isfinite(-1) 恒真导致静默落到 -1，此处恢复）
                if p <= 0 or not _math.isfinite(p):
                    raise ValueError
            except Exception:
                self.price_var.set('')
                self.status.config(text=st.t('price_bad'))
                return
            st.price_max = p
        agg = self._global_items()
        ewl = self._ewl_items()
        n = 0
        skip = 0
        for k, a in agg.items():
            if a['price'] is not None and a['price'] <= p and k not in st.gwl:
                if k in ewl:
                    skip += 1
                    continue
                st.gdel.add(k)
                st.boost.pop(k, None)
                n += 1
        self._save_sel()
        self._refresh_global()
        msg = st.t('price_done') % (n, p)
        if skip:
            msg += '\n' + st.t('ewl_skip_n') % skip
        self.status.config(text=msg)

    def _cat_bulk(self, which):
        # 加载期禁止：整类批量会改 st.gdel/gwl/boost
        if getattr(self, '_loading', False):
            return
        st = self.st
        cat = self._cat_key(self.cat_var.get().strip())
        if not cat:
            return
        # 若全局页同时选了官方子类，批量操作限定到该子类（显示名反查回原文）
        sub = ''
        sv = self.sub_var.get().strip()
        if sv and sv != st.t('gf_all'):
            sub = self._sub_key(sv)
        agg = self._global_items()
        if which == 'del':
            # 整类删除是批量破坏性操作：先列出数量让用户确认（保护项不受影响）
            targets = [k for k, a in agg.items()
                       if a['cat'] == cat and (not sub or a['sub'] == sub)
                       and k not in st.gwl]
            if not targets:
                self.status.config(text=st.t('cat_del_done') % 0)
                return
            if not messagebox.askyesno(st.t('info_title'),
                                       st.t('cat_del_confirm') % (self._cat_label(cat), len(targets))):
                return
            for k in targets:
                st.gdel.add(k)
                st.boost.pop(k, None)
            n = len(targets)
            self._save_sel()
            self._refresh_global()
            self.status.config(text=st.t('cat_del_done') % n)
            return
        n = 0
        for k, a in agg.items():
            if a['cat'] != cat or (sub and a['sub'] != sub):
                continue
            st.gwl.add(k)
            st.gdel.discard(k)
            st.boost.pop(k, None)
            n += 1
        self._save_sel()
        self._refresh_global()
        self.status.config(text=st.t('cat_wl_done') % n)

    def _wl_clear(self):
        if getattr(self, '_loading', False):
            return
        st = self.st
        if not st.gwl:
            return
        if not messagebox.askyesno(st.t('info_title'),
                                   st.t('wl_clear_confirm') % len(st.gwl)):
            return
        st.gwl = set()
        self._save_sel()
        self._refresh_global()

    def _wl_default(self):
        """默认白名单：documents（钥匙/任务纸）+ equipment（装备）。
        依据 H-1 实测：钥匙 6 铜、任务信 10 铜，按卖价筛会被当垃圾选走。"""
        if getattr(self, '_loading', False):
            return
        st = self.st
        agg = self._global_items()
        n = 0
        for k, a in agg.items():
            if a['cat'] in ('documents', 'equipment'):
                st.gwl.add(k)
                st.gdel.discard(k)
                st.boost.pop(k, None)
                n += 1
        self._save_sel()
        self._refresh_global()
        self.status.config(text=st.t('wl_default_done') % n)

    def _clear_all(self):
        # 加载期禁止：st.clear_all() 会直接清空 checked/gdel/boost
        if getattr(self, '_loading', False):
            return
        st = self.st
        c = st.counts()
        n = c['del'] + c['boost']
        if not n:
            messagebox.showinfo(st.t('info_title'), st.t('clear_all_none'))
            return
        if not messagebox.askyesno(st.t('info_title'), st.t('clear_all_confirm') % n):
            return
        st.clear_all()
        self._save_sel()
        self._refresh_global()
        self._refresh_enemies()
        if self.enemy_tree.selection():
            self._on_enemy_click()
        self._sync_count()

    def _g_sort_by(self, col):
        # L13：dl/wl 列原来映射不到 key 直接 return，点了毫无反馈
        key = {'n': 'n', 'price': 'price', 'name': 'name', 'cat': 'cat',
               'dl': 'dl', 'wl': 'wl'}.get(col)
        if not key:
            return
        if self.st.gsort == (key, False):
            self.st.gsort = (key, True)
        else:
            self.st.gsort = (key, False)
        self._refresh_global()

    # ── 掉落列表表头排序（点表头升序，再点切降序）──────────────

    def _d_sort_by(self, col):
        if self._d_sort and self._d_sort[0] == col:
            self._d_sort = (col, not self._d_sort[1])
        else:
            self._d_sort = (col, False)
        self._on_enemy_click()

    def _d_key(self, g, d, k, m, col):
        """掉落列表各列的排序键（None/缺失统一垫底）。"""
        if col == 'c':
            return {'none': 0, 'del': 1, 'wl': 2, 'gwl': 3, 'boost': 4}.get(m, 0)
        if col == 'name':
            return self._item_display(k, d.get('name') or k).lower()
        if col == 'cat':
            ci = (self.st.item_cats or {}).get(k, {})
            return (ci.get('main') or '-', ci.get('sub') or '')
        if col == 'prob':
            pb = d.get('prob')
            return (-1 if not isinstance(pb, (int, float)) else pb,
                    str(d.get('q', '')))
        if col == 'p':
            # 掉率列按「显示值」排序：有 boost 用 boost 值，否则用原始 p
            if k in self.st.boost:
                bp = self.st.boost[k]
                p = (1.0 if bp >= 1.0 else bp)
                return (p, str(d.get('q', '')))
            p = d.get('p')
            return (-1 if not isinstance(p, (int, float)) else p, str(d.get('q', '')))
        if col == 'qty':
            q = str(d.get('q', '-'))
            if q == '-':
                return (-1, q)
            parts = [int(x) for x in q.split('-') if x.strip().isdigit()]
            return (max(parts) if parts else -1, q)
        if col == 'type':
            return (d.get('type') or '')
        return (' / '.join(t[:30] for t in g['tables']
                           if str(k).isdigit() and int(k) in self.st.gd.tabs.get(t, {})) or '-')

    # ── 预设导入 / 导出（独立 JSON 文件）──────────────────────

    def _export_presets(self, which):
        """把当前预设从 nojunk2.json 导出为独立文件。"""
        if getattr(self, '_loading', False):
            return
        path = filedialog.asksaveasfilename(
            parent=self.root, defaultextension='.json',
            initialfile='nojunk2-presets-%s.json' % which,
            filetypes=[('JSON', '*.json')])
        if not path:
            return
        try:
            self.st.export_presets(path, which)
        except Exception as ex:
            messagebox.showerror(self.st.t('err_title'), '%s' % ex)
            return
        self.status.config(text=self.st.t('preset_exported') % path)

    def _import_presets(self, which):
        """从独立 JSON 文件导入预设（合并进配置，同名不覆盖）。"""
        # 加载期禁止：导入会写回 st.cfg 的预设表并触发保存
        if getattr(self, '_loading', False):
            return
        path = filedialog.askopenfilename(
            parent=self.root, filetypes=[('JSON', '*.json')])
        if not path:
            return
        try:
            n, names, skipped = self.st.import_presets(path, which)
        except Exception as ex:
            messagebox.showerror(self.st.t('err_title'), '%s' % ex)
            return
        if not n:
            messagebox.showinfo(self.st.t('info_title'), self.st.t('preset_import_none'))
            return
        self._refresh_preset_boxes()
        msg = self.st.t('preset_imported') % (n, ', '.join(names))
        if skipped:
            msg += '\n' + self.st.t('preset_import_skip') % skipped
        self.status.config(text=msg)

    def _refresh_preset_boxes(self):
        self.epreset_box.configure(values=self.st.enemy_preset_names())
        self.gpreset_box.configure(values=self.st.global_preset_names())

    # ── 预设 ────────────────────────────────────────────────

    def _save_enemy_preset(self):
        if getattr(self, '_loading', False):
            return
        name = simpledialog.askstring(self.st.t('preset_title'),
                                      self.st.t('epreset_prompt'), parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        if name in self.st.enemy_preset_names():
            if not messagebox.askyesno(self.st.t('info_title'),
                                       '预设 %s 已存在，覆盖？' % name):
                return
        self.st.save_enemy_preset(name)
        self.epreset_box.configure(values=self.st.enemy_preset_names())
        self.epreset_var.set(name)
        self.status.config(text=self.st.t('preset_saved') % name)

    def _apply_enemy_preset(self, _evt=None, name=None):
        if getattr(self, '_loading', False):
            return                  # 自审 P1-4：加载期间禁改状态
        name = name or self.epreset_var.get()
        if not self.st.apply_enemy_preset(name):
            messagebox.showinfo(self.st.t('info_title'), self.st.t('preset_none'))
            return
        self._save_sel()
        self.epreset_box.configure(values=self.st.enemy_preset_names())
        self.epreset_var.set(name)
        self._refresh_global()
        self._refresh_enemies()
        if self.enemy_tree.selection():
            self._on_enemy_click()
        self._sync_count()
        self.status.config(text=self.st.t('preset_applied') % name)

    def _del_enemy_preset(self):
        if getattr(self, '_loading', False):
            return
        name = self.epreset_var.get()
        if not name or not name.strip():
            messagebox.showinfo(self.st.t('info_title'), self.st.t('preset_none'))
            return
        # 与全局预设一致：先确认后删除（原来先删后确认，反悔时预设已丢）
        if not messagebox.askyesno(self.st.t('info_title'),
                                   self.st.t('preset_del_confirm') % name):
            return
        if not self.st.del_preset('enemy', name):
            messagebox.showinfo(self.st.t('info_title'), self.st.t('preset_none'))
            return
        self.epreset_box.configure(values=self.st.enemy_preset_names())
        self.epreset_var.set('')
        self.status.config(text=self.st.t('preset_deleted') % name)

    def _save_global_preset(self):
        if getattr(self, '_loading', False):
            return
        name = simpledialog.askstring(self.st.t('preset_title'),
                                      self.st.t('gpreset_prompt'), parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        if name in self.st.global_preset_names():
            if not messagebox.askyesno(self.st.t('info_title'),
                                       '预设 %s 已存在，覆盖？' % name):
                return
        self.st.save_global_preset(name)
        self.gpreset_box.configure(values=self.st.global_preset_names())
        self.gpreset_var.set(name)
        self.status.config(text=self.st.t('preset_saved') % name)

    def _apply_global_preset(self, _evt=None, name=None):
        if getattr(self, '_loading', False):
            return                  # 自审 P1-4：加载期间禁改状态
        name = name or self.gpreset_var.get()
        if not self.st.apply_global_preset(name):
            messagebox.showinfo(self.st.t('info_title'), self.st.t('preset_none'))
            return
        st = self.st
        # 敌人白名单优先：预设恢复的全局删除也受保护（P4）
        ewl = self._ewl_items()
        blocked = [k for k in st.gdel if k in ewl]
        if blocked:
            for k in blocked:
                st.gdel.discard(k)
        self.price_var.set('' if st.price_max < 0 else str(st.price_max))
        self._save_sel()
        self.gpreset_box.configure(values=st.global_preset_names())
        self.gpreset_var.set(name)
        self._refresh_global()
        self._sync_count()
        msg = st.t('preset_applied') % name
        if blocked:
            msg += '\n' + st.t('ewl_skip_n') % len(blocked)
        self.status.config(text=msg)

    def _del_global_preset(self):
        if getattr(self, '_loading', False):
            return
        name = self.gpreset_var.get()
        if not name or not name.strip():
            messagebox.showinfo(self.st.t('info_title'), self.st.t('preset_none'))
            return
        # 与敌人预设一致：先确认后删除（原来直接删，连确认都没有）
        if not messagebox.askyesno(self.st.t('info_title'),
                                   self.st.t('preset_del_confirm') % name):
            return
        if not self.st.del_preset('global', name):
            messagebox.showinfo(self.st.t('info_title'), self.st.t('preset_none'))
            return
        self.gpreset_box.configure(values=self.st.global_preset_names())
        self.gpreset_var.set('')
        self.status.config(text=self.st.t('preset_deleted') % name)

    # ── 应用 / 恢复 ─────────────────────────────────────────

    def _sum_p_of(self, item):
        """该不可达项的目标掉率之和（判断「是否需要调低」用）。

        只按 st.boost 里**用户实际设定**的值累加；拿不到时返回 0（调用方据此
        决定要不要额外提示「调低」——见 _boost_infeasible_text 的注释）。
        """
        tg = item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else ()
        tot = 0.0
        for k in tg:
            try:
                tot += float((self.st.boost or {}).get(str(k), 0) or 0)
            except (TypeError, ValueError):
                pass
        return tot

    def _boost_infeasible_text(self):
        """提率不可达的提示文案（没问题则返回 ''）。

        成因分两类，**用户该做的操作完全不同**，说错成因 = 让用户对着
        正确的操作反复白试：
          SOLE_DROP     目标是该表唯一掉落物 → 归一化后概率恒为 100%，
                        调不低也调不高。这不是用户操作失误，是模型的性质：
                        真实 1140 张表里 631 张（55%）是单槽表，「先删掉
                        该表其它物品再提率」这个工作流 100% 撞它。
                        指引：想少见到它只能勾选删除，调掉率没用。
          SUM_OVER_100  桶内含两种机制，**用户的动作相反**，必须分开说：
                          (a) 该表设了多个提率目标，加总 >100%
                              → 调低其中某一项，有效
                          (b) 表里已有必然掉落的槽（r>=W）把额度占满
                              → 调低没用（实测「降低其中某项」转可行 0/1046），
                                必须删掉那些必掉级物品（1046/1046 有效）
                        两者的判据是 st._boost_infeasible_blockers 的**空/非空**
                        ——plan 只在 fixed_w>0（确有满权重槽）时记录，所以
                        非空 = (b)，空 = 只能是 (a)。
                        ⚠️ 不分开的代价是死胡同：实测「同表两个普通槽各设
                        100%」走 (a)，而旧文案让用户去删「表内的必掉级物品」
                        ——那张表里一个都没有，用户被指向不存在的操作。
        成因码来自 plan.ChangePlan.infeasible_reason（{表名: 成因码}），
        由核心层透出为 st._boost_infeasible_reason。

        第三桶 n_other 兜两种「不该编造成因」的情况：拿不到成因（{}）或
        出现了本函数不认识的成因码。与其按关键字猜、猜错就误导用户，
        不如退回合并说法只报数量。
        """
        infeas = getattr(self.st, '_boost_infeasible', None) or []
        if not infeas:
            return ''
        reasons = getattr(self.st, '_boost_infeasible_reason', None) or {}
        blockers = getattr(self.st, '_boost_infeasible_blockers', None) or {}
        # 认常量不认关键字：以后 plan 新增第三类成因（如 NO_BUDGET）时，
        # 它落进 n_other 走中性文案，不会被关键字误判成「加总超 100%」
        sole_code = getattr(plan, 'INFEAS_SOLE_DROP', 'SOLE_DROP')
        over_code = getattr(plan, 'INFEAS_SUM_OVER_100', 'SUM_OVER_100')
        n_sole = n_over = n_sum = n_other = 0
        for item in infeas:
            tname = item[0] if isinstance(item, (list, tuple)) else item
            why = reasons.get(tname) or ''
            if why == sole_code:
                n_sole += 1
            elif why == over_code:
                # 两个条件**各自独立**判断，可同时成立（C4：Σp>1 且 blockers 非空）：
                #   Σp>1          → 该说「调低其中某项」
                #   blockers 非空 → 该说「删掉占住额度的必掉级物品」
                # ⚠️ 禁止改回 if/else 互斥分流：那会让 C4 只展「删必掉级物品」半句，
                #    而用户删完后 Σp 仍然是 2，照样不可行 —— 等于诱导一次破坏性操作
                #    （删的是游戏原版必定掉落的东西）且问题没解决。必须累加。
                # ⚠️ 别用 len(targets)>=2 当 Σp>1 的代理：两项各 0.3 时长度也是 2，
                #    那时成因只是预算被占满，说「调低」是错的。
                if blockers.get(tname):
                    # (b) 表内确有必掉级槽占住额度 → 说「删掉那些必掉级物品」
                    n_over += 1
                    # 但同时可能是 C4（Σp>1 也成立）：这时「调低某项」也要说，
                    #    否则用户删完必掉级物品后 Σp 仍是 2，白做一次破坏性操作。
                    if self._sum_p_of(item) > 1.0:
                        n_sum += 1
                else:
                    # (a) blockers 为空 → 成因必然是 Σp>1（逐条排过
                    # _boost_plan 的四条不可行路径后的不变量），直接说「调低某项」。
                    # 不重算 Σp：拿不到 st.boost 时也要给得出正确指引。
                    n_sum += 1
            else:
                n_other += 1
        parts = []
        if n_sole:
            parts.append(self.st.t('boost_infeasible_sole') % n_sole)
        if n_over:
            parts.append(self.st.t('boost_infeasible_over') % n_over)
        if n_sum:
            parts.append(self.st.t('boost_infeasible_sum') % n_sum)
        if n_other:
            parts.append(self.st.t('boost_infeasible') % n_other)
        return '\n'.join(parts)

    def _apply(self):
        # 加载期禁止写盘：此时 st.gd 可能还是上一份旧解析，写进去就是错数据
        if getattr(self, '_loading', False):
            return
        st = self.st
        if not st.gd:
            messagebox.showerror(st.t('err_title'), st.t('need_game'))
            return
        # 实际有「真勾选」的条目数（checked 字典里可能全是 False）
        # 提率要用 scoped_boost_count：目标只在非掉落物表时写盘一个字都不动，
        # 按 len(boost) 算会让用户以为有改动，实际 no_sel——静默欺骗
        n = (len([1 for v in st.checked.values() if v])
             + len(st.gdel) + st.scoped_boost_count())
        if not n:
            #  修复：全取消后再点应用必须能到达核心的「空选择=回原版」
            # 分支——否则旧效果残留磁盘（实测复现）。overlay 存在时转恢复流程。
            import os as _os
            ov = _os.path.join(st.game_dir or '', core.OVERLAY_REL)
            if st.game_dir and _os.path.exists(ov):
                if messagebox.askyesno(st.t('info_title'),
                                       st.t('restore_confirm')):
                    ok, msg = st.apply_changes()   # 核心空选择分支：备份+删 overlay
                    if ok:
                        st.checked = {}
                        st.gdel = set()
                        st.boost = {}
                        st.cfg['checked'] = {}
                        st.save_cfg()
                        self._save_sel()
                        self._refresh_enemies()
                        self._refresh_global()
                        self._sync_count()
                        messagebox.showinfo(st.t('info_title'),
                                            st.t('restore_done') % (msg[-1] or ''))
                    else:
                        # 七轮 P2：与主路径同口径，走统一错误处理（指纹不符给覆盖确认）
                        # 八轮 P1：_handle_write_error 只认 err_game_changed，
                        # err_raw/err_perm 会被静默吞掉（实测：只读 overlay 时
                        # 用户以为已恢复原版，实际没删）——未处理时必须兜底弹窗
                        if not self._handle_write_error(msg, self._apply):
                            messagebox.showerror(st.t('err_title'), str(msg))
                return
            messagebox.showinfo(st.t('info_title'), st.t('no_sel'))
            return
        # 未收录名字条目统计：仅风险提示，不拒绝、不阻断写盘
        target_keys = set(st.gdel)
        target_keys.update(k for (nm, k), v in st.checked.items() if v)
        target_keys.update(st.boost)
        n_unnamed = 0
        for k in target_keys:
            try:
                if self._item_display(k, k) == self.st.t('unnamed_mark') % k:
                    n_unnamed += 1
            except Exception:
                pass
        risk = ('\n' + st.t('risk_unnamed') % n_unnamed) if n_unnamed else ''
        # 越界槽默认已被 _slot_allowed 挡住，只有用户显式勾了「包含非击杀
        # 来源」才会走到这里——既然是花钱买的内容或好感度回礼，放行前
        # 必须把话说清楚。
        if st.include_nondrop:
            nrw = 0
            for k in target_keys:
                nrw += len(st.nondrop_slots_of(k))
            if nrw and not messagebox.askyesno(
                    st.t('info_title'), st.t('nondrop_confirm') % nrw):
                return
        # 写盘是危险操作：应用前先弹「影响面预览」（槽数/敌人数/提率/保护项）再确认
        pv = st.preview_changes()
        if pv is not None:
            # 共享表警告：有槽在 >=5 组共用的表里，清零会影响所有共用者
            shared_warn = ('\n' + st.t('preview_shared_warn') % pv['shared']
                           ) if pv.get('shared') else ''
            if not messagebox.askyesno(
                    st.t('info_title'),
                    (st.t('preview_text') % (pv['slots'], pv['enemies'],
                                             pv['boosts'], pv['protected']))
                    + shared_warn + risk):
                return
        else:
            if not messagebox.askyesno(st.t('info_title'), (st.t('confirm') % n) + risk):
                return
        ok, r = None, None
        try:
            ok, r = st.apply_changes()
        except Exception as ex:
            # P0-1 兜底：写盘路径任何意外异常都必须变成弹框，不能抛穿 Tk 回调
            messagebox.showerror(st.t('err_title'), '%s' % ex)
            return
        if not ok:
            if self._handle_write_error(r, self._apply):
                return
            if isinstance(r, tuple):
                key, arg = r
                messagebox.showerror(st.t('err_title'), st.t(key) % arg)
            else:
                # ：全部提率都不可达时走进来的是 no_sel，
                # 只说「没有选中任何修改」等于骗人——用户明明选了。
                # 这里优先说真话：被整表跳过的表各有成因（唯一掉落物 /
                # 加总超 100%），成因不同用户该做的操作也不同，必须分开说
                # ——旧版一律说「加总超过 100%」，而用户明明只设了 50%。
                infeas_txt = self._boost_infeasible_text()
                if r == 'no_sel' and infeas_txt:
                    messagebox.showerror(st.t('err_title'), infeas_txt)
                else:
                    messagebox.showerror(st.t('err_title'), st.t(r))
            return
        nslots, okzero, nboost, okb, bak = r
        # 写盘成功后预热重建 cdloader VFS 并收敛 meta stub（失败仅提示不阻断）
        # 目的：写盘改了 mods 指纹，下次启动加载器会全量重建出含 meta stub
        # 的映射树导致游戏崩溃；这里立即 build-only 重建 + 删 meta 条目，
        # 让下次启动直接 cache hit 且不崩。收敛失败绝不回滚写盘结果。
        try:
            conv = core.auto_rebuild_and_converge(st.game_dir)
        except Exception as ex:
            conv = {'ok': False, 'error': '%s' % ex}
        vfs_note = ''
        if conv.get('ok'):
            vfs_note = ('\n[VFS] 已预热重建并收敛映射树（%d→%d 条，%.2fs）'
                        % (conv.get('entries_before', 0),
                           conv.get('entries_after', 0),
                           conv.get('rebuild_seconds', 0.0)))
            if conv.get('loader_located') is False:
                vfs_note += ('\n[自检] %s' % conv.get('loader_detail', '掉落表未定位'))
        else:
            vfs_note = ('\n[VFS] 预热失败（不影响本次写盘）：%s'
                        % conv.get('error'))
        msg = st.t('applied') % okzero + vfs_note
        # ：兼容性自检门禁——cdloader 无法解析新版 LEB128 pamt 索引时，
        # 掉落表 target=null，写盘结果在游戏中不会生效。必须用警告弹窗
        # 让用户停下确认，而不是让成功绿勾掩盖静默失效。不阻断写盘流程。
        if conv.get('ok') and conv.get('loader_located') is False:
            messagebox.showwarning(
                st.t('info_title'),
                msg + '\n\n警告：加载器无法定位掉落表（索引格式不兼容），本次修改不会在游戏中生效，请更新 cdloader 加载器')
            self._refresh_global()
            self._refresh_enemies()
            if self.enemy_tree.selection():
                self._on_enemy_click()
            self._sync_count()
            return
        if nboost:
            msg += '\n' + st.t('boost_done') % (nboost, okb)
        # ：部分表提率不可达时，其余改动照常生效，
        # 必须把被跳过的表数说出来，否则用户以为全改上了
        infeas_txt = self._boost_infeasible_text()
        if infeas_txt:
            msg += '\n' + infeas_txt
        if bak:
            msg += '\n' + st.t('backup_line') % bak
        # ：区分「全部生效」与「部分成功」。
        # 删除生效但提率一件都没落地时（必掉撞 BUDGET 占必掉尝试的 25.7%），
        # 旧版照样弹成功绿勾，只在末尾附注一行"跳过 N 张表" —— 用户以为成了。
        # 这里不动 ok 返回值（改语义会波及 E1/H1 的门禁断言），只换弹窗类型：
        # warning 自带图标，用户会停下来读。
        if nboost == 0 and infeas_txt:
            messagebox.showwarning(st.t('info_title'), msg)
        else:
            messagebox.showinfo(st.t('info_title'), msg)
        # 应用写盘后统一刷新两侧列表与计数，避免界面停留在应用前状态
        self._refresh_global()
        self._refresh_enemies()
        if self.enemy_tree.selection():
            self._on_enemy_click()
        self._sync_count()

    def _export_patch(self):
        """导出字节补丁 JSON（CDUMM / JSON Mod Manager / DMM 通用）。
        只生成文件不写游戏：给不用 CDUMM overlay 的玩家一条活路。"""
        # 加载期禁止：导出基于当前 st 的计划，加载中数据是半初始化状态
        if getattr(self, '_loading', False):
            return
        st = self.st
        if not st.gd:
            messagebox.showerror(st.t('err_title'), st.t('need_game'))
            return
        path = filedialog.asksaveasfilename(
            parent=self.root, defaultextension='.json',
            initialfile='NoJunk2-drops.json',
            filetypes=[('JSON', '*.json')])
        if not path:
            return
        ok, r = st.export_patch_json(path)
        if not ok:
            key = r if r == 'no_sel' else None
            if key:
                messagebox.showinfo(st.t('info_title'), st.t('no_sel'))
            else:
                messagebox.showerror(st.t('err_title'), '%s' % r)
            return
        self.status.config(text=st.t('export_patch_done') % (r, path))

    def _restore_vanilla(self):
        # 加载期禁止：恢复原版会删 overlay，且 st 正被后台线程改写
        if getattr(self, '_loading', False):
            return
        st = self.st
        if not messagebox.askyesno(st.t('info_title'), st.t('restore_confirm')):
            return
        ok, r = st.restore_vanilla()
        if not ok:
            messagebox.showinfo(st.t('info_title'),
                                st.t(r) if not isinstance(r, tuple) else st.t(r[0]) % r[1])
            return
        self._save_sel()
        self._refresh_global()
        self._refresh_enemies()
        if self.enemy_tree.selection():
            self._on_enemy_click()
        self._sync_count()
        messagebox.showinfo(st.t('info_title'), st.t('restore_done') % r)

    def _show_preset_help(self):
        """预设使用流程帮助：先选好敌人/掉落 → 存为预设 → 再点应用写入游戏。"""
        messagebox.showinfo(self.st.t('preset_help_title'),
                            self.st.t('preset_help_text'))

    def _about(self):
        messagebox.showinfo(self.st.t('about'),
                            self.st.t('about_version') % core.VERSION
                            + '\nAuthor: 135207 (Nexus Mods)\n\n'
                            + self.st.t('about_text'))

    def _open_lang_dir(self):
        d = os.path.join(core.res_dir(), 'lang')
        try:
            if sys.platform.startswith('win'):
                os.startfile(d)
            else:
                import subprocess
                subprocess.run(['xdg-open', d])
        except Exception:
            messagebox.showinfo(self.st.t('info_title'), d)

    def _on_close(self):
        """退出前保存窗口尺寸/位置到配置，下次启动恢复。"""
        #  P2-2：取消挂起的 after 定时器（防御性——嵌套 update 场景
        # 残留定时器可能打到已销毁部件）
        for _a in ('_search_after', '_gsearch_after', '_poll_after'):
            _t = getattr(self, _a, None)
            if _t:
                try:
                    self.root.after_cancel(_t)
                except Exception:
                    pass
        try:
            self.st.cfg['win_geometry'] = self.root.geometry()
            self.st.save_cfg()
        except Exception:
            pass
        self.root.destroy()

    # ════════════════════════════════════════════════════════
    #  工具
    # ════════════════════════════════════════════════════════

    def _set_status(self, text):
        self.status.config(text=text)

    def _cur_group(self):
        for row in self.enemy_tree.selection():
            g = self.st.group_of(row)
            if g:
                return g
        return None

    def _cat_of(self, k):
        """物品类别（官方 item_cats 固化优先；回退 itemcats.json 只读缓存）。"""
        ic = getattr(self.st, 'item_cats', None)
        if ic:
            ci = ic.get(str(k), {})
            if ci.get('main'):
                return ci['main']
        cf = os.path.join(core.res_dir(), 'data', 'itemcats.json')
        if not os.path.exists(cf):
            return ''
        cats = getattr(self, '_cat_cache', None)
        if cats is None:
            try:
                cats = json.load(open(cf, encoding='utf-8'))
                self._cat_cache = cats
            except Exception:
                return ''
        return cats.get(k, '')

    def _ewl_items(self):
        """所有敌人白名单物品的并集（item_key 集合），供 gdel 入口做保护过滤（P4）。"""
        s = set()
        for ks in self.st.ewl.values():
            s.update(ks)
        return s

    def _item_name(self, k):
        agg = self._global_items()
        return agg.get(k, {}).get('name', k)

    def _drop_name_map(self):
        """掉落ID→名字映射：来自 npc_drops_<lang>.json 的 drops[].name（原数据自带），随界面语言，缺键用英文补。"""
        key = '_drop_names_' + str(self.st.lang)
        m = getattr(self, key, None)
        if m is not None:
            return m
        m = {}
        # 补充名库优先：data/drop_names_<lang>.json（玩家可维护的掉落翻译表，
        # 结构 {"items": {"掉落id": "名字"}}），存在则合并进映射（setdefault 使其优先）
        for code in (self.st.lang, 'en'):
            fp = os.path.join(core.res_dir(), 'data', 'drop_names_%s.json' % code)
            if not os.path.exists(fp):
                continue
            try:
                data = json.load(open(fp, encoding='utf-8'))
                items = data.get('items', {}) if isinstance(data, dict) else {}
                for k, nm in items.items():
                    if k and nm:
                        m.setdefault(str(k), nm)
            except Exception:
                continue
        for code in (self.st.lang, 'en'):
            fp = os.path.join(core.res_dir(), 'npc_drops_%s.json' % code)
            if not os.path.exists(fp):
                continue
            try:
                data = json.load(open(fp, encoding='utf-8'))
            except Exception:
                continue
            for info in data.values():
                for dr in info.get('drops', []):
                    k = str(dr.get('id'))
                    nm = dr.get('name')
                    if k and nm:
                        m.setdefault(k, nm)
        self.__dict__[key] = m
        return m

    def _item_display(self, k, fallback=None):
        """掉落物列显示 '名字 (ID)'；查不到名字时回退 fallback / 纯 ID。

        名字链路（只读补查，不改数据层）：
          1) npc_drops_<lang>.json drops[].name（原数据自带名字，随语言切换）
          2) hash_map.json items: 掉落ID → 大整数；gd.names: 大整数 → 名字（兜底）
        """
        try:
            nm = self._drop_name_map().get(str(k))
            if nm:
                return '%s (%s)' % (nm, k)
        except Exception:
            pass
        try:
            hm = getattr(self, '_hm_items', None)
            if hm is None:
                hp = os.path.join(core.res_dir(), 'data', 'hash_map.json')
                if os.path.exists(hp):
                    hm = json.load(open(hp, encoding='utf-8')).get('items', {})
                else:
                    hm = {}
                self._hm_items = hm
            h = hm.get(str(k))
            if h is not None:
                nm = self.st.gd.names.get(str(h))
                if nm:
                    return '%s (%s)' % (nm, k)
        except Exception:
            pass
        # 名称库（npc_drops / hash_map）都没命中：明确标记「未命名」，防止被当成正常物品
        if fallback and str(fallback) != str(k):
            return fallback
        return self.st.t('unnamed_mark') % k

    def _save_sel(self):
        """把当前选择写进配置。"""
        st = self.st
        st.cfg['global_del'] = sorted(st.gdel)
        st.cfg['whitelist'] = sorted(st.gwl)
        st.cfg['boost'] = dict(st.boost)
        st.cfg['enemy_wl'] = {k: list(v) for k, v in st.ewl.items()}
        st.cfg['price_max'] = st.price_max
        # 敌人勾选（删除项）持久化：只存真勾选，格式与预设一致 {gid: [item_key]}
        sel = {}
        for (gid, k), v in st.checked.items():
            if v:
                sel.setdefault(gid, []).append(k)
        st.cfg['checked'] = {g: sorted(set(ks)) for g, ks in sel.items()}
        st.save_cfg(self._set_status)

    def _rebuild_ui(self):
        """切语言：整块重建界面。先拆旧的，再按当前语言重建。"""
        # 取消可能挂起的搜索防抖定时器（重建后旧回调引用已销毁部件）
        for _a in ('_search_after', '_gsearch_after'):
            _t = getattr(self, _a, None)
            if _t:
                try:
                    self.root.after_cancel(_t)
                except Exception:
                    pass
                setattr(self, _a, None)
        keep_sel = [s for s in self.enemy_tree.selection()] if hasattr(self, 'enemy_tree') else []
        for w in self.root.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass
        self._build_ui()
        #  P1-2：加载中切语言时，重建出来的按钮默认 normal，
        # 必须把加载态的 disabled 恢复回去（否则用户能点到半初始化数据）
        if getattr(self, '_loading', False):
            self.load_btn.config(state='disabled')
            self.apply_btn.config(state='disabled')
            self.lang_box.config(state='disabled')
            if hasattr(self, 'g_apply_btn'):
                self.g_apply_btn.config(state='disabled')
        self._refresh_chips()
        self._refresh_enemies()
        self._refresh_global()
        if keep_sel:
            still = [s for s in keep_sel if self.enemy_tree.exists(s)]
        else:
            still = []
        # ⚠️ still 为空时要能回退到「选第一个」—— 原来写成 elif 挂在 if keep_sel 上，
        #    导致切语言后旧名字对不上时就什么都不选，右侧一片空白（用户会遇到）
        if still:
            self._suspend_sel = True
            try:
                self.enemy_tree.selection_set(still)
            finally:
                self._suspend_sel = False
            self._on_enemy_click()
        elif self.enemy_tree.get_children():
            self._suspend_sel = True
            try:
                self.enemy_tree.selection_set(self.enemy_tree.get_children()[0])
            finally:
                self._suspend_sel = False
            self._on_enemy_click()


def hide_console():
    """用 python.exe 启动时隐藏黑框（pythonw 下 GetConsoleWindow 返回 0）。"""
    try:
        import ctypes
        whnd = ctypes.windll.kernel32.GetConsoleWindow()
        if whnd:
            ctypes.windll.user32.ShowWindow(whnd, 0)
    except Exception:
        pass


def main():
    core.install_excepthook()     # 黑匣子：未捕获异常落 nojunk2.log
    core.log('启动 NoJunk2 %s' % core.VERSION)
    hide_console()
    root = tk.Tk()
    # ⚠️ Tk 回调里的异常**不走 sys.excepthook**：Tk 自己吞下来交给
    # report_callback_exception，默认行为是 print(..., file=sys.stderr)。
    # 用 pythonw 启动（双击 NoJunk2.pyw）没有控制台 → 用户点了没反应，
    # nojunk2.log 里也一个字没有（实测：会抛异常的 after 回调，
    # sys.excepthook 命中 0 次）。这里接管：落日志 + 弹窗。
    _box = {}

    def _tk_exc(exc_type, exc_value, exc_tb):
        try:
            import traceback
            core.log('!! Tk 回调异常:\n' + ''.join(
                traceback.format_exception(exc_type, exc_value, exc_tb)))
        except Exception:
            pass                # 记日志这件事本身绝不能再抛
        try:
            app = _box.get('app')
            # 标题走语言包；app 还没建好（构造期）时回退硬编码，绝不因此不弹窗
            title = app.st.t('err_title') if app is not None else '错误'
            messagebox.showerror(title, '%s: %s' % (
                getattr(exc_type, '__name__', 'Exception'), exc_value))
        except Exception:
            pass                # 弹窗失败也不能再抛：否则递归打回本钩子

    root.report_callback_exception = _tk_exc
    _box['app'] = App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
