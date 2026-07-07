#!/usr/bin/env python3
"""
PC端日志推送软件（多文件夹监控版 V2）
功能：
  - 多文件夹监控，支持文件名格式匹配
  - 配置文件持久化
  - 文件夹自定义备注名
  - 合并/分别显示模式
  - 自动连接 + 系统托盘后台运行
"""

import asyncio
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from tkinter import (
    Tk, Frame, Label, Button, Entry, Text, Scrollbar, filedialog,
    StringVar, BooleanVar, Checkbutton, Listbox, END, NORMAL, DISABLED,
    W, E, N, S, SINGLE, Toplevel, messagebox, Menu, ttk
)

import websockets

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

try:
    import pystray
    from PIL import Image, ImageDraw
    PYSTRAY_AVAILABLE = True
except ImportError:
    PYSTRAY_AVAILABLE = False

# ==================== 主题色 ====================
BG_COLOR = "#1e1e1e"
FG_COLOR = "#d4d4d4"
ACCENT_COLOR = "#007acc"
SUCCESS_COLOR = "#4ec9b0"
ERROR_COLOR = "#f44747"
WARNING_COLOR = "#dcdcaa"
PANEL_BG = "#252526"
ITEM_BG = "#2d2d30"
ITEM_ACTIVE = "#094771"

# ==================== strftime → 正则 ====================
_STRftime_MAP = {
    "%Y": r"(\d{4})", "%m": r"(\d{2})", "%d": r"(\d{2})",
    "%H": r"(\d{2})", "%M": r"(\d{2})", "%S": r"(\d{2})",
    "%y": r"(\d{2})", "%I": r"(\d{2})", "%p": r"(AM|PM)",
    "%B": r"([A-Za-z]+)", "%b": r"([A-Za-z]{3})",
    "%A": r"([A-Za-z]+)", "%a": r"([A-Za-z]{3})",
    "%w": r"(\d)", "%j": r"(\d{3})",
    "%U": r"(\d{2})", "%W": r"(\d{2})",
    "%c": r"(.+)", "%x": r"(.+)", "%X": r"(.+)",
    "%%": r"(%)",
}


def pattern_to_regex(pattern: str) -> str:
    regex = re.escape(pattern)
    for fmt, rx in _STRftime_MAP.items():
        regex = regex.replace(re.escape(fmt), rx)
    return "^" + regex


def find_latest_matching_file(folder: str, pattern: str, extensions: list) -> str | None:
    if not os.path.isdir(folder):
        return None
    regex = re.compile(pattern_to_regex(pattern), re.IGNORECASE)
    matched = []
    for name in os.listdir(folder):
        ext = os.path.splitext(name)[1].lower()
        if ext not in extensions:
            continue
        if regex.match(name):
            path = os.path.join(folder, name)
            try:
                mtime = os.path.getmtime(path)
                matched.append((path, mtime))
            except OSError:
                continue
    if not matched:
        return None
    matched.sort(key=lambda x: x[1], reverse=True)
    return matched[0][0]


# ==================== 配置管理 ====================
class ConfigManager:
    def __init__(self):
        self.config_dir = os.path.join(os.path.expanduser("~"), ".log_sender")
        self.config_file = os.path.join(self.config_dir, "settings.json")
        self.defaults = {
            "nas_url": "ws://192.168.1.100:8765/ws",
            "auto_connect": False,
            "auto_scroll": True,
            "display_mode": "merged",  # merged | separate
            "minimize_to_tray": True,
            "window_geometry": "1100x750+100+100",
            "watch_folders": [],
        }
        self.data = {}
        self.load()

    def load(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}
        # 补全默认值
        for k, v in self.defaults.items():
            if k not in self.data:
                self.data[k] = v

    def save(self):
        os.makedirs(self.config_dir, exist_ok=True)
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def get(self, key):
        return self.data.get(key, self.defaults.get(key))

    def set(self, key, value):
        self.data[key] = value


# ==================== 文件夹监控处理器 ====================
class FolderEventHandler(FileSystemEventHandler):
    def __init__(self, watch_item):
        self.watch_item = watch_item

    def on_created(self, event):
        if event.is_directory:
            return
        self.watch_item.on_file_created(event.src_path)

    def on_modified(self, event):
        if event.is_directory:
            return
        self.watch_item.on_file_modified(event.src_path)


