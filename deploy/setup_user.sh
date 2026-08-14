#!/bin/bash
# ---- 模拟定投决策台 · 新用户部署脚本 ----
#
# 用法:  ./deploy/setup_user.sh <用户名> [月度预算]
# 示例:  ./deploy/setup_user.sh zhangsan 30000
#        ./deploy/setup_user.sh lisi 45000
#
# 执行内容：
#   1. 创建用户数据目录 data/<用户名>/data/
#   2. 复制默认配置 config.json
#   3. 初始化空的交易记录文件
#   4. 更新 docker-compose.yml（追加用户服务）
#   5. 更新 nginx.conf（追加路由）
#   6. 重启 docker compose
#
# 前提：已运行过 docker compose build（首次需要构建镜像）

set -euo pipefail

USER_ID="${1:?用法: $0 <用户名> [月度预算]}"
BUDGET="${2:-30000}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DATA_ROOT="$PROJECT_DIR/data"

# ---- 端口分配：自动找下一个可用端口 ----
NEXT_PORT=$(grep -oP 'dca-\w+' "$PROJECT_DIR/deploy/docker-compose.yml" 2>/dev/null | wc -l)
NEXT_PORT=$((NEXT_PORT + 1))
SVC_NAME="user${NEXT_PORT}"

echo "📋 新增用户: $USER_ID"
echo "   服务名:   $SVC_NAME"
echo "   数据目录: $DATA_ROOT/$USER_ID/data/"
echo "   月度预算: ¥$BUDGET"
echo ""

# ---- 1. 创建数据目录 ----
USER_DATA="$DATA_ROOT/$USER_ID/data"
mkdir -p "$USER_DATA/market_history"

# ---- 2. 复制默认 config.json ----
cp "$PROJECT_DIR/data/config.json" "$USER_DATA/config.json"

# ---- 3. 初始化空交易记录 ----
echo "date,action,asset,symbol,currency,amount_rmb,price,shares,fee_rmb,fx_rate,notes" > "$USER_DATA/transactions.csv"
echo "date,action,total_suggested_rmb,user_amount_rmb,decision_level,sp500_weight,ndx100_weight,gold_weight,reason,notes" > "$USER_DATA/observations.csv"

# ---- 4. 追加 docker-compose 服务 ----
cat >> "$PROJECT_DIR/deploy/docker-compose.yml" << EOF

  $SVC_NAME:
    build:
      context: ..
      dockerfile: deploy/Dockerfile
    container_name: dca-$USER_ID
    command: ["--base-dir", "/app/user-data"]
    volumes:
      - ../data/$USER_ID:/app/user-data
    expose:
      - "8501"
    restart: unless-stopped
    mem_limit: 512m
EOF

echo "✅ docker-compose.yml 已追加 $SVC_NAME"

# ---- 5. 追加 nginx upstream + location ----
NGINX_CONF="$PROJECT_DIR/deploy/nginx.conf"

# 在最后一个 } 之前插入 upstream
sed -i "/^server {/i upstream $SVC_NAME {\n    server $SVC_NAME:8501;\n}\n" "$NGINX_CONF"

# 在 server 块最后一个 } 之前插入 location
cat >> /tmp/nginx_location_$USER_ID.txt << EOF

    location /$USER_ID/ {
        proxy_pass http://$SVC_NAME/;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
    }
EOF

# 在 server 块最后的 } 前插入
sed -i -e "/^}$/r /tmp/nginx_location_$USER_ID.txt" -e "/^}$/N" "$NGINX_CONF"
rm -f /tmp/nginx_location_$USER_ID.txt

echo "✅ nginx.conf 已追加 /$USER_ID/ 路由"

# ---- 6. 重启服务 ----
cd "$PROJECT_DIR"
docker compose up -d --build $SVC_NAME nginx

echo ""
echo "🎉 用户 $USER_ID 部署完成！"
echo "   访问地址: http://<你的服务器IP>/$USER_ID/"
echo "   数据目录: $DATA_ROOT/$USER_ID/"
echo ""
echo "⚠️  请提醒用户：本工具仅为模拟测算，不构成投资建议。"
