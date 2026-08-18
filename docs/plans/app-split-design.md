# app.py 拆分设计方案

> 2026-08-17 撰写。**这是设计文档，不是施工记录。** 未经确认前不动一行代码。
> 前置条件：迁移后回归门禁已全绿（引擎 exit 0 / 协议探针 script_finished=0 / 增量缓存原地更新 / Sheets 连通 2 用户）。

---

## 0. 结论先行

| | |
|---|---|
| **现状** | app.py 1984 行，单文件承载 CSS + 认证 + 服务 + 侧栏 + 6 个 tab |
| **要做的** | 拆成 1 个入口 + 13 个模块，app.py 收口到 ≈120 行 |
| **不做的** | 不改任何计算逻辑、不改 storage.py、不改认证的两段式时序 |
| **最大的一块** | tab5 回测结果 638 行（占 32%），其中 **415 行是硬编码数据**，出到 JSON 后剩 ≈250 行 |
| **真正的拦路虎** | 不是行数，是 Streamlit 的脚本执行模型（见 §2） |
| **建议节奏** | 6 刀，每刀独立 commit + 独立回归，任何一刀都能单独停下不影响可用性 |

---

## 1. 业务逻辑地图（先对清楚逻辑）

### 1.1 一次页面渲染的真实时序

app.py **不是模块，是一个从头跑到尾的脚本**。每次用户点任何控件，整个文件重新执行一遍。

```
① 1–51    解析 --base-dir → 定 BASE/DATA_DIR → storage.init() → 读 config.json → set_page_config
② 54–229  注入全局 CSS
③ 230–280 定义三个遮罩组件（show_loading / show_sync_mask / show_auth_mask）
④ 281–556 认证门闸 ←── 未登录就 st.stop()，下面的代码根本不执行
⑤ 557–731 定义服务函数（run_model / 行情 / 曲线）
⑥ 732–993 渲染侧边栏 ←── 副作用：在这里跑模型，产出 result/dec/ms/pf
⑦ 994–1005 声明 6 个 tab
⑧ 1006–1984 依次渲染 6 个 tab ←── 消费 ⑥ 产出的变量
```

**关键点：⑥ 既是 UI 又是业务入口。** 侧边栏渲染的过程中调用 `run_model()`，把决策结果留在模块级作用域，下游 6 个 tab 直接引用。这就是为什么"把 tab 搬出去"必须先解决"结果怎么传进去"。

### 1.2 三条业务链路

**A. 认证链（281–556，327 行）**

三阶段状态机，全部走 `st.session_state`：

| 阶段 | 触发 | 做什么 |
|---|---|---|
| `login` | 默认 | 名字 + PIN 校验 → `storage.authenticate()` |
| `activate` | 账号存在但未激活 | 首次设 PIN → `storage.set_pin()` |
| `bootstrap` | users 表为空 | 首个注册者自动成为 admin → `storage.create_user()` |

**两段式设计（踩过 3 轮坑，不要动）：** 点击那一趟**零网络 I/O**（用户名单取 session 缓存），把意图写进 `session_state["_auth"]` → `ph.empty()` 把登录页从 DOM 里**真删除**（不是遮住）→ 挂 `show_auth_mask` → `st.rerun()`。下一趟才在遮罩后面做全部网络工作。
原因：以 `st.rerun()` 结束的运行不会清除该趟未重新渲染的旧元素，登录表单会残留并漂在主应用上。

**B. 决策链（557–731 定义 + 732–993 执行）**

```
侧栏渲染 ──→ run_model(None) ──subprocess──→ scripts/dca_calculator.py ──→ JSON
                    │
                    └─→ result / dec / ms / pf 落在模块作用域
                                │
              ┌─────────────────┼──────────────────┐
            tab1 今日模拟      tab2 持仓曲线      tab3 记账
         (result×9 dec×6 ms×4)  (pf×8)        (result×3 dec×3)
```

**已知业务缺陷：模型会跑两次。** 侧栏先 `run_model(None)` 自动定额；若用户手填了金额（`amount_in > 0`），再 `run_model(amount_in)` **整体重跑一遍子进程**。每次调金额滑块都要多一次进程启动 + 可能的行情请求。

**C. 记账链（tab3 写 → tab4 读）**

```
tab3  用户回报成交 → session_state["pending_tx"] 暂存 → 复述确认 → storage.append_row("transactions")
      主动跳过     → session_state["pending_obs"]              → storage.append_row("observations")
tab4  storage.read_rows("transactions") + read_rows("observations") → 两张表原样展示
```