# ==================== 单个监控项 ====================
class WatchItem:
    def __init__(self, folder_path: str, name_pattern: str, extensions: list,
                 display_name: str, on_log_callback, on_status_callback):
        self.folder_path = folder_path
        self.name_pattern = name_pattern
        self.extensions = [e.lower() for e in extensions]
        self.custom_name = display_name
        self.on_log = on_log_callback
        self.on_status = on_status_callback

        self.current_file = None
        self.last_position = 0
        self.observer = None
        self.event_handler = None
        self._running = False
        self._lock = threading.Lock()
        self._poll_timer = None
        self._sent_count = 0
        self._regex = re.compile(pattern_to_regex(name_pattern), re.IGNORECASE)

    def start(self):
        self._running = True
        self._switch_to_latest()
        self._start_observer()
        self._schedule_poll()
        self.on_status(self, "监控中")

    def stop(self):
        self._running = False
        if self._poll_timer:
            self._poll_timer.cancel()
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=2)
            self.observer = None
        self.on_status(self, "已停止")

    def _start_observer(self):
        if not WATCHDOG_AVAILABLE:
            return
        self.event_handler = FolderEventHandler(self)
        self.observer = Observer()
        self.observer.schedule(self.event_handler, self.folder_path, recursive=False)
        self.observer.start()

    def _schedule_poll(self):
        if not self._running:
            return
        self._poll_timer = threading.Timer(15.0, self._poll_check)
        self._poll_timer.daemon = True
        self._poll_timer.start()

    def _poll_check(self):
        if not self._running:
            return
        self._switch_to_latest()
        self._schedule_poll()

    def _switch_to_latest(self):
        latest = find_latest_matching_file(self.folder_path, self.name_pattern, self.extensions)
        if latest is None:
            return
        with self._lock:
            if self.current_file is None:
                self.current_file = latest
                self.last_position = os.path.getsize(latest) if os.path.exists(latest) else 0
                self.on_log(self, f"[系统] 开始监控: {os.path.basename(latest)}", "system")
            elif os.path.abspath(latest) != os.path.abspath(self.current_file):
                old_name = os.path.basename(self.current_file)
                new_name = os.path.basename(latest)
                self.current_file = latest
                self.last_position = os.path.getsize(latest) if os.path.exists(latest) else 0
                self.on_log(self, f"[系统] 日志轮转: {old_name} → {new_name}", "system")

    def on_file_created(self, path: str):
        if not self._match_file(path):
            return
        threading.Timer(1.0, self._switch_to_latest).start()

    def on_file_modified(self, path: str):
        if not self.current_file:
            return
        if os.path.abspath(path) != os.path.abspath(self.current_file):
            return
        self._read_new_lines()

    def _match_file(self, path: str) -> bool:
        name = os.path.basename(path)
        ext = os.path.splitext(name)[1].lower()
        if ext not in self.extensions:
            return False
        return bool(self._regex.match(name))

    def _read_new_lines(self):
        if not self.current_file or not os.path.exists(self.current_file):
            return
        try:
            with open(self.current_file, "r", encoding="utf-8", errors="ignore") as f:
                # 文件被截断（删除内容后重写）时，重置偏移量
                file_size = os.path.getsize(self.current_file)
                if file_size < self.last_position:
                    self.last_position = 0
                f.seek(self.last_position)
                new_lines = f.readlines()
                self.last_position = f.tell()
                for line in new_lines:
                    line = line.rstrip("\n\r")
                    if line:
                        self._sent_count += 1
                        self.on_log(self, line, "sent")
        except Exception as e:
            self.on_log(self, f"[错误] 读取失败: {e}", "error")

    @property
    def sent_count(self):
        return self._sent_count

    @property
    def display_name(self):
        return self.custom_name or os.path.basename(self.folder_path)

    @property
    def status_info(self):
        if self.current_file:
            return f"{os.path.basename(self.current_file)} ({self._sent_count}条)"
        return "等待匹配文件..."


