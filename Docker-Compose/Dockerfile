# NAS WebSocket 中继服务 - Docker镜像
# 使用 aiohttp 同时提供 HTTP 页面和 WebSocket 中继

FROM python:3.11-slim

WORKDIR /app

# 安装依赖
RUN pip install --no-cache-dir aiohttp

# 复制服务代码
COPY nas_relay_server.py /app/

# 暴露端口
EXPOSE 8765

# 启动服务
CMD ["python", "nas_relay_server.py"]