tab4 是这条链的**读侧**，只有 14 行，业务上和 tab3 是一件事。

### 1.3 六个 tab 的业务职责

| tab | 行区间 | 行数 | 业务职责 | 依赖 |
|---|---|---:|---|---|
| 🎯 今日模拟 | 1006–1085 | 80 | 今日建议金额/部署系数/三资产分配/三档执行方案 | result, dec, ms, ASSETS |
| 📊 持仓与曲线 | 1086–1149 | 64 | 持仓汇总、估值、浮盈亏、XIRR、净值曲线 | pf, ASSETS |
| ✍️ 记账 | 1150–1257 | 108 | 回报成交 / 主动跳过，二次确认后落库 | result, dec, ASSETS, CURRENT_USER |
| 📜 历史记录 | 1258–1271 | 14 | 回读 transactions / observations | CURRENT_USER |
| 🧪 回测结果 | 1272–1909 | **638** | 5 段静态回测报告（见 §1.4） | BACKTEST_DIR |
| 📖 策略说明 | 1910–1984 | 75 | 一大段 markdown 文档 | 无 |

### 1.4 tab5 的内部构成（这是最大的一块）

| 段 | 行区间 | 行数 | 数据来源 |
|---|---|---:|---|
| 一、三策略对比 | 1272–1362 | 91 | ✅ 已读 `results_compare3.json` |
| 二、为什么定额等比最高 | 1363–1375 | 13 | 纯 markdown |
| 三、单品种动态vs固定 | 1376–1428 | 53 | ✅ 已读 `results_single_compare.json` |
| ├ 标普500 滚动表 | 1429–1525 | 97 | ❌ **硬编码 dict 字面量** |
| ├ 纳指100 滚动表 | 1526–1632 | 107 | ❌ **硬编码** |
| ├ 黄金 滚动表 | 1633–1731 | 99 | ❌ **硬编码** |
| └ 沪深300 滚动表 | 1732–1833 | 102 | ❌ **硬编码** |
| 四、四标的横向对比 | 1834–1877 | 44 | ❌ **硬编码** |
| 五、综合结论 | 1878–1909 | 32 | 纯 markdown |

**同一个 tab 里两种写法并存**：前两段规规矩矩读 JSON，后五张表把 415 行数据硬写在代码里。这不是风格问题——回测数据更新时要改代码，改代码就要重新回归整个应用。

### 1.5 全局耦合清单（实测，不是估计）

**模块级全局：9 个。** 关键发现——它们的作用域比想象的窄得多：

| 全局 | 总用量 | 侧栏 | tab1 | tab2 | tab3 | tab4 | tab5 | tab6 | 归属判定 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `CURRENT_USER` | 15 | 8 | 0 | 0 | 2 | 2 | 0 | 0 | 会话态 |
| `DATA_DIR` | 9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **纯服务层** |
| `ASSETS` | 7 | 0 | 1 | 1 | 4 | 0 | 0 | 0 | 配置 |
| `CODE_DIR` | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **纯服务层** |
| `BASE` | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **纯服务层** |
| `CONFIG` | 3 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 配置 |
| `TX_CSV` | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **纯服务层** |
| `BACKTEST_DIR` | 3 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | **只 tab5 用** |
| `OBS_CSV` | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **死代码** |

`DATA_DIR / CODE_DIR / BASE / TX_CSV` 在**任何 UI 代码里都是 0 次**——它们只被服务函数用。这意味着 UI 层根本不需要看见路径，Context 可以做得很薄。

**session_state key：11 个。** `synced`(5) `user`(4) `_names`(4) `_login_err`(4) `_auth`(3) `pending_tx`(2) `activating`(2) `pending_obs` `_boot_err` `_act_err`，另有 13 处 `.pop()` / 6 处 `.get()` / 1 处 `.clear()`。其中 8 个属于认证链，3 个属于记账链——**没有跨链共享**，可以随各自模块一起搬。

**storage 接口：18 个函数**（`sync_local`×4, `read_rows`×2, `list_users`×2, `is_admin`×2, `append_row`×2, 其余各 1）。这一层已经是干净边界，本次不动。

---

## 2. 为什么原草案（CLAUDE.md 里那份）不能直接用

原草案是纸上估的，实测后有 4 处偏差：