# ==================== 添加/编辑文件夹对话框 ====================
class AddFolderDialog(Toplevel):
    def __init__(self, parent, on_confirm, edit_data=None):
        super().__init__(parent)
        self.title("编辑监控文件夹" if edit_data else "添加监控文件夹")
        self.geometry("520x320")
        self.resizable(False, False)
        self.configure(bg=BG_COLOR)
        self.on_confirm = on_confirm
        self.result = None

        self.transient(parent)
        self.grab_set()
        parent_x = parent.winfo_x() + parent.winfo_width() // 2
        parent_y = parent.winfo_y() + parent.winfo_height() // 2
        self.geometry(f"+{parent_x - 260}+{parent_y - 160}")

        self._build_ui(edit_data)

    def _build_ui(self, edit_data):
        pad = {"padx": 15, "pady": 8}

        Label(self, text="文件夹路径:", bg=BG_COLOR, fg=FG_COLOR, font=("Microsoft YaHei", 10)).grid(row=0, column=0, sticky=W, **pad)
        path_frame = Frame(self, bg=BG_COLOR)
        path_frame.grid(row=0, column=1, sticky=(W, E), **pad)
        path_frame.columnconfigure(0, weight=1)
        self.path_var = StringVar(value=edit_data.get("path", "") if edit_data else "")
        Entry(path_frame, textvariable=self.path_var, font=("Consolas", 10),
              bg=ITEM_BG, fg=FG_COLOR, insertbackground=FG_COLOR,
              relief="flat", highlightthickness=1, highlightcolor=ACCENT_COLOR).grid(row=0, column=0, sticky=(W, E))
        Button(path_frame, text="浏览", command=self._browse, bg=PANEL_BG, fg=FG_COLOR, relief="flat", padx=10).grid(row=0, column=1, padx=(8, 0))

        Label(self, text="显示名称:", bg=BG_COLOR, fg=FG_COLOR, font=("Microsoft YaHei", 10)).grid(row=1, column=0, sticky=W, **pad)
        self.name_var = StringVar(value=edit_data.get("name", "") if edit_data else "")
        Entry(self, textvariable=self.name_var, font=("Microsoft YaHei", 10),
              bg=ITEM_BG, fg=FG_COLOR, insertbackground=FG_COLOR,
              relief="flat", highlightthickness=1, highlightcolor=ACCENT_COLOR).grid(row=1, column=1, sticky=(W, E), **pad)

        Label(self, text="文件名格式:", bg=BG_COLOR, fg=FG_COLOR, font=("Microsoft YaHei", 10)).grid(row=2, column=0, sticky=W, **pad)
        self.pattern_var = StringVar(value=edit_data.get("pattern", "app%Y%m%d") if edit_data else "app%Y%m%d")
        Entry(self, textvariable=self.pattern_var, font=("Consolas", 10),
              bg=ITEM_BG, fg=FG_COLOR, insertbackground=FG_COLOR,
              relief="flat", highlightthickness=1, highlightcolor=ACCENT_COLOR).grid(row=2, column=1, sticky=(W, E), **pad)

        hint = "支持 strftime 占位符，如 %Y(年) %m(月) %d(日)\n示例: better-genshin-impact%Y%m%d 匹配 better-genshin-impact20260707"
        Label(self, text=hint, bg=BG_COLOR, fg="#808080", font=("Microsoft YaHei", 9),
              justify="left", wraplength=480).grid(row=3, column=0, columnspan=2, sticky=W, padx=15, pady=(0, 5))

        Label(self, text="文件后缀:", bg=BG_COLOR, fg=FG_COLOR, font=("Microsoft YaHei", 10)).grid(row=4, column=0, sticky=W, **pad)
        self.ext_var = StringVar(value=", ".join(edit_data.get("extensions", [".log", ".txt"])) if edit_data else ".log, .txt")
        Entry(self, textvariable=self.ext_var, font=("Consolas", 10),
              bg=ITEM_BG, fg=FG_COLOR, insertbackground=FG_COLOR,
              relief="flat", highlightthickness=1, highlightcolor=ACCENT_COLOR).grid(row=4, column=1, sticky=(W, E), **pad)

        btn_frame = Frame(self, bg=BG_COLOR)
        btn_frame.grid(row=5, column=0, columnspan=2, pady=15)
        Button(btn_frame, text="确定", command=self._confirm, bg=ACCENT_COLOR, fg="white",
               font=("Microsoft YaHei", 10, "bold"), relief="flat", padx=25).pack(side="left", padx=10)
        Button(btn_frame, text="取消", command=self.destroy, bg=PANEL_BG, fg=FG_COLOR,
               font=("Microsoft YaHei", 10), relief="flat", padx=25).pack(side="left", padx=10)

        self.columnconfigure(1, weight=1)

    def _browse(self):
        path = filedialog.askdirectory(title="选择日志文件夹")
        if path:
            self.path_var.set(path)
            if not self.name_var.get():
                self.name_var.set(os.path.basename(path))

    def _confirm(self):
        path = self.path_var.get().strip()
        name = self.name_var.get().strip()
        pattern = self.pattern_var.get().strip()
        ext_str = self.ext_var.get().strip()

        if not path or not os.path.isdir(path):
            messagebox.showerror("错误", "请选择有效的文件夹路径")
            return
        if not pattern:
            messagebox.showerror("错误", "请输入文件名格式")
            return

        extensions = [e.strip().lower() for e in ext_str.split(",") if e.strip()]
        if not extensions:
            extensions = [".log", ".txt"]

        self.result = {"path": path, "name": name, "pattern": pattern, "extensions": extensions}
        self.on_confirm(self.result)
        self.destroy()


