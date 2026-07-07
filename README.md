# 跨网络日志实时推送系统
实时监控 PC 端日志文件，通过 NAS 中继服务跨网络推送到移动端浏览器显示。
```
PC推送端 (内网) --WS--> NAS中继 (Docker + CF Tunnel) --WSS--> 移动端浏览器
```
## 功能特性
- **多文件夹监控**：同时监控多个日志目录，支持自定义备注名
- **文件名格式匹配**：按 `strftime` 格式（如 `%Y%m%d`）自动识别日志文件
- **自动切换轮转日志**：新日志文件生成时自动无缝切换
- **双重检测防漏检**：watchdog 实时监听 + 15秒轮询兜底
- **跨网络推送**：通过 Cloudflare Tunnel 实现公网访问，零服务器成本
- **移动端 PWA**：浏览器打开即可使用，支持添加到主屏幕
- **Web 端分类浏览**：按来源自动分标签页显示
- **配置持久化**：自动保存设置，重启后恢复
- **系统托盘**：连接成功后自动后台运行，双击恢复窗口
## 项目结构
```
.
├── docker-compose
   ├── Dockerfile                # NAS 中继 Docker 镜像定义
   ├── docker-compose.yml        # DPanel / Docker Compose 部署配置
   ├── nas_relay_server.py       # NAS WebSocket 中继服务（集成 Web 前端）
├── pc_sender.py              # PC 端桌面推送软件（tkinter GUI）
├── README.md                 # 本文档
```
## 组件说明
| 组件 | 文件 | 运行环境 | 说明 |
|------|------|---------|------|
| NAS 中继 | `nas_relay_server.py` | Docker（飞牛OS fnOS） | aiohttp 同时提供 HTTP 页面 + WebSocket 中继 |
| PC 推送 | `pc_sender.py` | Windows/Mac/Linux | tkinter GUI，多文件夹监控，系统托盘 |
| 移动端 | 内置于 NAS 中继 | 手机浏览器 | 按来源分标签页，自动重连 |
## 技术栈
| 模块 | 技术 |
|------|------|
| NAS 中继 | Python 3.11 + aiohttp |
| PC 端 | Python 3.11 + tkinter + watchdog + websockets + pystray |
| 移动端 | HTML5 + CSS3 + JavaScript |
| 部署 | Docker + Docker Compose |
| 公网穿透 | Cloudflare Tunnel |
## 快速开始
### 一、NAS 端部署（飞牛OS fnOS）
#### 1. 准备文件
在 NAS 上创建项目目录（如 `/数据卷/docker/log-relay/`），放入：
- `Dockerfile`
- `docker-compose.yml`
- `nas_relay_server.py`
#### 2. DPanel Compose 部署
1. 打开 DPanel → Compose → 新建项目
2. 项目目录选择上述文件夹
3. 启动项目，映射端口 `8765`
或命令行部署：
```bash
cd /数据卷/docker/log-relay/
docker compose up -d --build
```
#### 3. Cloudflare Tunnel 配置
1. 登录 [Cloudflare Zero Trust](https://one.dash.cloudflare.com/)
2. 进入 **Networks > Tunnels**，选择你的 Tunnel
3. 添加 **Public Hostname**：
   - Subdomain: `logrelay`（自定义）
   - Domain: 你的域名
   - Type: HTTP
   - URL: `http://NAS内网IP:8765`
4. 保存后移动端访问：`https://logrelay.yourdomain.com`
### 二、PC 端部署
#### 1. 安装 Python 依赖
```bash
pip install websockets watchdog
# 可选：系统托盘功能
pip install pystray Pillow
```
#### 2. 运行
```bash
python pc_sender.py
```
#### 3. 构建 EXE（可选）
```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "LogRelay-PC" pc_sender.py
```
### 三、移动端使用
1. 手机浏览器打开 `https://logrelay.yourdomain.com`
2. 页面自动检测地址，点击 **连接**
3. 日志按来源自动分标签页显示
## PC 端使用说明
### 添加监控文件夹
1. 点击 **+ 添加**，配置：
   - **文件夹路径**：日志所在目录
   - **显示名称**：自定义备注（如"应用A"）
   - **文件名格式**：如 `app%Y%m%d`（支持 strftime 占位符）
   - **文件后缀**：如 `.log, .txt`
2. 系统自动识别最新匹配文件并开始监控
### 显示模式
- **合并**：所有日志统一显示在"全部"标签
- **分别**：每个文件夹独立标签页
### 设置
点击顶部 **⚙ 设置**：
- 启动时自动连接 NAS
- 连接成功后最小化到系统托盘
## 常见问题
**Q: 移动端 502 错误？**
- 确认 CF Tunnel 指向 NAS 内网 IP（不是 localhost）
- 确认 NAS 防火墙放行 8765 端口
- 查看容器日志确认服务已启动
**Q: PC 端连不上 NAS？**
- 确认 PC 和 NAS 在同一局域网
- 确认 NAS Docker 端口映射正确
- 尝试用 NAS 内网 IP 而非 localhost
**Q: 日志文件编码乱码？**
- PC 端默认 UTF-8，如遇乱码检查日志文件实际编码
**Q: 托盘双击不恢复窗口？**
- 确认已安装 `pystray` 和 `Pillow`
- Windows 上双击托盘图标会触发默认菜单项（显示窗口）
## 协议
- PC → NAS: WebSocket (`ws://`)
- 移动端 → NAS: WebSocket Secure (`wss://`，通过 Cloudflare Tunnel)
- 消息格式: JSON
  ```json
  {"type": "log", "content": "日志内容", "source": "来源名", "timestamp": "2026-07-07T10:00:00"}
  ```
## License
MIT