| 原草案 | 实测 | 影响 |
|---|---|---|
| `auth.py ~200 行` | 认证实为 **327 行** | 低估 60%，且其中约一半是 HTML 字符串 |
| `backtest.py` 未估行数 | **638 行**，占全文件 32% | 拆完仍是最大文件——除非先把数据抽出去 |
| `history.py` 独立文件 | tab4 只有 **14 行** | 建独立文件的 import 开销大于内容 |
| 按"UI/服务/tab"三层直接搬 | **globals 是脚本级共享的** | 子模块 `from app import X` = 循环导入 + 脚本二次执行 |

**最后一条是硬阻塞。** app.py 是被 Streamlit 当脚本执行的，不是可 import 的模块。所以：

> **传参对象必须先建，或与第一刀同时建。** 不能"先搬 tab 再想办法传值"。

---

## 3. 目标架构

### 3.1 六个设计决策

**决策 1：显式传参，禁止 `from app import *`。**
子模块一律 `def render(ctx): ...`，由 app.py 调用时把 context 递进去。这样每个模块都能单独 import 做单元测试，也不会触发脚本二次执行。

**决策 2：Context 分两层，不做成一个大杂烩。**
依据 §1.5 的实测依赖表——路径类全局零个 UI 用到，决策结果只有 3 个 tab 用到：

```python
# src/context.py（≈70 行，不 import streamlit）
@dataclass(frozen=True)
class Paths:                    # 启动期确定，全程不变
    base: Path                  # --base-dir 或代码目录
    code_dir: Path
    data_dir: Path
    backtest_dir: Path
    config: dict
    assets: list                # = config["assets"]

@dataclass
class Decision:                 # 侧栏产出，可能被重算
    result: dict                # run_model 的原始 JSON
    dec: dict                   # result["decision"]
    ms: dict                    # result["monthly_budget_status"]
    pf: dict                    # result["portfolio"]

def build_paths(argv) -> Paths: ...   # 把 1–51 行的启动逻辑收进来
```

`CURRENT_USER` 不进 Context——它是会话态，从 `st.session_state["user"]` 取，认证模块暴露一个 `current_user()` 即可。

**决策 3：先"零风险搬运"，后"业务改动"。**
第 1–3 刀只挪位置、不改逻辑（CSS、遮罩、服务函数、tab5 数据外置）。第 4–6 刀才动结构。这样如果中途出问题，能精确定位到是哪一类改动引入的。

**决策 4：认证放最后一刀，不是第一刀。**
它是唯一含 `st.stop()` 的顶层控制流，两段式遮罩踩过 3 轮坑（DOM 残留 / 遮罩透明），改错了直接锁死登录入口。而且 327 行里约一半是 HTML 字符串常量，搬运收益低、风险高。**先把它围起来，最后再动。**

**决策 5：tab4 并入 `records.py`，不建独立文件。**
14 行，读的是 tab3 写的同两张表，业务上是记账链的读侧。UI 上仍然是 6 个 tab，只是代码住在一起。

**决策 6：tab5 的 415 行数据出到 `backtest/rolling_tables.json`。**
和 tab5 已有的 `results_compare3.json` / `results_single_compare.json` 读法完全一致（`_load_json(BACKTEST_DIR / ...)`），5 张表用一个渲染循环覆盖。

### 3.2 目录与行数预算

```
app.py                          ≈ 120   ← 只剩：启动、注入 CSS、门闸、侧栏、6 个 render 调用
storage.py                        442   ← 不动
scripts/dca_calculator.py         930   ← 不动
src/
├── context.py                  ≈  70   Paths / Decision / build_paths
├── ui/
│   ├── styles.py               ≈ 180   全局 CSS 原样搬（54–229）
│   ├── overlays.py             ≈  55   show_loading / show_sync_mask / show_auth_mask（230–280）
│   ├── auth.py                 ≈ 310   登录页 + 三阶段门闸 + current_user()（281–556）
│   └── sidebar.py              ≈ 270   行情/预算/汇率/管理员，返回 Decision（732–993）
├── services/
│   ├── model.py                ≈  60   run_model / parse_wide_table
│   ├── quotes.py               ≈  70   fetch_xau_spot / fetch_btc
│   └── curves.py               ≈  80   load_price_series / portfolio_curve / _load_json
└── tabs/
    ├── today.py                ≈  85   tab1
    ├── holdings.py             ≈  70   tab2
    ├── records.py              ≈ 125   tab3 + tab4
    ├── backtest.py             ≈ 250   tab5（数据已外置）
    └── strategy.py             ≈  80   tab6
backtest/rolling_tables.json    (415 行数据，不计代码行)
```