# ==================== 设置对话框 ====================
class SettingsDialog(Toplevel):
    def __init__(self, parent, config: ConfigManager):
        super().__init__(parent)
        self.title("设置")
        self.geometry("400x280")
        self.resizable(False, False)
        self.configure(bg=BG_COLOR)
        self.config = config

        self.transient(parent)
        self.grab_set()
        parent_x = parent.winfo_x() + parent.winfo_width() // 2
        parent_y = parent.winfo_y() + parent.winfo_height() // 2
        self.geometry(f"+{parent_x - 200}+{parent_y - 140}")

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 20, "pady": 10}

        self.auto_conn = BooleanVar(value=self.config.get("auto_connect"))
        Checkbutton(self, text="启动时自动连接NAS", variable=self.auto_conn,
                    bg=BG_COLOR, fg=FG_COLOR, selectcolor=ITEM_BG,
                    activebackground=BG_COLOR, font=("Microsoft YaHei", 11)).pack(anchor=W, **pad)

        self.to_tray = BooleanVar(value=self.config.get("minimize_to_tray"))
        Checkbutton(self, text="连接成功后最小化到系统托盘", variable=self.to_tray,
                    bg=BG_COLOR, fg=FG_COLOR, selectcolor=ITEM_BG,
                    activebackground=BG_COLOR, font=("Microsoft YaHei", 11)).pack(anchor=W, **pad)

        Label(self, text="注：系统托盘需要安装 pystray 和 Pillow", bg=BG_COLOR,
              fg="#808080", font=("Microsoft YaHei", 9)).pack(anchor=W, padx=20)

        btn_frame = Frame(self, bg=BG_COLOR)
        btn_frame.pack(pady=20)
        Button(btn_frame, text="保存", command=self._save, bg=ACCENT_COLOR, fg="white",
               font=("Microsoft YaHei", 10, "bold"), relief="flat", padx=25).pack(side="left", padx=10)
        Button(btn_frame, text="取消", command=self.destroy, bg=PANEL_BG, fg=FG_COLOR,
               font=("Microsoft YaHei", 10), relief="flat", padx=25).pack(side="left", padx=10)

    def _save(self):
        self.config.set("auto_connect", self.auto_conn.get())
        self.config.set("minimize_to_tray", self.to_tray.get())
        self.config.save()
        self.destroy()


# ==================== 日志标签页组件 ====================
class LogTab:
    def __init__(self, parent, title):
        self.frame = Frame(parent, bg=BG_COLOR)
        self.title = title

        self.text = Text(self.frame, wrap="none", font=("Consolas", 10),
                         bg=BG_COLOR, fg=FG_COLOR, insertbackground=FG_COLOR,
                         relief="flat", padx=8, pady=6, selectbackground=ACCENT_COLOR)
        self.text.pack(side="left", fill="both", expand=True)

        v_scroll = Scrollbar(self.frame, command=self.text.yview, bg=ITEM_BG)
        v_scroll.pack(side="right", fill="y")
        self.text.config(yscrollcommand=v_scroll.set)

        self.text.tag_config("timestamp", foreground="#569cd6")
        self.text.tag_config("source", foreground="#ce9178")
        self.text.tag_config("sent", foreground=SUCCESS_COLOR)
        self.text.tag_config("error", foreground=ERROR_COLOR)
        self.text.tag_config("system", foreground=WARNING_COLOR)

        self._count = 0

    def append(self, source, message, tag, auto_scroll):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.text.insert(END, f"[{timestamp}] ", "timestamp")
        if source:
            self.text.insert(END, f"[{source}] ", "source")
        self.text.insert(END, f"{message}\n", tag)
        if auto_scroll:
            self.text.see(END)
        self._count += 1

    def clear(self):
        self.text.delete(1.0, END)
        self._count = 0

    @property
    def count(self):
        return self._count


