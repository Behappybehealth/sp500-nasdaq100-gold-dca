# -*- coding: utf-8 -*-
"""Tab4 历史记录：回读 transactions / observations 两张表原样展示。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import storage


def render(tab, user: str):
    with tab:
        st.subheader("成交记录")
        _tx_rows = storage.read_rows("transactions", user)
        if _tx_rows:
            st.dataframe(pd.DataFrame(_tx_rows), width="stretch", hide_index=True)
        else:
            st.info("暂无成交记录。")
        st.subheader("观察记录")
        _obs_rows = storage.read_rows("observations", user)
        if _obs_rows:
            st.dataframe(
                pd.DataFrame(_obs_rows), width="stretch", hide_index=True
            )
        else:
            st.info("暂无观察记录。")
