# 📈 模拟定投决策台 · 部署指南

> 从零到上线的完整步骤。适用于 Oracle Cloud 永久免费实例、任何 Linux VPS、或本地电脑 + Cloudflare Tunnel。

---

## 目录

1. [服务器准备](#1-服务器准备)
2. [安装 Docker](#2-安装-docker)
3. [上传代码](#3-上传代码)
4. [首次构建](#4-首次构建)
5. [添加用户](#5-添加用户)
6. [域名与 HTTPS（可选）](#6-域名与-https可选)
7. [日常运维](#7-日常运维)
8. [0 费用方案详解](#8-0-费用方案详解)

---

## 1. 服务器准备

### Oracle Cloud 永久免费（推荐）

注册地址：https://cloud.oracle.com/signup

免费套餐规格：
- **4 核 ARM CPU**（Ampere A1）
- **24 GB 内存**
- **200 GB 存储**
- **每月 10 TB 出站流量**
- **永久免费**，不会过期

选择镜像时选 **Ubuntu 22.04 (aarch64)**。

> ⚠️ 注册需要信用卡验证（不扣费），选 Always Free 资源。

### 其他免费方案

| 平台 | 免费额度 | 有效期 | 备注 |
|---|---|---|---|
| Google Cloud | $300 赠金 | 12 个月 | e2-micro 实例 |
| AWS | t2.micro | 12 个月 | 到期后收费 |
| Cloudflare Tunnel | 无限流量 | 永久 | 需要一台能开机的电脑 |

---

## 2. 安装 Docker

SSH 登录服务器后执行：

```bash
# 安装 Docker + Docker Compose
curl -fsSL https://get.docker.com | sh

# 验证
docker --version
docker compose version

# 把自己加入 docker 组（免 sudo）
sudo usermod -aG docker $USER
# 重新登录 SSH 使组生效
```

---

## 3. 上传代码

```bash
# 在服务器上创建项目目录
mkdir -p ~/dca-sim && cd ~/dca-sim

# 从你的电脑上传（在你本地电脑执行）
scp -r /c/Users/xiezhibo/.claude/skills/sp500-nasdaq100-gold-dca/*  用户名@服务器IP:~/dca-sim/

# 或者用 Git（如果你把代码推到了 GitHub）
git clone https://github.com/你的用户名/dca-sim.git ~/dca-sim
cd ~/dca-sim
```

项目目录结构：

```
~/dca-sim/
├── app.py                  # Streamlit 应用
├── requirements.txt        # Python 依赖
├── scripts/
│   └── dca_calculator.py   # 策略引擎
├── data/
│   ├── config.json         # 默认配置
│   └── user1/              # 用户数据（部署后生成）
└── deploy/
    ├── Dockerfile          # 容器镜像
    ├── docker-compose.yml  # 多用户编排
    ├── nginx.conf          # 反向代理
    ├── setup_user.sh       # 新用户脚本
    └── streamlit-config.toml
```

---

## 4. 首次构建

```bash
cd ~/dca-sim

# 首次需要初始化一个默认用户（你自己）
mkdir -p data/me/data/market_history
cp data/config.json data/me/data/config.json
echo "date,action,asset,symbol,currency,amount_rmb,price,shares,fee_rmb,fx_rate,notes" > data/me/data/transactions.csv
echo "date,action,total_suggested_rmb,user_amount_rmb,decision_level,sp500_weight,ndx100_weight,gold_weight,reason,notes" > data/me/data/observations.csv

# 构建镜像（首次约 2-3 分钟）
docker compose -f deploy/docker-compose.yml build

# 启动
docker compose -f deploy/docker-compose.yml up -d

# 检查状态
docker compose -f deploy/docker-compose.yml ps
```

访问 `http://服务器IP/user1/` 即可看到界面。

---

## 5. 添加用户

### 方法 A：自动脚本（推荐）

```bash
cd ~/dca-sim
chmod +x deploy/setup_user.sh

# 添加用户 zhangsan，月预算 30000
./deploy/setup_user.sh zhangsan 30000

# 添加用户 lisi，月预算 45000
./deploy/setup_user.sh lisi 45000
```

脚本会自动：创建数据目录 → 复制配置 → 更新 docker-compose → 更新 nginx → 重启服务。

用户访问地址：
- zhangsan → `http://服务器IP/zhangsan/`
- lisi → `http://服务器IP/lisi/`

### 方法 B：手动添加

1. 复制 `data/user1/` 目录为 `data/新用户名/`
2. 编辑 `deploy/docker-compose.yml`，复制 user1 服务块并改名
3. 编辑 `deploy/nginx.conf`，复制 location 块并改路径
4. `docker compose -f deploy/docker-compose.yml up -d --build`

### 删除用户

```bash
# 停止容器
docker compose -f deploy/docker-compose.yml stop <服务名>

# 删除容器
docker compose -f deploy/docker-compose.yml rm -f <服务名>

# 手动从 docker-compose.yml 和 nginx.conf 中删除对应块
# 备份用户数据后删除目录
mv data/用户名 data/_archived_用户名
```

---

## 6. 域名与 HTTPS（可选）

### 有域名

```bash
# 1. 在域名 DNS 添加 A 记录指向服务器 IP
# 2. 安装 certbot
sudo apt install certbot python3-certbot-nginx

# 3. 修改 nginx.conf 中 server_name 为你的域名
# 4. 申请证书
sudo certbot --nginx -d dca.yourdomain.com

# 5. 自动续期（certbot 已配置 cron）
sudo certbot renew --dry-run
```

### 无域名（Cloudflare Tunnel）

```bash
# 1. 注册 Cloudflare 账号（免费）
# 2. 在服务器上安装 cloudflared
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64 -o cloudflared
chmod +x cloudflared
sudo mv cloudflared /usr/local/bin/

# 3. 登录
cloudflared tunnel login

# 4. 创建隧道
cloudflared tunnel create dca-sim

# 5. 配置隧道
cat > ~/.cloudflared/config.yml << EOF
tunnel: dca-sim
ingress:
  - hostname: dca-sim.your-subdomain.cfargotunnel.com
    service: http://localhost:80
  - service: http_status:404
EOF

# 6. 启动
cloudflared tunnel run dca-sim
```

### 本机临时外发（ngrok 固定域名）

`deploy/start-dca-tunnel.bat` —— 本机开着 Streamlit（8501）时，双击即把它发到公网固定地址
`https://sudoku-manhood-argue.ngrok-free.dev`。脚本会先查 ngrok 是否已在跑，避免重复启动。

`ngrok.exe` 放在 `deploy/bin/`（33 MB，**随项目走但不入库**，见 .gitignore）。
脚本用 `%~dp0bin\ngrok.exe` 相对定位，PATH 作兜底——**全程无绝对路径**，项目搬到哪都能跑。
换机器只需去 https://ngrok.com/download 重新下一个丢进 `deploy/bin/`。

**唯一留在 C 盘的东西：** ngrok 的 authtoken 在 `%LOCALAPPDATA%\ngrok\ngrok.yml`，
那是 ngrok 自己写死的配置位置，搬不走。换机器要重跑一次 `ngrok config add-authtoken <token>`。

**改这个 bat 时注意：只能用 ASCII。** cmd.exe 按 OEM 码页（中文 Windows 是 936）读批处理文件，
UTF-8 中文注释会被误读，cmd 可能把乱码碎片当命令去执行——这个坑只在双击运行时才现形，
在编辑器里看不出来。中文说明一律写在本文档里，不要写进 bat。

这条路子只适合临时给人看，机器一关就断；长期在线走上面的 Cloudflare Tunnel 或 Streamlit Community Cloud。
（`cloudflared.exe` 本机没配过，已收到 `X:\coding\tools\bin\` 作通用工具备用。）

---

## 7. 日常运维

### 查看日志

```bash
# 所有服务
docker compose -f deploy/docker-compose.yml logs --tail 50

# 单个用户
docker logs dca-zhangsan --tail 20
```

### 更新代码

```bash
cd ~/dca-sim
# 上传新代码或 git pull
docker compose -f deploy/docker-compose.yml up -d --build
```

### 备份

```bash
# 备份所有用户数据
tar -czf backup_$(date +%Y%m%d).tar.gz data/

# 恢复
tar -xzf backup_20260812.tar.gz
```

### 资源监控

```bash
# 查看各容器内存占用
docker stats --no-stream

# Oracle 免费实例 24GB 内存，每个用户约 200-300MB
# 理论可支撑 50-80 个并发用户
```

### 防火墙

```bash
# Oracle Cloud 需要开放端口
# 1. 在 Oracle 控制台 → 网络 → 安全列表 → 添加入站规则 TCP 80, 443
# 2. 服务器本机
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

---

## 8. 0 费用方案详解

### 方案 A：Oracle Cloud 永久免费（⭐推荐）

**适合**：正式运营，需要稳定公网访问

| 项目 | 费用 |
|---|---|
| Oracle Cloud ARM 实例 | ¥0（永久） |
| 域名（可选） | ¥0~70/年 |
| HTTPS（Let's Encrypt） | ¥0 |
| **合计** | **¥0~70/年** |

**注册步骤**：
1. 访问 https://cloud.oracle.com/signup
2. 选择 Home Region（建议选日本/韩国/新加坡，国内访问较快）
3. 创建 Always Free 的 VM.Standard.A1.Flex 实例（4核24G）
4. 开放安全列表 80/443 端口
5. SSH 登录后按本文第 2 步开始部署

### 方案 B：家里电脑 + Cloudflare Tunnel

**适合**：不想注册云、有闲置电脑/树莓派

| 项目 | 费用 |
|---|---|
| 家里电脑（已有） | ¥0 |
| Cloudflare Tunnel | ¥0（永久） |
| 电费 | 忽略不计 |
| **合计** | **¥0** |

**原理**：Cloudflare Tunnel 从你家电脑反向连接到 Cloudflare 全球 CDN，用户通过 Cloudflare 的 URL 访问，不需要公网 IP、不需要端口映射。

**步骤**：
1. 在家里电脑安装 Docker + 启动服务
2. 安装 cloudflared
3. 创建隧道 → 得到一个 `xxx.cfargotunnel.com` 地址
4. 把这个地址发给用户

### 方案 C：Google Cloud 赠金

**适合**：临时测试，12 个月有效

1. 注册 Google Cloud，获得 $300 赠金
2. 创建 e2-medium 实例（2核4G）
3. 12 个月内免费使用

---

## 费用与容量估算

| 用户数 | Oracle 免费实例 | 每月成本 |
|---|---|---|
| 1-10 | 绰绰有余 | ¥0 |
| 10-30 | 轻松运行 | ¥0 |
| 30-80 | 接近上限（内存） | ¥0 |
| 80+ | 需要升级或加机器 | 另算 |

按 ¥9.9/月定价，**3 个付费用户**就能覆盖一年的域名费用，其余全是利润。

---

## 常见问题

**Q: 用户数据会丢吗？**
A: 不会。数据在 `data/用户名/` 目录，容器重启不丢数据。建议每周备份一次。

**Q: 用户能互相看到数据吗？**
A: 不能。每个用户有独立的容器和数据目录，完全隔离。

**Q: 用户自己能看到别人吗？**
A: 用户只能访问自己的 URL 路径（如 `/zhangsan/`），nginx 只转发到自己的容器。

**Q: 东财行情抓不到怎么办？**
A: 代码有 3 次重试 + 缓存兜底 + GC=F 期货估算三级 fallback。服务器上装好 curl 即可。

**Q: 可以放在国内服务器吗？**
A: 可以，但域名需要备案。Oracle Cloud 海外节点不需要备案。
