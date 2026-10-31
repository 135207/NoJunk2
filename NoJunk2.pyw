# -*- coding: utf-8 -*-
"""NoJunk2.pyw —— 双击即启动（一键启动器）。

装了 Python 3.10+ 的 Windows 上，.pyw 默认关联 pythonw.exe：
双击本文件 = 无控制台直接打开 NoJunk2 窗口，效果等同 NoJunk2.exe。
（zip 里不带 .bat 启动器——.bat 是 Nexus 安全启发式的高危文件类型，
而 .pyw 是普通 Python 文件，不会被拦。）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nojunk_ui

if __name__ == '__main__':
    nojunk_ui.main()
