# 部署与外发指南

> 【活文档 · 更新时机：部署方式变动同期】索引见 [docs/README.md](../docs/README.md)。
> 2026-08-17 重写。**只写真实在用的路径。**
> 原先那套 Oracle Cloud + Docker + nginx 多容器方案已删除（原因见文末「为什么删掉 Docker 那套」）。

---

## 1. 三条路径，现状

| 路径 | 状态 | 用途 |
|---|---|---|
| **Streamlit Community Cloud** | ✅ **生产环境，唯一在线路径** | https://dca365.streamlit.app/ |
| **本机直跑** | ✅ 开发调试 | `.venv/Scripts/streamlit run app.py` → localhost:8501 |
| **ngrok 固定域名** | ✅ 临时给人看 | 本机开着时把 8501 发到公网 |

---

## 2. Streamlit Community Cloud（生产）

**部署机制：推 `main` 分支即自动重新部署。** 没有额外的构建步骤、没有 Dockerfile 参与。

```bash
git push origin main
# 平台检测到新提交 → 自动拉取 → 装 requirements.txt → 重启应用
```

### 需要在平台后台配置的东西

| 配置项 | 位置 | 说明 |
|---|---|---|
| **GCP 服务账号凭据** | share.streamlit.io → 应用 ⋮ → Settings → Secrets | 内容同本机 `.streamlit/secrets.toml`。**凭据不在 git 里**，换机器/重建应用时要手动贴进去 |
| **自定义子域** | Settings → General → App URL | 已设为 `dca365`（2026-08-14 生效） |
| **公开访问** | Settings → Sharing → public | 必须 public。默认私有会要求访问者先登录有权限的 Streamlit 账号，家人打不开 |

> ⚠️ **应用已 public，意味着应用内的「名字 + PIN」门闸是唯一防线。**
> 门闸为 fail-closed：secrets 缺失/损坏即拒绝服务；PIN 是 PBKDF2 加盐哈希 + 连续失败锁定。

### 已知的平台侧限制

- **时区是 UTC**，用户在 UTC+8。业务「今天」由 `biz_today()`（Asia/Shanghai 固定 UTC+8）统一定义，与容器时区解耦——写新日期逻辑不许裸用 `date.today()`（`src/dates.py` 与引擎 `dca_calculator.py` 双实现同规则，必须同改）。
- **单进程服务所有用户**，所以 `st.cache_data` 是全用户共用的；缓存键内含用户名，跨用户不串号。
- 平台没有日志留存、没有告警。线上炸了只能等人告诉你。

---

## 3. 本机开发

```bash
cd X:/coding/projects/sp500-nasdaq100-gold-dca
.venv/Scripts/streamlit run app.py

# 或双击项目根目录的 start-app.bat
```

**认证模式（fail-closed）**：默认要求云端模式——没配 secrets 会**拒绝启动**并提示原因。本机已配 `.streamlit/secrets.toml` 则无感（照常登录）。要在**无凭据的机器**上跑单机版，必须显式设置：

```bash
# Git Bash:
DCA_AUTH_MODE=local .venv/Scripts/streamlit run app.py
:: cmd:
:: set DCA_AUTH_MODE=local && .venv\Scripts\streamlit run app.py
```

单机模式不登录、数据只存本机 CSV，页面顶部有黄色警示条。

单跑计算引擎（不起网页）：

```bash
.venv/Scripts/python.exe scripts/dca_calculator.py --base-dir .
```

**本机环境实况（2026-08-17 实测）**：Python 3.14.4 / streamlit 1.61.1 / pandas 3.0.5 / numpy 2.5.2 / yfinance 1.6.0 / gspread 5.12.4。
`requirements.txt` 里全是无上界的 `>=`（如 `pandas>=2.0.0`），**与实装版本差很远，等于没有可复现性**。

---

## 4. ngrok 临时外发

`deploy/start-dca-tunnel.bat` —— 本机开着 Streamlit（8501）时，双击即把它发到公网固定地址
`https://sudoku-manhood-argue.ngrok-free.dev`。脚本会先查 ngrok 是否已在跑，避免重复启动。

`ngrok.exe` 放在 `deploy/bin/`（33 MB，**随项目走但不入库**，见 .gitignore）。
脚本用 `%~dp0bin\ngrok.exe` 相对定位，PATH 作兜底——**全程无绝对路径**，项目搬到哪都能跑。
换机器只需去 https://ngrok.com/download 重新下一个丢进 `deploy/bin/`。

> ⚠️ **`deploy/bin/ngrok.exe` 不在 git 里，删掉无法从版本库恢复**，只能重新下载。

**唯一留在 C 盘的东西：** ngrok 的 authtoken 在 `%LOCALAPPDATA%\ngrok\ngrok.yml`，
那是 ngrok 自己写死的配置位置，搬不走。换机器要重跑一次 `ngrok config add-authtoken <token>`。

### 改这个 bat 时注意：只能用 ASCII

cmd.exe 按 OEM 码页（中文 Windows 是 936）读批处理文件，UTF-8 中文注释会被误读，
cmd 可能把乱码碎片当命令去执行——**这个坑只在双击运行时才现形，在编辑器里看不出来**。
中文说明一律写在本文档里，不要写进 bat。

### 这条路子的边界

