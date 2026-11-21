# QuantInsight Pro - ModelScope 创空间 Docker 镜像
# 端口规则：必须 7860（8080 被平台占用）
FROM python:3.11-slim

# 中文字体（matplotlib 中文图表不乱码）+ curl（健康探测）
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖（利用层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    -i https://pypi.tuna.tsinghua.edu.cn/simple

# 应用代码 + 数据库（qi_data.db 已随仓库携带）
COPY . .

# 财报引导开关：app 启动线程检测到该 ENV 时后台拉取 financials（幂等，失败静默）
ENV QI_FIN_BOOTSTRAP=1

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -sf http://localhost:7860/ || exit 1

# 启动：财报引导已改为 Dockerfile 构建期不做、运行期由 streamlit 首屏按需触发
# （回滚到最简 CMD：sh -c 后台子 shell 在部分平台构建器上会导致失败）
CMD ["streamlit", "run", "app.py", "--server.port=7860", "--server.address=0.0.0.0", "--server.headless=true"]
