# ---- 模拟定投决策台 · 生产镜像 ----
# 构建: docker build -t dca-sim .
# 运行: docker run -p 8501:8501 -v /data/user1:/app/user-data dca-sim --base-dir /app/user-data

FROM python:3.12-slim

# 系统依赖（curl 用于东财行情抓取）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖（利用 Docker 缓存层）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码（不含 data/，data 通过 volume 挂载）
COPY app.py .
COPY scripts/ scripts/
COPY .streamlit/ .streamlit/

# Streamlit 生产配置
COPY deploy/streamlit-config.toml .streamlit/config.toml

EXPOSE 8501

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

# 入口：支持 --base-dir 传入用户数据目录
ENTRYPOINT ["streamlit", "run", "app.py", "--server.headless", "true", "--server.port", "8501", "--server.address", "0.0.0.0", "--browser.gatherUsageStats", "false", "--"]