机器一关就断，只适合临时给人看。**这个脚本会把 8501 端口发到公网**——运行前确认你真的想让外面的人访问。

---

## 5. 为什么删掉 Docker 那套

2026-08-17 删除了 `deploy/Dockerfile`、`docker-compose.yml`、`nginx.conf`、`setup_user.sh`、`streamlit-config.toml`。

**理由：它从来没有成功跑过一次。** 铁证是这几个文件自 `574c7a7`（2026-08-12 初始提交）起**一行未改** —— 真跑过的东西不可能零迭代。而它当时被 CLAUDE.md 标为「部署指南（唯一事实源）」，是最危险的一种技术债：**看起来是备用方案，实际是三道死墙**。

照原文档走会连撞：

| 墙 | 具体问题 |
|---|---|
| 镜像里没有 `storage.py` | Dockerfile 只 COPY `app.py` / `scripts/` / `.streamlit/`，而 `app.py:22` 就 `import storage` → 启动即 `ModuleNotFoundError`。配上 `restart: unless-stopped` = 无限崩溃重启 |
| build context 错位 | compose 写 `context: .` + `dockerfile: deploy/Dockerfile`，而文档全程用 `-f deploy/docker-compose.yml` → 解析成 `deploy/deploy/Dockerfile`，构建立刻失败 |
| 挂载路径对不上 | 文档教你建 `data/me/`，compose 挂的是 `./data/user1` → 容器里读不到 config.json，`SystemExit` |

**删除那套文件同时消掉的真实风险：**

- **GCP 私钥不再会被烤进镜像层。** `.dockerignore` 没排除 `.streamlit/`，而 `Dockerfile:21` COPY 整个目录。镜像层是只读堆叠的，后面 `rm` 掉前面那层还在，**「打包进去再删」是无效的**。没有 Dockerfile = 没有这条外泄路径。
- **`setup_user.sh` 不会再炸配置。** 它用 `sed "/^}$/r ..."` 插 nginx location 块，而文件里第一个独占一行的 `}` 是 **upstream 块的收尾**，不是 server 块的 → location 被插到 server 块外面 → nginx 报 `location directive is not allowed here`，**完全起不来**。加一个用户 = 所有用户下线。
- **两套互相抵消的多用户实现收敛成一套。** 原先「一用户一容器一目录」和「应用内登录 + Sheets 行隔离」并存，但所有容器 COPY 的是同一份 service account、指向同一个表格 —— 容器隔离是纯废重量，且 nginx 路径与 Sheets 用户名毫无绑定，任何人从任意 `/xxx/` 路径都能登任何账号。现在只剩应用内登录这一套。
- **原文档里那句不实陈述一并删掉了**：「用户能互相看到数据吗？不能，每个用户有独立的容器和数据目录，完全隔离。」—— 真实情况是所有用户共用一个 `data/transactions.csv` 和一块进程级缓存。

### 将来真要用 Docker 的话

**取回旧文件：**

```bash
git show 574c7a7:deploy/Dockerfile          # 看
git checkout 574c7a7 -- deploy/Dockerfile   # 取回
```

**但建议重写而不是取回**，因为那份有上面三道墙。重写时的必守清单：

1. **COPY 清单必须含 `storage.py`**（当年就漏了这个）
2. **`.streamlit/secrets.toml` 绝不进镜像层**。`.dockerignore` 已经保留并把它列在第一条了 —— 凭据走运行时挂载或环境变量注入
3. **设 `TZ=Asia/Shanghai`**（当年 Dockerfile 和 compose 都没设时区，容器内 `date.today()` 会落在 UTC）
4. **`FROM` 钉住 digest**，不要用 `python:3.12-slim` 这种浮动 tag。且注意本机已经是 **Python 3.14.4**，pandas 3.0.5 —— 镜像里装 3.12 会得到完全不同的依赖解析
5. **COPY 要带上 `strategy/`、`backtest/`、`data/market_history/`**，否则 Tab5 是空的、每个用户从冷缓存起步（`.dockerignore` 现在还排除着 `backtest*/` 和 `*.md`，重启 Docker 时要一并复核）
6. **每用户独立 `--base-dir`**。这个机制在代码里已经就绪（`app.py` 和 `dca_calculator.py` 都支持 `--base-dir`），与 Docker 无关，不需要容器也能用
7. **`nginx -t` 必须在 reload 之前跑**，且改配置前先备份

---

## 6. 备份现状（⚠️ 缺口）

**线上真正的数据在 Google Sheets，项目目前没有自己可控、自动执行且验证过可恢复的备份。** Google Sheets 的平台版本历史可能是最后一道救命绳，但它不等于本项目已经建立了备份制度。

`storage.py` 以 Sheets 为「唯一事实源」，本地侧已有的保护：

- 本地 `data/*.localbak` 是带时间戳的轮转留底（滚动保留最近 10 份），`sheets` 写前会先快照 `<表名>_bak` 工作表，快照失败则放弃写入
- Sheets 读取故障会抛错拒写，不会把空表当成"没有数据"覆写上去

**仍缺的是项目自己可控的备份制度**：没有自动导出、命名快照、保留周期和恢复演练，只能被动依赖 Google 平台可能提供的版本历史。这是当前最急的缺口之一，完整清单与验证标准见 [docs/BUGLIST.md](../docs/BUGLIST.md) 备份相关条目。