# ==================== 主应用 ====================
class LogSenderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("日志实时推送工具 - 多文件夹监控版")
        self.root.geometry("1100x750")
        self.root.minsize(900, 600)
        self.root.configure(bg=BG_COLOR)

        # 配置
        self.config = ConfigManager()
        geo = self.config.get("window_geometry")
        if geo:
            self.root.geometry(geo)

        # 状态
        self.ws_url = StringVar(value=self.config.get("nas_url"))
        self.is_connected = False
        self.ws = None
        self.ws_loop = None
        self.ws_task = None
        self.watch_items = []
        self.auto_scroll = BooleanVar(value=self.config.get("auto_scroll"))
        self.display_mode = StringVar(value=self.config.get("display_mode"))  # merged | separate

        # 标签页管理
        self.notebook = None
        self.tabs = {}  # name -> LogTab
        self.all_tab = None

        # 托盘
        self.tray_icon = None
        self._tray_visible = True

        self._build_ui()
        self._update_connection_status()

        # 加载保存的文件夹
        self._load_folders()

        # 自动连接
        if self.config.get("auto_connect"):
            self.root.after(1000, self._connect)

        # 关闭事件
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        main_frame = Frame(self.root, bg=BG_COLOR)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(2, weight=1)

        # ===== 顶部栏 =====
        top_frame = Frame(main_frame, bg=BG_COLOR)
        top_frame.grid(row=0, column=0, columnspan=2, sticky=(W, E), pady=(0, 10))

        # NAS地址
        Label(top_frame, text="NAS中继:", bg=BG_COLOR, fg=FG_COLOR, font=("Microsoft YaHei", 11)).pack(side="left", padx=(0, 8))
        Entry(top_frame, textvariable=self.ws_url, font=("Consolas", 11),
              bg=ITEM_BG, fg=FG_COLOR, insertbackground=FG_COLOR,
              relief="flat", highlightthickness=1, highlightcolor=ACCENT_COLOR, width=35).pack(side="left")

        self.conn_btn = Button(top_frame, text="连接", command=self._toggle_connection,
                               bg=ACCENT_COLOR, fg="white", font=("Microsoft YaHei", 10, "bold"),
                               relief="flat", padx=15, cursor="hand2")
        self.conn_btn.pack(side="left", padx=(10, 0))

        self.conn_status = Label(top_frame, text="● 未连接", bg=BG_COLOR, fg=ERROR_COLOR, font=("Microsoft YaHei", 10))
        self.conn_status.pack(side="left", padx=10)

        # 分隔线
        Frame(top_frame, bg="#3c3c3c", width=1, height=20).pack(side="left", padx=10, fill="y")

        # 显示模式切换
        Label(top_frame, text="显示:", bg=BG_COLOR, fg=FG_COLOR, font=("Microsoft YaHei", 10)).pack(side="left")
        Radiobutton = Checkbutton  # 简化为按钮样式
        self.btn_merged = Button(top_frame, text="合并", command=lambda: self._set_display_mode("merged"),
                                  bg=ACCENT_COLOR if self.display_mode.get() == "merged" else PANEL_BG,
                                  fg="white" if self.display_mode.get() == "merged" else FG_COLOR,
                                  relief="flat", font=("Microsoft YaHei", 9), padx=12)
        self.btn_merged.pack(side="left", padx=(5, 0))
        self.btn_separate = Button(top_frame, text="分别", command=lambda: self._set_display_mode("separate"),
                                    bg=ACCENT_COLOR if self.display_mode.get() == "separate" else PANEL_BG,
                                    fg="white" if self.display_mode.get() == "separate" else FG_COLOR,
                                    relief="flat", font=("Microsoft YaHei", 9), padx=12)
        self.btn_separate.pack(side="left", padx=2)

        # 设置按钮
        Button(top_frame, text="⚙ 设置", command=self._open_settings,
               bg=PANEL_BG, fg=FG_COLOR, relief="flat", font=("Microsoft YaHei", 9), padx=12).pack(side="left", padx=(10, 0))

        self.total_label = Label(top_frame, text="总发送: 0 条", bg=BG_COLOR, fg="#808080",
                                 font=("Consolas", 10))
        self.total_label.pack(side="right")

        # ===== 左侧：文件夹列表 =====
        left_frame = Frame(main_frame, bg=PANEL_BG, padx=8, pady=8)
        left_frame.grid(row=1, column=0, rowspan=2, sticky=(N, S, W, E), padx=(0, 10))
        left_frame.rowconfigure(2, weight=1)

        Label(left_frame, text="📁 监控文件夹", bg=PANEL_BG, fg=FG_COLOR,
              font=("Microsoft YaHei", 12, "bold")).pack(anchor=W, pady=(0, 8))

        btn_frame = Frame(left_frame, bg=PANEL_BG)
        btn_frame.pack(fill="x", pady=(0, 8))
        Button(btn_frame, text="+ 添加", command=self._add_folder, bg=ACCENT_COLOR, fg="white",
               relief="flat", font=("Microsoft YaHei", 9), padx=12).pack(side="left", padx=(0, 6))
        Button(btn_frame, text="✎ 编辑", command=self._edit_folder, bg="#3c7eaa", fg="white",
               relief="flat", font=("Microsoft YaHei", 9), padx=12).pack(side="left", padx=(0, 6))
        Button(btn_frame, text="- 删除", command=self._remove_folder, bg="#c75450", fg="white",
               relief="flat", font=("Microsoft YaHei", 9), padx=12).pack(side="left")

        self.folder_listbox = Listbox(left_frame, bg=ITEM_BG, fg=FG_COLOR, selectbackground=ITEM_ACTIVE,
                                      selectforeground="white", font=("Microsoft YaHei", 10),
                                      relief="flat", highlightthickness=0, activestyle="none", height=12)
        self.folder_listbox.pack(fill="both", expand=True)
        self.folder_listbox.bind("<<ListboxSelect>>", self._on_select_folder)

        # 详情面板
        self.detail_frame = Frame(left_frame, bg=PANEL_BG, pady=8)
        self.detail_frame.pack(fill="x", pady=(8, 0))
        self.detail_labels = {}
        for key in ["格式", "当前文件", "状态"]:
            f = Frame(self.detail_frame, bg=PANEL_BG)
            f.pack(fill="x", pady=2)
            Label(f, text=f"{key}:", bg=PANEL_BG, fg="#808080", font=("Microsoft YaHei", 9)).pack(side="left")
            lbl = Label(f, text="-", bg=PANEL_BG, fg=FG_COLOR, font=("Microsoft YaHei", 9), wraplength=200, justify="left")
            lbl.pack(side="left", padx=(4, 0))
            self.detail_labels[key] = lbl

        # ===== 右侧：Notebook 标签页日志区 =====
        self.right_frame = Frame(main_frame, bg=BG_COLOR)
        self.right_frame.grid(row=1, column=1, rowspan=2, sticky=(N, S, E, W))
        self.right_frame.columnconfigure(0, weight=1)
        self.right_frame.rowconfigure(0, weight=1)

        # 先创建默认的 notebook
        self._rebuild_notebook()

    def _rebuild_notebook(self):
        """根据显示模式重建 Notebook，保留已有日志内容"""
        # 保存"全部"标签的文本内容（重建后恢复）
        preserved_text = ""
        if self.all_tab is not None:
            preserved_text = self.all_tab.text.get("1.0", END)

        if self.notebook:
            self.notebook.destroy()
        self.tabs.clear()

        self.notebook = ttk.Notebook(self.right_frame)
        self.notebook.grid(row=0, column=0, sticky=(N, S, E, W))

        # 始终有"全部"标签，恢复之前的内容
        self.all_tab = LogTab(self.notebook, "全部")
        self.notebook.add(self.all_tab.frame, text="📋 全部")
        self.tabs["__all__"] = self.all_tab
        if preserved_text.strip():
            self.all_tab.text.insert("1.0", preserved_text)
            self.all_tab.text.see(END)

        # 分别模式下为每个文件夹创建标签
        if self.display_mode.get() == "separate":
            for item in self.watch_items:
                tab = LogTab(self.notebook, item.display_name)
                self.notebook.add(tab.frame, text=item.display_name)
                self.tabs[item.display_name] = tab

        # 样式
        style = ttk.Style()
        style.theme_use("default")
        style.configure("TNotebook", background=BG_COLOR, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL_BG, foreground=FG_COLOR,
                        font=("Microsoft YaHei", 9), padding=(10, 4))
        style.map("TNotebook.Tab", background=[("selected", ITEM_ACTIVE)],
                  foreground=[("selected", "white")])

    def _set_display_mode(self, mode):
        if self.display_mode.get() == mode:
            return
        self.display_mode.set(mode)
        self.config.set("display_mode", mode)
        self.config.save()

        # 更新按钮样式
        self.btn_merged.config(bg=ACCENT_COLOR if mode == "merged" else PANEL_BG,
                               fg="white" if mode == "merged" else FG_COLOR)
        self.btn_separate.config(bg=ACCENT_COLOR if mode == "separate" else PANEL_BG,
                                 fg="white" if mode == "separate" else FG_COLOR)
        self._rebuild_notebook()

    # ==================== 连接管理 ====================
    def _toggle_connection(self):
        if self.is_connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        url = self.ws_url.get().strip()
        if not url:
            messagebox.showerror("错误", "请输入NAS中继地址")
            return

        self.config.set("nas_url", url)
        self.config.save()

        def run_ws():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._ws_loop(url))

        self.ws_task = threading.Thread(target=run_ws, daemon=True)
        self.ws_task.start()

    async def _ws_loop(self, url):
        try:
            self.ws_loop = asyncio.get_event_loop()
            self._log_all("系统", f"正在连接 {url}...", "system")
            self.ws = await websockets.connect(url)
            await self.ws.send(json.dumps({"role": "sender"}))

            self.is_connected = True
            self.root.after(0, self._update_connection_status)
            self._log_all("系统", "WebSocket 连接成功", "system")

            # 托盘提示
            if self.config.get("minimize_to_tray") and PYSTRAY_AVAILABLE:
                self.root.after(0, self._hide_to_tray)

            while True:
                try:
                    msg = await asyncio.wait_for(self.ws.recv(), timeout=25)
                    data = json.loads(msg)
                    if data.get("type") == "pong":
                        continue
                except asyncio.TimeoutError:
                    await self.ws.send(json.dumps({"type": "ping"}))
        except Exception as e:
            self.is_connected = False
            self.root.after(0, self._update_connection_status)
            self._log_all("系统", f"连接失败: {e}", "error")
        finally:
            if self.ws:
                await self.ws.close()
            self.is_connected = False
            self.ws_loop = None
            self.root.after(0, self._update_connection_status)

    def _disconnect(self):
        if self.ws and self.ws_loop:
            try:
                asyncio.run_coroutine_threadsafe(self.ws.close(), self.ws_loop)
            except Exception:
                pass
        self.is_connected = False
        self.ws_loop = None
        self._update_connection_status()
        self._log_all("系统", "已断开连接", "system")

    def _update_connection_status(self):
        if self.is_connected:
            self.conn_status.config(text="● 已连接", fg=SUCCESS_COLOR)
            self.conn_btn.config(text="断开", bg=ERROR_COLOR)
        else:
            self.conn_status.config(text="● 未连接", fg=ERROR_COLOR)
            self.conn_btn.config(text="连接", bg=ACCENT_COLOR)

    # ==================== 文件夹管理 ====================
    def _load_folders(self):
        for info in self.config.get("watch_folders"):
            self._create_item(info)

    def _create_item(self, info):
        item = WatchItem(
            folder_path=info["path"],
            name_pattern=info["pattern"],
            extensions=info.get("extensions", [".log", ".txt"]),
            display_name=info.get("name", ""),
            on_log_callback=self._on_watch_log,
            on_status_callback=self._on_watch_status
        )
        self.watch_items.append(item)
        self.folder_listbox.insert(END, item.display_name)
        item.start()
        if self.display_mode.get() == "separate":
            self._rebuild_notebook()

    def _add_folder(self):
        def on_confirm(result):
            self._create_item(result)
            self._save_folders()

        AddFolderDialog(self.root, on_confirm)

    def _edit_folder(self):
        sel = self.folder_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        item = self.watch_items[idx]

        def on_confirm(result):
            item.custom_name = result["name"]
            item.name_pattern = result["pattern"]
            item.extensions = result["extensions"]
            self.folder_listbox.delete(idx)
            self.folder_listbox.insert(idx, item.display_name)
            self._save_folders()
            if self.display_mode.get() == "separate":
                self._rebuild_notebook()

        edit_data = {
            "path": item.folder_path,
            "name": item.custom_name,
            "pattern": item.name_pattern,
            "extensions": item.extensions,
        }
        AddFolderDialog(self.root, on_confirm, edit_data=edit_data)

    def _remove_folder(self):
        sel = self.folder_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        item = self.watch_items[idx]
        item.stop()
        self.watch_items.pop(idx)
        self.folder_listbox.delete(idx)
        for key in self.detail_labels:
            self.detail_labels[key].config(text="-")
        self._save_folders()
        if self.display_mode.get() == "separate":
            self._rebuild_notebook()

    def _save_folders(self):
        folders = []
        for item in self.watch_items:
            folders.append({
                "path": item.folder_path,
                "name": item.custom_name,
                "pattern": item.name_pattern,
                "extensions": item.extensions,
            })
        self.config.set("watch_folders", folders)
        self.config.save()

    def _on_select_folder(self, event):
        sel = self.folder_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self.watch_items):
            return
        item = self.watch_items[idx]
        self.detail_labels["格式"].config(text=item.name_pattern)
        self.detail_labels["当前文件"].config(text=os.path.basename(item.current_file) if item.current_file else "未找到")
        status_text = "监控中" if item._running else "已停止"
        self.detail_labels["状态"].config(text=status_text)

    def _on_watch_status(self, item, status):
        self.root.after(0, self._on_select_folder, None)

    # ==================== 日志处理 ====================
    def _on_watch_log(self, item, line, tag):
        """WatchItem 的日志回调（子线程调用）"""
        self.root.after(0, lambda: self._append_log(item.display_name, line, tag))
        if tag == "sent":
            self._send_log(line, item.display_name)

    def _append_log(self, source, message, tag):
        """追加到对应标签页"""
        # 始终追加到"全部"
        if self.all_tab:
            self.all_tab.append(source, message, tag, self.auto_scroll.get())

        # 分别模式下追加到对应标签
        if self.display_mode.get() == "separate" and source in self.tabs:
            self.tabs[source].append(None, message, tag, self.auto_scroll.get())

        self._update_total()

    def _log_all(self, source, message, tag):
        """系统级日志"""
        self.root.after(0, lambda: self._append_log(source, message, tag))

    def _send_log(self, line, source=""):
        if not self.is_connected or not self.ws or not self.ws_loop:
            return
        try:
            msg = {
                "type": "log",
                "content": line,
                "source": source,
                "timestamp": datetime.now().isoformat()
            }
            asyncio.run_coroutine_threadsafe(
                self.ws.send(json.dumps(msg)),
                self.ws_loop
            )
        except Exception as e:
            self._log_all("系统", f"发送失败: {e}", "error")

    def _update_total(self):
        total = sum(item.sent_count for item in self.watch_items)
        self.total_label.config(text=f"总发送: {total} 条")

    def _clear_logs(self):
        for tab in self.tabs.values():
            tab.clear()

    # ==================== 设置 ====================
    def _open_settings(self):
        SettingsDialog(self.root, self.config)

    # ==================== 系统托盘 ====================
    def _create_tray_icon(self):
        if not PYSTRAY_AVAILABLE:
            return None
        # 创建简单图标
        width = 64
        height = 64
        image = Image.new("RGB", (width, height), "#007acc")
        dc = ImageDraw.Draw(image)
        dc.rectangle([8, 8, 56, 56], fill="white", outline="white")
        dc.text((16, 20), "LOG", fill="#007acc")

        menu = pystray.Menu(
            pystray.MenuItem("显示窗口", self._show_window, default=True),
            pystray.MenuItem("连接", self._connect, enabled=lambda item: not self.is_connected),
            pystray.MenuItem("断开", self._disconnect, enabled=lambda item: self.is_connected),
            pystray.MenuItem("退出", self._exit_from_tray),
        )
        return pystray.Icon("log_sender", image, "日志推送工具", menu)

    def _hide_to_tray(self):
        if not PYSTRAY_AVAILABLE:
            return
        self.root.withdraw()
        self._tray_visible = False
        if not self.tray_icon:
            self.tray_icon = self._create_tray_icon()
            threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self._tray_visible = True

    def _on_tray_activate(self, icon):
        """托盘图标被双击时恢复窗口"""
        self.root.after(0, self._show_window)

    def _exit_from_tray(self):
        if self.tray_icon:
            self.tray_icon.stop()
        self._cleanup()
        self.root.quit()

    # ==================== 关闭处理 ====================
    def _on_close(self):
        """右上角X关闭按钮 - 弹窗确认"""
        self._show_close_dialog()

    def _show_close_dialog(self):
        """显示关闭确认弹窗"""
        dialog = Toplevel(self.root)
        dialog.title("确认")
        dialog.geometry("320x160")
        dialog.resizable(False, False)
        dialog.configure(bg=BG_COLOR)
        dialog.transient(self.root)
        dialog.grab_set()

        # 居中
        self.root.update_idletasks()
        dx = self.root.winfo_x() + (self.root.winfo_width() - 320) // 2
        dy = self.root.winfo_y() + (self.root.winfo_height() - 160) // 2
        dialog.geometry(f"+{dx}+{dy}")

        # 提示文字
        msg = "日志推送正在运行，请选择操作："
        if self.is_connected:
            msg = "当前已连接到NAS中继，请选择操作："
        Label(dialog, text=msg, bg=BG_COLOR, fg=FG_COLOR,
              font=("Microsoft YaHei", 11), wraplength=280).pack(pady=(20, 15))

        # 按钮区域
        btn_frame = Frame(dialog, bg=BG_COLOR)
        btn_frame.pack(pady=10)

        # 最小化按钮
        btn_min = Button(btn_frame, text="🔽 最小化", command=lambda: self._do_minimize(dialog),
                         bg="#3c7eaa", fg="white", font=("Microsoft YaHei", 10, "bold"),
                         relief="flat", padx=15, cursor="hand2")
        btn_min.pack(side="left", padx=5)

        # 关闭按钮
        close_text = "❌ 断开并关闭" if self.is_connected else "❌ 关闭软件"
        btn_close = Button(btn_frame, text=close_text, command=lambda: self._do_close(dialog),
                           bg="#c75450", fg="white", font=("Microsoft YaHei", 10, "bold"),
                           relief="flat", padx=15, cursor="hand2")
        btn_close.pack(side="left", padx=5)

        # 取消按钮
        btn_cancel = Button(btn_frame, text="取消", command=dialog.destroy,
                            bg=PANEL_BG, fg=FG_COLOR, font=("Microsoft YaHei", 10),
                            relief="flat", padx=15, cursor="hand2")
        btn_cancel.pack(side="left", padx=5)

        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

    def _do_minimize(self, dialog):
        """最小化操作"""
        dialog.destroy()
        if self.config.get("minimize_to_tray") and PYSTRAY_AVAILABLE:
            self._hide_to_tray()
        else:
            self.root.iconify()

    def _do_close(self, dialog):
        """关闭软件操作"""
        dialog.destroy()
        self._cleanup()
        # 停止托盘图标
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.root.destroy()

    def _cleanup(self):
        for item in self.watch_items:
            item.stop()
        self._disconnect()
        # 保存窗口位置
        self.config.set("window_geometry", self.root.geometry())
        self.config.save()


def main():
    if not WATCHDOG_AVAILABLE:
        print("请先安装依赖: pip install websockets watchdog")
        print("可选功能: pip install pystray Pillow")
        sys.exit(1)

    root = Tk()
    app = LogSenderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