**最大文件从 1984 → 310。** 全部代码行合计 ≈1825（比现在少 ≈160，来自死代码清理和数据外置的收敛）。

---

## 4. 施工顺序（6 刀）

每一刀都是独立 commit，都能单独回归，都能停下不影响可用性。

### 第 1 刀｜数据外置：tab5 的 415 行 → JSON

- **动**：新建 `backtest/rolling_tables.json`；tab5 内 5 处 `xxx_rolling = [...]` / `cross_rows = [...]` 换成读 JSON + 一个渲染循环。
- **行数**：app.py 1984 → **≈1580**（−404）
- **风险**：**最低**。纯数据搬运，可以逐字节对比渲染结果。
- **为什么第一刀**：一刀砍掉 20% 行数，不碰任何控制流，也不需要 Context 就位。立刻验证"我们的回归门禁真的能发现问题吗"。
- **回归**：对比 tab5 5 张表的渲染 DataFrame 与改前完全一致（可导出 CSV 做 diff）。

### 第 2 刀｜建 Context + 搬服务层

- **动**：新建 `src/context.py` `src/services/{model,quotes,curves}.py`；把 1–51 的启动逻辑收进 `build_paths()`；服务函数改成显式收 `paths` 参数。顺手删死代码 `append_csv`（583）、`OBS_CSV`（40）。
- **行数**：app.py ≈1580 → **≈1370**（−210）
- **风险**：中低。服务函数只有 8 个调用点，全在侧栏和 tab 里。
- **回归**：引擎自检 + 协议探针 + 手点侧栏行情刷新。
- **注意**：`run_model` 用 subprocess 调 `scripts/dca_calculator.py`，路径基准从 `CODE_DIR` 来，搬模块后 `Path(__file__).parent` 会多一层——**这是本刀唯一的真实陷阱**，必须走 `paths.code_dir` 而不是重新算。

### 第 3 刀｜搬 CSS + 遮罩

- **动**：`src/ui/styles.py` `src/ui/overlays.py`，原样搬 227 行。
- **行数**：app.py ≈1370 → **≈1145**（−225）
- **风险**：低。但遮罩被认证链用了 10 次，改完要**视觉级确认**（截图或查 computedStyle 背景不透明度），不能只看 DOM 里元素存在——这个坑踩过。
- **回归**：协议探针 + 登录一次看遮罩是否仍然不透明。

### 第 4 刀｜搬 4 个安全 tab（1/2/5/6）

- **动**：`src/tabs/{today,holdings,backtest,strategy}.py`，签名 `render(paths, decision)`。
- **行数**：app.py ≈1145 → **≈680**（−465）
- **风险**：低。这 4 个 tab 只读不写，不碰 session_state。
- **回归**：手点 4 个 tab，对比关键数字。

### 第 5 刀｜搬记账链（tab3 + tab4）

- **动**：`src/tabs/records.py`，含 `pending_tx` / `pending_obs` 两个 session key 的完整二次确认流程。
- **行数**：app.py ≈680 → **≈555**（−125）
- **风险**：**中高**。这是唯一**写数据**的路径。二次确认状态机跨 rerun，搬错了可能重复落账或丢账。
- **回归**：必须做**真实写入测试**——记一笔、确认库里只有一条、删掉。不能只跑探针。

### 第 6 刀｜搬认证 + 侧栏，app.py 收口

- **动**：`src/ui/auth.py`（含 `st.stop()` 的门闸语义要保留在 app.py 顶层：`if not auth.gate(paths): st.stop()`）、`src/ui/sidebar.py`（返回 `Decision`）。app.py 收口到 ≈120 行。
- **行数**：app.py ≈555 → **≈120**（−435）
- **风险**：**最高**。两段式时序 + `st.stop()` 位置 + 8 个 session key。
- **回归**：5 条认证路径全跑（登录成功 / 错 PIN / 无账号 / 待激活设 PIN / 首次自举）。记忆里记着有 AppTest 回归脚本的做法（monkeypatch 掉 Sheets 网络层），这一刀应该把它捡回来用。

---

## 5. 顺路发现的 4 个业务问题（需要你拍板是否纳入）

这些不是重构，是重构时绕不开的决定。**默认全部不做**，除非你点头。

