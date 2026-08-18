# -*- coding: utf-8 -*-
"""Tab 层：六个 tab 的渲染函数（BUG-020 刀 4/5 从 app.py 原样搬入）。

每个模块暴露 render(tab, ...)，tab 对象为 app.py 里 st.tabs() 的产物；
数据全部显式收参，不读 app.py 模块级全局。
"""
