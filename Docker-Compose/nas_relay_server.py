#!/usr/bin/env python3
"""
NAS WebSocket 中继服务（集成Web前端）
使用 aiohttp 同时提供 HTTP 页面和 WebSocket 中继
"""

import asyncio
import json
import logging
import signal
import sys
from datetime import datetime
from collections import defaultdict

from aiohttp import web

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("nas-relay")

# 全局状态
senders = set()
receivers = set()
connection_info = defaultdict(dict)

# ==================== HTML 前端页面 ====================
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta name="theme-color" content="#1e1e1e">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <title>日志接收器</title>
    <link rel="apple-touch-icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 192 192'%3E%3Crect fill='%23007acc' width='192' height='192' rx='24'/%3E%3Ctext x='96' y='120' font-size='90' text-anchor='middle' fill='white' font-family='Arial'%3E📋%3C/text%3E%3C/svg%3E">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: #0d1117;
            color: #c9d1d9;
            height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }
        .header { background: #161b22; border-bottom: 1px solid #30363d; padding: 12px 16px; flex-shrink: 0; }
        .header-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
        .title { font-size: 18px; font-weight: 600; color: #f0f6fc; }
        .status { display: flex; align-items: center; gap: 6px; font-size: 13px; }
        .status-dot { width: 8px; height: 8px; border-radius: 50%; background: #f85149; transition: background 0.3s; }
        .status-dot.connected { background: #3fb950; box-shadow: 0 0 6px #3fb950; }
        .status-dot.connecting { background: #d29922; animation: pulse 1s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
        .url-input-group { display: flex; gap: 8px; }
        .url-input {
            flex: 1; background: #0d1117; border: 1px solid #30363d; border-radius: 8px;
            padding: 10px 12px; color: #c9d1d9; font-size: 14px; font-family: "SF Mono", Monaco, monospace; outline: none;
        }
        .url-input:focus { border-color: #58a6ff; }
        .btn { padding: 10px 20px; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.2s; white-space: nowrap; }
        .btn-connect { background: #238636; color: white; }
        .btn-connect:active { background: #2ea043; }
        .btn-disconnect { background: #da3633; color: white; }
        .btn-disconnect:active { background: #f85149; }
        .btn-clear { background: #30363d; color: #c9d1d9; padding: 8px 14px; font-size: 12px; }
        .btn-clear:active { background: #484f58; }
        .stats-bar { display: flex; justify-content: space-between; align-items: center; padding: 6px 16px; background: #0d1117; border-bottom: 1px solid #21262d; font-size: 12px; color: #8b949e; flex-shrink: 0; }
        .stats-bar span { font-family: "SF Mono", monospace; }
        /* 标签栏 */
        .tab-bar { display: flex; overflow-x: auto; background: #161b22; border-bottom: 1px solid #30363d; flex-shrink: 0; -webkit-overflow-scrolling: touch; }
        .tab-bar::-webkit-scrollbar { display: none; }
        .tab-item { padding: 10px 16px; font-size: 13px; color: #8b949e; border-bottom: 2px solid transparent; white-space: nowrap; cursor: pointer; transition: all 0.2s; user-select: none; flex-shrink: 0; }
        .tab-item:hover { color: #c9d1d9; background: #1c2128; }
        .tab-item.active { color: #58a6ff; border-bottom-color: #58a6ff; }
        .tab-badge { display: inline-block; background: #30363d; color: #8b949e; font-size: 10px; padding: 1px 6px; border-radius: 10px; margin-left: 6px; font-family: "SF Mono", monospace; }
        .tab-item.active .tab-badge { background: #1f6feb; color: #ffffff; }
        /* 日志区域 */
        .log-container { flex: 1; overflow: hidden; position: relative; }
        .log-panel { display: none; height: 100%; }
        .log-panel.active { display: block; }
        .log-list { height: 100%; overflow-y: auto; padding: 8px 12px; -webkit-overflow-scrolling: touch; }
        .log-item { padding: 6px 0; border-bottom: 1px solid #161b22; font-size: 13px; line-height: 1.5; word-break: break-all; animation: fadeIn 0.2s ease; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: translateY(0); } }
        .log-time { color: #58a6ff; font-family: "SF Mono", monospace; font-size: 11px; margin-right: 8px; white-space: nowrap; }
        .log-source { color: #d2a8ff; font-family: "SF Mono", monospace; font-size: 11px; margin-right: 8px; white-space: nowrap; }
        .log-content { color: #c9d1d9; }
        .log-empty { text-align: center; color: #484f58; padding: 40px 20px; font-size: 14px; }
        .log-system { color: #d29922; font-style: italic; }
        .toolbar { display: flex; justify-content: space-between; align-items: center; padding: 10px 16px; background: #161b22; border-top: 1px solid #30363d; flex-shrink: 0; gap: 10px; }
        .toolbar-left { display: flex; align-items: center; gap: 10px; flex: 1; }
        .toggle-group { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #8b949e; }
        .toggle-switch { width: 40px; height: 22px; background: #30363d; border-radius: 11px; position: relative; cursor: pointer; transition: background 0.2s; }
        .toggle-switch.active { background: #238636; }
        .toggle-switch::after { content: ""; position: absolute; width: 18px; height: 18px; background: white; border-radius: 50%; top: 2px; left: 2px; transition: transform 0.2s; }
        .toggle-switch.active::after { transform: translateX(18px); }
        .scroll-bottom { position: absolute; bottom: 16px; right: 16px; width: 40px; height: 40px; background: #238636; border: none; border-radius: 50%; color: white; font-size: 18px; cursor: pointer; display: none; align-items: center; justify-content: center; box-shadow: 0 2px 8px rgba(0,0,0,0.4); z-index: 10; }
        .scroll-bottom.show { display: flex; }
        .toast { position: fixed; top: 60px; left: 50%; transform: translateX(-50%) translateY(-20px); background: #161b22; border: 1px solid #30363d; padding: 10px 20px; border-radius: 8px; font-size: 13px; opacity: 0; transition: all 0.3s; pointer-events: none; z-index: 100; white-space: nowrap; }
        .toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
        .toast.success { border-color: #238636; color: #3fb950; }
        .toast.error { border-color: #da3633; color: #f85149; }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-top">
            <div class="title">日志接收器</div>
            <div class="status">
                <div class="status-dot" id="statusDot"></div>
                <span id="statusText">未连接</span>
            </div>
        </div>
        <div class="url-input-group">
            <input type="text" class="url-input" id="wsUrl" placeholder="自动检测中..." value="" readonly>
            <button class="btn btn-connect" id="connectBtn" onclick="toggleConnection()">连接</button>
        </div>
    </div>
    <div class="stats-bar">
        <div>接收日志: <span id="logCount">0</span> 条</div>
        <div>推送端: <span id="senderCount">0</span> 个</div>
    </div>
    <div class="tab-bar" id="tabBar">
        <div class="log-empty" id="tabEmpty" style="padding:10px 16px;color:#484f58;font-size:13px;">等待日志来源...</div>
    </div>
    <div class="log-container" id="logContainer">
        <button class="scroll-bottom" id="scrollBtn" onclick="scrollToBottom()">↓</button>
    </div>
    <div class="toolbar">
        <div class="toolbar-left">
            <div class="toggle-group">
                <div class="toggle-switch active" id="autoScrollToggle" onclick="toggleAutoScroll()"></div>
                <span>自动滚动</span>
            </div>
        </div>
        <button class="btn btn-clear" onclick="clearLogs()">清空日志</button>
    </div>
    <div class="toast" id="toast"></div>
    <script>
        var ws = null;
        var isConnected = false;
        var isConnecting = false;
        var autoScroll = true;
        var totalLogCount = 0;
        var reconnectTimer = null;
        var pingTimer = null;
        var activeTab = null;
        var tabData = {};
        var sourceToTab = {};

        var els = {
            wsUrl: document.getElementById('wsUrl'),
            connectBtn: document.getElementById('connectBtn'),
            statusDot: document.getElementById('statusDot'),
            statusText: document.getElementById('statusText'),
            logCount: document.getElementById('logCount'),
            senderCount: document.getElementById('senderCount'),
            tabBar: document.getElementById('tabBar'),
            tabEmpty: document.getElementById('tabEmpty'),
            logContainer: document.getElementById('logContainer'),
            autoScrollToggle: document.getElementById('autoScrollToggle'),
            scrollBtn: document.getElementById('scrollBtn'),
            toast: document.getElementById('toast')
        };

        (function() {
            var loc = window.location;
            var proto = loc.protocol === 'https:' ? 'wss:' : 'ws:';
            var wsUrl = proto + '//' + loc.host + '/ws';
            els.wsUrl.value = wsUrl;
            els.wsUrl.readOnly = true;
        })();

        function showToast(msg, type) { type = type || 'success'; els.toast.textContent = msg; els.toast.className = 'toast ' + type; setTimeout(function(){ els.toast.classList.add('show'); }, 10); setTimeout(function(){ els.toast.classList.remove('show'); }, 2500); }
        function updateStatus(status, text) { els.statusDot.className = 'status-dot' + (status ? ' ' + status : ''); els.statusText.textContent = text; }
        function toggleConnection() { if (isConnected || isConnecting) { disconnect(); } else { connect(); } }

        function connect() {
            var url = els.wsUrl.value.trim();
            if (!url) { showToast('WebSocket地址无效', 'error'); return; }
            isConnecting = true;
            updateStatus('connecting', '连接中...');
            els.connectBtn.textContent = '断开';
            els.connectBtn.className = 'btn btn-disconnect';
            try {
                ws = new WebSocket(url);
                ws.onopen = function() {
                    isConnecting = false; isConnected = true;
                    updateStatus('connected', '已连接');
                    showToast('连接成功');
                    ws.send(JSON.stringify({ role: 'receiver' }));
                    pingTimer = setInterval(function() { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' })); }, 20000);
                };
                ws.onmessage = function(event) {
                    try { var data = JSON.parse(event.data); handleMessage(data); } catch(e) { appendLog({ content: event.data, timestamp: new Date().toISOString() }); }
                };
                ws.onclose = function() {
                    onDisconnected();
                    if (!isConnecting && els.wsUrl.value.trim()) { showToast('连接断开，3秒后重连...', 'error'); reconnectTimer = setTimeout(function(){ if (!isConnected) connect(); }, 3000); }
                };
                ws.onerror = function() { showToast('连接错误', 'error'); };
            } catch(e) { isConnecting = false; updateStatus('', '连接失败'); showToast('连接失败: ' + e.message, 'error'); els.connectBtn.textContent = '连接'; els.connectBtn.className = 'btn btn-connect'; }
        }

        function disconnect() { if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; } if (pingTimer) { clearInterval(pingTimer); pingTimer = null; } if (ws) { ws.close(); ws = null; } onDisconnected(); }
        function onDisconnected() { isConnected = false; isConnecting = false; updateStatus('', '未连接'); els.connectBtn.textContent = '连接'; els.connectBtn.className = 'btn btn-connect'; }

        function handleMessage(data) {
            var type = data.type || 'log';
            if (type === 'log') { appendLog(data); }
            else if (type === 'system') { appendLog({ content: '[系统] ' + data.content, timestamp: data.timestamp, isSystem: true, source: '_system' }); if (data.senders_count !== undefined) els.senderCount.textContent = data.senders_count; }
            else if (type === 'error') { appendLog({ content: '[错误] ' + data.content, timestamp: new Date().toISOString(), isSystem: true, source: '_system' }); }
            else if (type === 'pong') {}
        }

        function ensureSourceTab(source) {
            if (!source) source = '_unknown';
            if (!(source in sourceToTab)) {
                var tabId = 'src_' + source.replace(/[^a-zA-Z0-9\u4e00-\u9fff_]/g, '_');
                sourceToTab[source] = tabId;
                tabData[tabId] = 0;
                // 创建标签
                if (els.tabEmpty) { els.tabEmpty.style.display = 'none'; }
                var tabEl = document.createElement('div');
                tabEl.className = 'tab-item active';
                tabEl.setAttribute('data-tab', tabId);
                tabEl.onclick = (function(tid){ return function(){ switchTab(tid); }; })(tabId);
                tabEl.innerHTML = '<span>' + escapeHtml(source) + '</span><span class="tab-badge" id="badge-' + tabId + '">0</span>';
                els.tabBar.appendChild(tabEl);
                // 创建面板
                var panel = document.createElement('div');
                panel.className = 'log-panel active';
                panel.id = 'panel-' + tabId;
                panel.innerHTML = '<div class="log-list" id="logList-' + tabId + '"><div class="log-empty">等待日志...</div></div>';
                els.logContainer.insertBefore(panel, els.scrollBtn);
                // 自动切换到新标签
                switchTab(tabId);
            }
            return sourceToTab[source];
        }

        function switchTab(tabId) {
            activeTab = tabId;
            var tabs = els.tabBar.querySelectorAll('.tab-item');
            for (var i = 0; i < tabs.length; i++) { tabs[i].classList.toggle('active', tabs[i].getAttribute('data-tab') === tabId); }
            var panels = els.logContainer.querySelectorAll('.log-panel');
            for (var i = 0; i < panels.length; i++) { panels[i].classList.toggle('active', panels[i].id === 'panel-' + tabId); }
            var activeList = document.getElementById('logList-' + tabId);
            if (activeList) {
                els.scrollBtn.classList.toggle('show', activeList.scrollHeight - activeList.scrollTop - activeList.clientHeight >= 100);
            }
        }

        function appendLog(data) {
            var source = data.source || '_unknown';
            var tabId = ensureSourceTab(source);

            var timestamp = data.timestamp ? new Date(data.timestamp) : new Date();
            var timeStr = timestamp.toLocaleTimeString('zh-CN', { hour12: false });

            var div = document.createElement('div');
            div.textContent = data.content;

            var html = '<span class="log-time">' + timeStr + '</span><span class="log-content">' + div.innerHTML + '</span>';

            appendToPanel(tabId, html, data.isSystem);

            // 更新计数
            totalLogCount++;
            tabData[tabId] = (tabData[tabId] || 0) + 1;
            els.logCount.textContent = totalLogCount;
            var badge = document.getElementById('badge-' + tabId);
            if (badge) badge.textContent = tabData[tabId];
        }

        function appendToPanel(tabId, html, isSystem) {
            var list = document.getElementById('logList-' + tabId);
            if (!list) return;
            var empty = list.querySelector('.log-empty');
            if (empty) empty.remove();
            var item = document.createElement('div');
            item.className = 'log-item';
            if (isSystem) item.classList.add('log-system');
            item.innerHTML = html;
            list.appendChild(item);
            while (list.children.length > 2000) list.removeChild(list.firstChild);
            if (tabId === activeTab) {
                if (autoScroll) { list.scrollTop = list.scrollHeight; els.scrollBtn.classList.remove('show'); }
                else { if (list.scrollHeight - list.scrollTop - list.clientHeight >= 100) els.scrollBtn.classList.add('show'); }
            }
        }

        function scrollToBottom() {
            var list = document.getElementById('logList-' + activeTab);
            if (list) { list.scrollTop = list.scrollHeight; els.scrollBtn.classList.remove('show'); }
        }
        function toggleAutoScroll() { autoScroll = !autoScroll; els.autoScrollToggle.classList.toggle('active', autoScroll); if (autoScroll) scrollToBottom(); }
        function clearLogs() {
            var keys = Object.keys(tabData);
            for (var i = 0; i < keys.length; i++) { tabData[keys[i]] = 0; }
            totalLogCount = 0;
            els.logCount.textContent = '0';
            var panels = els.logContainer.querySelectorAll('.log-list');
            for (var i = 0; i < panels.length; i++) { panels[i].innerHTML = '<div class="log-empty">日志已清空</div>'; }
        }
        function escapeHtml(text) { var d = document.createElement('div'); d.textContent = text; return d.innerHTML; }
        document.body.addEventListener('touchmove', function(e) { if (e.target.closest('.log-list') || e.target.closest('.tab-bar')) return; e.preventDefault(); }, { passive: false });
    </script>
</body>
</html>"""


# ==================== WebSocket 中继逻辑 ====================
async def register_sender(ws):
    senders.add(ws)
    connection_info[ws]["role"] = "sender"
    connection_info[ws]["connected_at"] = datetime.now().isoformat()
    logger.info(f"PC推送端已连接 | 推送端: {len(senders)} | 接收端: {len(receivers)}")


async def register_receiver(ws):
    receivers.add(ws)
    connection_info[ws]["role"] = "receiver"
    connection_info[ws]["connected_at"] = datetime.now().isoformat()
    logger.info(f"移动端接收端已连接 | 推送端: {len(senders)} | 接收端: {len(receivers)}")
    await ws.send_str(json.dumps({
        "type": "system",
        "content": "已连接到NAS中继服务",
        "timestamp": datetime.now().isoformat(),
        "senders_count": len(senders),
        "receivers_count": len(receivers)
    }))


async def unregister(ws):
    role = connection_info[ws].get("role", "unknown")
    senders.discard(ws)
    receivers.discard(ws)
    connection_info.pop(ws, None)
    logger.info(f"{role} 已断开 | 推送端: {len(senders)} | 接收端: {len(receivers)}")


async def broadcast_to_receivers(message, exclude=None):
    if not receivers:
        return
    payload = json.dumps(message)
    dead = set()
    for r in receivers:
        if r is exclude:
            continue
        try:
            await r.send_str(payload)
        except Exception:
            dead.add(r)
    for d in dead:
        await unregister(d)


# ==================== aiohttp 路由处理器 ====================
async def index_handler(request):
    """HTTP GET / 返回前端页面"""
    return web.Response(text=HTML_PAGE, content_type="text/html")


async def ws_handler(request):
    """WebSocket /ws 处理中继 - 统一消息循环"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    client_ip = request.remote or "unknown"
    logger.info(f"新WS连接: {client_ip}")

    role = None
    registered = False

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                if not registered:
                    # 第一条消息：身份注册
                    try:
                        data = json.loads(msg.data)
                        role = data.get("role", "")
                    except json.JSONDecodeError:
                        role = ""

                    if role == "sender":
                        await register_sender(ws)
                        registered = True
                        logger.info(f"发送者已注册，开始接收日志: {client_ip}")
                    elif role == "receiver":
                        await register_receiver(ws)
                        registered = True
                        logger.info(f"接收者已注册，等待日志推送: {client_ip}")
                    else:
                        await ws.send_str(json.dumps({
                            "type": "error",
                            "content": '请发送 {"role": "sender"} 或 {"role": "receiver"}'
                        }))
                        await ws.close()
                        return ws
                elif role == "sender":
                    # 发送者后续消息：日志数据
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        data = {"type": "log", "content": msg.data}

                    msg_type = data.get("type", "log")
                    if msg_type == "log":
                        if "timestamp" not in data:
                            data["timestamp"] = datetime.now().isoformat()
                        await broadcast_to_receivers(data)
                        logger.info(f"转发日志 [{len(receivers)}接收端]: {str(data.get('content', ''))[:80]}")
                    elif msg_type == "ping":
                        await ws.send_str(json.dumps({"type": "pong"}))

                elif role == "receiver":
                    # 接收者后续消息：心跳
                    try:
                        data = json.loads(msg.data)
                        if data.get("type") == "ping":
                            await ws.send_str(json.dumps({"type": "pong"}))
                    except json.JSONDecodeError:
                        pass

            elif msg.type == web.WSMsgType.ERROR:
                logger.error(f"WebSocket错误: {ws.exception()}")

    except Exception as e:
        logger.error(f"WS处理出错: {e}")
    finally:
        await unregister(ws)

    return ws


async def status_reporter():
    while True:
        await asyncio.sleep(60)
        logger.info(f"【状态】推送端: {len(senders)} | 接收端: {len(receivers)}")


async def main():
    host = "0.0.0.0"
    port = 8765

    app = web.Application()
    app.router.add_get("/", index_handler)
    app.router.add_get("/ws", ws_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)

    logger.info("=" * 50)
    logger.info("NAS WebSocket 中继服务启动（集成Web前端）")
    logger.info(f"监听地址: {host}:{port}")
    logger.info(f"  HTTP  → http://{host}:{port}/  (接收页面)")
    logger.info(f"  WS    → ws://{host}:{port}/ws   (中继服务)")
    logger.info(f"Cloudflare Tunnel 将公网流量转发到此地址")
    logger.info("=" * 50)

    await site.start()

    asyncio.create_task(status_reporter())

    stop = asyncio.Future()

    def shutdown():
        logger.info("收到关闭信号，正在停止服务...")
        stop.set_result(None)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown)

    await stop
    await runner.cleanup()
    logger.info("服务已停止")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("通过键盘中断停止")
        sys.exit(0)