| # | 问题 | 位置 | 影响 | 建议 |
|---|---|---|---|---|
| ① | **模型跑两次**：`run_model(None)` 后若用户填了金额，整体重跑子进程 | 771–781 / 913–918 | 每次调金额多一次进程启动 + 可能的行情请求，浪费时间和外部配额 | 拆分后在 `model.py` 里让引擎复用同一份行情快照重算金额。**建议单独一个 commit，不混进重构** |
| ② | 死代码 `append_csv`（定义后从未调用）、`OBS_CSV`（定义后从未使用） | 583 / 40 | 无功能影响，误导读代码的人（看着像还在用本地 CSV 写入） | 第 2 刀顺手删 |
| ③ | 文案指向不存在的目录：`"完整回测数据文件见 backtest-dca-5y/"` | 1908 | `backtest/` 下没有这个目录，用户照着找不到 | 改成 `backtest/` 实际文件名 |
| ④ | 文案已过期：`"都在本地 skill 目录"` | 1983 | 项目 2026-08-17 已迁出 `~/.claude/skills/`，且线上跑在 Streamlit Cloud + Google Sheets，"不上传任何地方"这句对云端用户是错的 | 改成实际口径 |

③④ 是一行字的事，建议随第 1 刀（tab5）和第 4 刀（tab6）各自捎带。

---

## 6. 每刀之后的回归门禁

三级，前两级已经在迁移验证时建好了手法：

| 级 | 内容 | 判定 |
|---|---|---|
| **L1 引擎** | `python scripts/dca_calculator.py --base-dir .` | exit 0，输出含 15 个顶层键，`wide_table_markdown` 非空 |
| **L2 协议探针** | 起 Streamlit → websocket 连 `/_stcore/stream` → 发 `BackMsg(rerun_script)` | `ForwardMsg.script_finished == 0`，异常帧数 = 0 |
| **L3 人工** | 登录 → 点 6 个 tab | 无报错、关键数字与改前一致 |

额外的、按刀追加的：

- 第 1 刀：5 张表导出 CSV 与改前做 diff
- 第 3 刀：遮罩视觉级确认（截图 / computedStyle 背景不透明度）
- 第 5 刀：真实写入 1 笔并核对库里只有 1 条
- 第 6 刀：5 条认证路径全跑（建议捡回 AppTest 脚本）

**注意 L2 的一个已知坑**：Streamlit 1.61 在 websocket 连接时**不会**自动跑脚本，必须显式发 `BackMsg(rerun_script)`，且握手要带 `subprotocols=["streamlit"]`。第一次探测拿到 0 帧不是应用挂了。

---

## 7. 明确不做的事

- **不改 `scripts/dca_calculator.py`** —— 计算引擎已经是干净边界，subprocess 隔离有效
- **不改 `storage.py`** —— 18 个接口已经是干净边界
- **不改认证的两段式时序** —— 只搬位置，不改逻辑
- **不引入新依赖** —— 不上 pydantic、不上 DI 框架
- **不做 UI 改版** —— tab 数量、名称、布局一律不动
- **不动 `data/market_history/`** —— 增量缓存是入库的，删任何一个 csv 会触发全量重建

---

## 8. 提交计划

```
refactor(tab5): 415 行硬编码回测数据外置到 rolling_tables.json     # 第 1 刀
refactor(services): 建 AppContext，服务层独立成模块，清死代码       # 第 2 刀
refactor(ui): CSS 与遮罩组件独立成模块                            # 第 3 刀
refactor(tabs): 今日/持仓/回测/策略四个 tab 独立成模块              # 第 4 刀
refactor(tabs): 记账链（tab3 写 + tab4 读）独立成模块              # 第 5 刀
refactor(app): 认证与侧栏独立成模块，app.py 收口到 120 行           # 第 6 刀
```

**如果只做一刀**：做第 1 刀。20% 行数、最低风险、立刻验证回归门禁的有效性。
**如果做到一半要停**：停在第 4 刀之后。那时 app.py ≈680 行，认证和记账都还在原位没动过，风险最高的两块完全没碰。

---

## 9. 待确认清单

动手前需要你确认的 4 件事：

1. **6 刀的顺序认不认？** 特别是"认证放最后"这个反直觉的决定。
2. **§5 的 4 个业务问题，哪些纳入本次？** 建议：②③④ 纳入（都是清理），① 单独做。
3. **tab4 并入 `records.py`** 还是坚持独立文件？
4. **一次做完还是一刀一停？** 建议一刀一停，每刀我报回归结果你再放行下一刀。
