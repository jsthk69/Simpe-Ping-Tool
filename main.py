import os
import sys
import time
import json
import threading
import subprocess
from datetime import datetime
import urllib.request
import urllib.parse

import tkinter as tk
from tkinter import ttk, messagebox

import pystray
from PIL import Image

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# ================= НАСТРОЙКИ ПО УМОЛЧАНИЮ =================

DEFAULT_HOSTS = [
    {"name": "Google DNS", "host": "8.8.8.8"},
    {"name": "Cloudflare DNS", "host": "1.1.1.1"},
    {"name": "Яндекс", "host": "ya.ru"},
    {"name": "Роутер", "host": "192.168.1.1"},
]

CHECK_INTERVAL = 5      # интервал проверки, секунд
PING_TIMEOUT = 2        # таймаут пинга, секунд

# Цвета
COLOR_BG = "#1E1E1E"
COLOR_PANEL = "#252526"
COLOR_TEXT = "#CCCCCC"
COLOR_MUTED = "#888888"
COLOR_GREEN = "#2ECC71"
COLOR_RED = "#E74C3C"
COLOR_TABLE = "#2D2D30"
COLOR_HEADER = "#3C3C3C"

# Пути
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_FILE = os.path.join(BASE_DIR, "icon.png")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")

# Сколько точек держать в истории RTT на графике
MAX_RTT_POINTS = 200

# ============================================


def ping_host(host: str, timeout: int = PING_TIMEOUT):
    """
    Пингуем хост через системный ping.
    Возвращает (is_up: bool, rtt_ms или None).
    На Windows ping запускается без всплывающей консоли.
    """
    is_windows = sys.platform.startswith("win")

    if is_windows:
        cmd = ["ping", "-n", "1", "-w", str(timeout * 1000), host]
        creationflags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags |= subprocess.CREATE_NO_WINDOW
        startupinfo = None
    else:
        cmd = ["ping", "-c", "1", "-W", str(timeout), host]
        creationflags = 0
        startupinfo = None

    try:
        start = time.time()
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
        elapsed_ms = (time.time() - start) * 1000

        if proc.returncode != 0:
            return False, None

        rtt_ms = None
        for line in proc.stdout.splitlines():
            if "time=" in line.lower():
                part = line.lower().split("time=")[1]
                value = part.split()[0].replace("ms", "").replace("мс", "")
                try:
                    rtt_ms = float(value)
                except Exception:
                    rtt_ms = round(elapsed_ms, 1)
                break

        if rtt_ms is None:
            rtt_ms = round(elapsed_ms, 1)

        return True, rtt_ms

    except Exception:
        return False, None


class PingMonitorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Ping Monitor")
        self.geometry("1000x500")
        self.configure(bg=COLOR_BG)

        # иконка окна
        self._icon_img = None
        try:
            self._icon_img = tk.PhotoImage(file=ICON_FILE)
            self.iconphoto(True, self._icon_img)
        except Exception as e:
            print("Не удалось загрузить icon.png:", e)

        # модель
        self.hosts = []
        self.rows = {}
        self.row_widgets = []
        self.prev_status = {}
        self.events = []

        # история RTT для графиков: host -> list[(datetime, rtt or None)]
        self.rtt_history = {}

        # настройки Telegram
        self.telegram_enabled = False
        self.telegram_bot_token = ""
        self.telegram_chat_id = ""

        # состояние приложения
        self._stop = False
        self.tray_icon = None

        # GUI элементы для графиков
        self.graph_host_var = None
        self.graph_host_combo = None
        self.graph_fig = None
        self.graph_ax = None
        self.graph_canvas = None

        # загрузка настроек
        self.load_settings()
        self.prev_status = {h["host"]: None for h in self.hosts}
        self.rtt_history = {h["host"]: [] for h in self.hosts}

        self._style_dark()
        self._build_ui()

        self.after(1000, self.schedule_checks)

    # ---------- настройки (файл) ----------

    def load_settings(self):
        self.hosts = list(DEFAULT_HOSTS)
        self.telegram_enabled = False
        self.telegram_bot_token = ""
        self.telegram_chat_id = ""

        if not os.path.exists(SETTINGS_FILE):
            return

        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print("Ошибка чтения настроек:", e)
            return

        hosts = data.get("hosts")
        if isinstance(hosts, list) and hosts:
            cleaned = []
            for h in hosts:
                name = str(h.get("name", "")).strip()
                host = str(h.get("host", "")).strip()
                if host:
                    if not name:
                        name = host
                    cleaned.append({"name": name, "host": host})
            if cleaned:
                self.hosts = cleaned

        tg = data.get("telegram", {})
        self.telegram_enabled = bool(tg.get("enabled", False))
        self.telegram_bot_token = str(tg.get("bot_token", "") or "")
        self.telegram_chat_id = str(tg.get("chat_id", "") or "")

    def save_settings(self):
        data = {
            "hosts": self.hosts,
            "telegram": {
                "enabled": self.telegram_enabled,
                "bot_token": self.telegram_bot_token,
                "chat_id": self.telegram_chat_id,
            },
        }
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("Ошибка записи настроек:", e)

    # ---------- тёмная тема ----------

    def _style_dark(self):
        style = ttk.Style()
        style.theme_use("default")

        # общий фон
        style.configure("TFrame", background=COLOR_BG)

        # вкладки
        style.configure(
            "TNotebook",
            background=COLOR_BG,
            borderwidth=0,
        )
        style.configure(
            "TNotebook.Tab",
            background=COLOR_PANEL,
            foreground=COLOR_TEXT,
            padding=(10, 5),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", COLOR_TABLE), ("!selected", COLOR_PANEL)],
            foreground=[("selected", COLOR_TEXT), ("!selected", COLOR_MUTED)],
        )

        # заголовки таблиц
        style.configure(
            "Header.TLabel",
            background=COLOR_HEADER,
            foreground=COLOR_TEXT,
            font=("Segoe UI", 10, "bold"),
            padding=6,
        )

        # обычные подписи/ячейки
        style.configure(
            "Cell.TLabel",
            background=COLOR_TABLE,
            foreground=COLOR_TEXT,
            font=("Segoe UI", 10),
            padding=6,
        )

        # кнопки
        style.configure(
            "Dark.TButton",
            background=COLOR_PANEL,
            foreground=COLOR_TEXT,
            padding=6,
            borderwidth=0,
            focusthickness=0,
        )
        style.map(
            "Dark.TButton",
            background=[("active", COLOR_HEADER), ("pressed", COLOR_HEADER)],
            foreground=[("disabled", COLOR_MUTED)],
            relief=[("pressed", "sunken"), ("!pressed", "flat")],
        )

        # поля ввода
        style.configure(
            "Dark.TEntry",
            fieldbackground=COLOR_TABLE,
            foreground=COLOR_TEXT,
            insertcolor=COLOR_TEXT,
            borderwidth=1,
        )

        # комбобокс
        style.configure(
            "Dark.TCombobox",
            fieldbackground=COLOR_TABLE,
            background=COLOR_PANEL,
            foreground=COLOR_TEXT,
            arrowcolor=COLOR_TEXT,
            borderwidth=1,
        )

        # чекбокс
        style.configure(
            "Dark.TCheckbutton",
            background=COLOR_BG,
            foreground=COLOR_TEXT,
        )

        # скроллбар
        style.configure(
            "Dark.Vertical.TScrollbar",
            troughcolor=COLOR_BG,
            background=COLOR_PANEL,
            arrowcolor=COLOR_TEXT,
            borderwidth=1,
        )

    # ---------- построение GUI ----------

    def _build_ui(self):
        # Notebook с вкладками
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # вкладка мониторинга
        self.monitor_frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.monitor_frame, text="Мониторинг")

        # вкладка графиков
        self.graph_frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.graph_frame, text="Графики")

        # MONITORING TAB
        headers = ["Имя", "Хост", "Статус", "RTT (мс)", "Последняя проверка"]
        for col, text in enumerate(headers):
            ttk.Label(self.monitor_frame, text=text, style="Header.TLabel").grid(
                row=0, column=col, padx=2, pady=2, sticky="nsew"
            )

        self.build_rows()

        for c in range(5):
            self.monitor_frame.grid_columnconfigure(c, weight=1)

        # GRAPH TAB
        self._build_graph_tab()

        # нижняя панель
        bottom = ttk.Frame(self, padding=10)
        bottom.pack(fill=tk.X, side=tk.BOTTOM)

        manage_btn = ttk.Button(
            bottom,
            text="Управление хостами",
            style="Dark.TButton",
            command=self.open_manage_window,
        )
        manage_btn.pack(side=tk.LEFT)

        settings_btn = ttk.Button(
            bottom,
            text="Настройки Telegram",
            style="Dark.TButton",
            command=self.open_telegram_settings,
        )
        settings_btn.pack(side=tk.LEFT, padx=(10, 0))

        history_btn = ttk.Button(
            bottom,
            text="История событий",
            style="Dark.TButton",
            command=self.open_history_window,
        )
        history_btn.pack(side=tk.LEFT, padx=(10, 0))

        quit_btn = ttk.Button(
            bottom,
            text="Выход",
            style="Dark.TButton",
            command=self.on_close,
        )
        quit_btn.pack(side=tk.RIGHT)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_rows(self):
        # очистка
        for w in self.row_widgets:
            w.destroy()
        self.row_widgets.clear()
        self.rows.clear()

        # синхронизация prev_status и rtt_history
        new_prev = {}
        new_history = {}
        for h in self.hosts:
            host = h["host"]
            new_prev[host] = self.prev_status.get(host, None)
            new_history[host] = self.rtt_history.get(host, [])
        self.prev_status = new_prev
        self.rtt_history = new_history

        # строки
        for idx, h in enumerate(self.hosts, start=1):
            name_lbl = ttk.Label(self.monitor_frame, text=h["name"], style="Cell.TLabel")
            host_lbl = ttk.Label(self.monitor_frame, text=h["host"], style="Cell.TLabel")

            status_lbl = tk.Label(
                self.monitor_frame,
                text="...",
                bg=COLOR_TABLE,
                fg=COLOR_TEXT,
                width=10,
                font=("Segoe UI", 10, "bold"),
            )

            rtt_lbl = ttk.Label(self.monitor_frame, text="-", style="Cell.TLabel")
            time_lbl = ttk.Label(self.monitor_frame, text="-", style="Cell.TLabel")

            name_lbl.grid(row=idx, column=0, padx=2, pady=1, sticky="nsew")
            host_lbl.grid(row=idx, column=1, padx=2, pady=1, sticky="nsew")
            status_lbl.grid(row=idx, column=2, padx=2, pady=1, sticky="nsew")
            rtt_lbl.grid(row=idx, column=3, padx=2, pady=1, sticky="nsew")
            time_lbl.grid(row=idx, column=4, padx=2, pady=1, sticky="nsew")

            self.row_widgets.extend([name_lbl, host_lbl, status_lbl, rtt_lbl, time_lbl])

            self.rows[h["host"]] = {
                "status": status_lbl,
                "rtt": rtt_lbl,
                "time": time_lbl,
                "name": h["name"],
            }

        for r in range(1, len(self.hosts) + 1):
            self.monitor_frame.grid_rowconfigure(r, weight=1)

        # обновить список хостов для графика
        self.refresh_graph_hosts()

    def _build_graph_tab(self):
        top = ttk.Frame(self.graph_frame)
        top.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(top, text="Хост для графика:", style="Cell.TLabel").pack(side=tk.LEFT)

        self.graph_host_var = tk.StringVar()
        self.graph_host_combo = ttk.Combobox(
            top,
            textvariable=self.graph_host_var,
            state="readonly",
            width=30,
            style="Dark.TCombobox",
        )
        self.graph_host_combo.pack(side=tk.LEFT, padx=10)

        def on_select_host(event=None):
            self.update_graph()

        self.graph_host_combo.bind("<<ComboboxSelected>>", on_select_host)

        update_btn = ttk.Button(
            top, text="Обновить", style="Dark.TButton", command=self.update_graph
        )
        update_btn.pack(side=tk.LEFT)

        # фигура matplotlib
        self.graph_fig = Figure(figsize=(6, 3), dpi=100)
        self.graph_fig.patch.set_facecolor(COLOR_BG)
        self.graph_ax = self.graph_fig.add_subplot(111)
        self.graph_ax.set_facecolor(COLOR_TABLE)
        self.graph_ax.set_title("RTT выбранного хоста", color=COLOR_TEXT)
        self.graph_ax.set_xlabel("Измерения", color=COLOR_TEXT)
        self.graph_ax.set_ylabel("RTT, мс", color=COLOR_TEXT)
        self.graph_ax.grid(True, color=COLOR_MUTED, alpha=0.3)

        for spine in self.graph_ax.spines.values():
            spine.set_color(COLOR_MUTED)
        self.graph_ax.tick_params(colors=COLOR_TEXT)

        self.graph_canvas = FigureCanvasTkAgg(self.graph_fig, master=self.graph_frame)
        self.graph_canvas_widget = self.graph_canvas.get_tk_widget()
        self.graph_canvas_widget.pack(fill=tk.BOTH, expand=True)
        self.graph_canvas_widget.configure(bg=COLOR_BG, highlightthickness=0)

        self.refresh_graph_hosts()
        self.update_graph()

    def refresh_graph_hosts(self):
        if not self.graph_host_combo:
            return

        hosts_list = [h["host"] for h in self.hosts]
        self.graph_host_combo["values"] = hosts_list

        cur = self.graph_host_var.get()
        if cur not in hosts_list:
            if hosts_list:
                self.graph_host_var.set(hosts_list[0])
            else:
                self.graph_host_var.set("")

    def update_graph(self):
        if not self.graph_ax or not self.graph_canvas:
            return

        host = self.graph_host_var.get()
        self.graph_ax.clear()
        self.graph_ax.set_facecolor(COLOR_TABLE)
        self.graph_fig.patch.set_facecolor(COLOR_BG)

        self.graph_ax.set_xlabel("Измерения", color=COLOR_TEXT)
        self.graph_ax.set_ylabel("RTT, мс", color=COLOR_TEXT)
        self.graph_ax.grid(True, color=COLOR_MUTED, alpha=0.3)
        for spine in self.graph_ax.spines.values():
            spine.set_color(COLOR_MUTED)
        self.graph_ax.tick_params(colors=COLOR_TEXT)

        if not host or host not in self.rtt_history:
            self.graph_ax.set_title("Нет данных", color=COLOR_TEXT)
            self.graph_canvas.draw_idle()
            return

        hist = self.rtt_history.get(host, [])
        if not hist:
            self.graph_ax.set_title(f"{host} — нет данных", color=COLOR_TEXT)
            self.graph_canvas.draw_idle()
            return

        xs = list(range(1, len(hist) + 1))
        ys = [r if r is not None else float("nan") for (_t, r) in hist]

        self.graph_ax.plot(xs, ys, marker="o")
        self.graph_ax.set_title(f"RTT: {host}", color=COLOR_TEXT)
        self.graph_canvas.draw_idle()

    # ---------- жизненный цикл ----------

    def on_close(self):
        if self._stop:
            return
        self._stop = True

        self.save_settings()

        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass

        self.destroy()

    def schedule_checks(self):
        if self._stop:
            return
        threading.Thread(target=self.check_all_hosts, daemon=True).start()
        self.after(CHECK_INTERVAL * 1000, self.schedule_checks)

    def check_all_hosts(self):
        hosts_snapshot = list(self.hosts)
        for h in hosts_snapshot:
            host = h["host"]
            name = h["name"]
            is_up, rtt = ping_host(host)
            now = datetime.now().strftime("%H:%M:%S")
            self.after(0, self.update_row, host, name, is_up, rtt, now)

    def append_event(self, timestamp: str, name: str, host: str, status: str):
        line = f"[{timestamp}] {name} ({host}): {status}"
        self.events.append(line)

        if hasattr(self, "_history_listbox") and self._history_listbox.winfo_exists():
            self._history_listbox.insert(tk.END, line)
            self._history_listbox.yview_moveto(1.0)

    def send_telegram_alert(self, name: str, host: str):
        if not self.telegram_enabled:
            return
        if not self.telegram_bot_token or not self.telegram_chat_id:
            return

        text = f"❗ Упал хост:\n{name} ({host})"
        url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
        data = urllib.parse.urlencode(
            {"chat_id": str(self.telegram_chat_id), "text": text}
        ).encode("utf-8")

        def _worker():
            try:
                req = urllib.request.Request(url, data=data, method="POST")
                urllib.request.urlopen(req, timeout=5).read()
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def update_row(self, host, name, is_up, rtt, timestamp):
        row = self.rows.get(host)
        if not row:
            return

        status_lbl = row["status"]
        rtt_lbl = row["rtt"]
        time_lbl = row["time"]

        prev = self.prev_status.get(host)

        # RTT история
        lst = self.rtt_history.setdefault(host, [])
        lst.append((datetime.now(), rtt if is_up else None))
        if len(lst) > MAX_RTT_POINTS:
            lst.pop(0)

        if is_up:
            status_lbl.config(text="OK", bg=COLOR_GREEN, fg="black")
            rtt_lbl.config(text=f"{rtt:.1f}" if rtt is not None else "-")
            new_status_str = "UP"
        else:
            status_lbl.config(text="DOWN", bg=COLOR_RED, fg="white")
            rtt_lbl.config(text="-")
            new_status_str = "DOWN"

        time_lbl.config(text=timestamp)
        self.prev_status[host] = is_up

        if prev is not None and prev != is_up:
            self.append_event(timestamp, name, host, new_status_str)

        # график, если открыт текущий хост
        if self.graph_host_var and self.graph_host_var.get() == host:
            self.update_graph()

        if prev is True and is_up is False:
            messagebox.showwarning(
                "Хост недоступен",
                f"Объект упал:\n{name} ({host})",
                parent=self,
            )
            self.send_telegram_alert(name, host)

    # ---------- управление хостами ----------

    def open_manage_window(self):
        win = tk.Toplevel(self)
        win.title("Управление хостами")
        win.configure(bg=COLOR_BG)
        win.geometry("500x320")
        win.transient(self)

        if self._icon_img is not None:
            win.iconphoto(True, self._icon_img)

        frame = ttk.Frame(win, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        lbl_list = ttk.Label(frame, text="Хосты:", style="Cell.TLabel")
        lbl_list.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 5))

        listbox = tk.Listbox(
            frame,
            bg=COLOR_TABLE,
            fg=COLOR_TEXT,
            selectbackground="#555555",
            selectforeground=COLOR_TEXT,
            height=10,
            borderwidth=0,
            highlightthickness=0,
        )
        listbox.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(0, 10))

        scrollbar = ttk.Scrollbar(
            frame,
            orient="vertical",
            command=listbox.yview,
            style="Dark.Vertical.TScrollbar",
        )
        scrollbar.grid(row=1, column=2, sticky="ns")
        listbox.config(yscrollcommand=scrollbar.set)

        ttk.Label(frame, text="Имя:", style="Cell.TLabel").grid(row=2, column=0, sticky="w")
        name_var = tk.StringVar()
        name_entry = ttk.Entry(frame, textvariable=name_var, width=30, style="Dark.TEntry")
        name_entry.grid(row=2, column=1, sticky="we", pady=2)

        ttk.Label(frame, text="Хост/IP:", style="Cell.TLabel").grid(row=3, column=0, sticky="w")
        host_var = tk.StringVar()
        host_entry = ttk.Entry(frame, textvariable=host_var, width=30, style="Dark.TEntry")
        host_entry.grid(row=3, column=1, sticky="we", pady=2)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=4, column=0, columnspan=3, pady=10, sticky="e")

        def refresh_listbox():
            listbox.delete(0, tk.END)
            for h in self.hosts:
                listbox.insert(tk.END, f"{h['name']} ({h['host']})")

        def on_select(event=None):
            sel = listbox.curselection()
            if not sel:
                return
            idx = sel[0]
            h = self.hosts[idx]
            name_var.set(h["name"])
            host_var.set(h["host"])

        def on_save():
            name = name_var.get().strip()
            host = host_var.get().strip()
            if not host:
                messagebox.showerror("Ошибка", "Хост/IP не может быть пустым.", parent=win)
                return
            if not name:
                name = host

            sel = listbox.curselection()
            if sel:
                idx = sel[0]
                self.hosts[idx] = {"name": name, "host": host}
            else:
                self.hosts.append({"name": name, "host": host})

            refresh_listbox()
            self.build_rows()
            self.save_settings()

        def on_delete():
            sel = listbox.curselection()
            if not sel:
                return
            idx = sel[0]
            h = self.hosts[idx]
            if messagebox.askyesno(
                "Удалить хост",
                f"Удалить {h['name']} ({h['host']})?",
                parent=win,
            ):
                self.hosts.pop(idx)
                refresh_listbox()
                self.build_rows()
                self.save_settings()
                name_var.set("")
                host_var.set("")

        def on_close_manage():
            win.destroy()

        save_btn = ttk.Button(
            btn_frame, text="Сохранить/Обновить", style="Dark.TButton", command=on_save
        )
        save_btn.pack(side=tk.LEFT, padx=5)

        del_btn = ttk.Button(btn_frame, text="Удалить", style="Dark.TButton", command=on_delete)
        del_btn.pack(side=tk.LEFT, padx=5)

        close_btn = ttk.Button(btn_frame, text="Закрыть", style="Dark.TButton", command=on_close_manage)
        close_btn.pack(side=tk.RIGHT, padx=5)

        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=0)
        frame.grid_columnconfigure(1, weight=1)

        listbox.bind("<<ListboxSelect>>", on_select)

        refresh_listbox()

        win.grab_set()
        win.focus_set()

    # ---------- настройки Telegram ----------

    def open_telegram_settings(self):
        win = tk.Toplevel(self)
        win.title("Настройки Telegram")
        win.configure(bg=COLOR_BG)
        win.geometry("500x220")
        win.transient(self)

        if self._icon_img is not None:
            win.iconphoto(True, self._icon_img)

        frame = ttk.Frame(win, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        enabled_var = tk.BooleanVar(value=self.telegram_enabled)
        token_var = tk.StringVar(value=self.telegram_bot_token)
        chat_var = tk.StringVar(value=self.telegram_chat_id)

        chk = ttk.Checkbutton(
            frame,
            text="Включить уведомления в Telegram",
            variable=enabled_var,
            style="Dark.TCheckbutton",
        )
        chk.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        ttk.Label(frame, text="Bot token:", style="Cell.TLabel").grid(row=1, column=0, sticky="w")
        token_entry = ttk.Entry(frame, textvariable=token_var, width=40, style="Dark.TEntry")
        token_entry.grid(row=1, column=1, sticky="we", pady=5)

        ttk.Label(frame, text="Chat ID:", style="Cell.TLabel").grid(row=2, column=0, sticky="w")
        chat_entry = ttk.Entry(frame, textvariable=chat_var, width=40, style="Dark.TEntry")
        chat_entry.grid(row=2, column=1, sticky="we", pady=5)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=3, column=0, columnspan=2, pady=10, sticky="e")

        def on_save_tg():
            self.telegram_enabled = bool(enabled_var.get())
            self.telegram_bot_token = token_var.get().strip()
            self.telegram_chat_id = chat_var.get().strip()
            self.save_settings()
            win.destroy()

        def on_close_tg():
            win.destroy()

        save_btn = ttk.Button(btn_frame, text="Сохранить", style="Dark.TButton", command=on_save_tg)
        save_btn.pack(side=tk.LEFT, padx=5)

        close_btn = ttk.Button(btn_frame, text="Отмена", style="Dark.TButton", command=on_close_tg)
        close_btn.pack(side=tk.RIGHT, padx=5)

        frame.grid_columnconfigure(1, weight=1)

        win.grab_set()
        win.focus_set()

    # ---------- окно истории ----------

    def open_history_window(self):
        win = tk.Toplevel(self)
        win.title("История событий")
        win.configure(bg=COLOR_BG)
        win.geometry("600x400")
        win.transient(self)

        if self._icon_img is not None:
            win.iconphoto(True, self._icon_img)

        frame = ttk.Frame(win, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        lbl = ttk.Label(frame, text="История (с момента запуска):", style="Cell.TLabel")
        lbl.grid(row=0, column=0, sticky="w", pady=(0, 5))

        listbox = tk.Listbox(
            frame,
            bg=COLOR_TABLE,
            fg=COLOR_TEXT,
            selectbackground="#555555",
            selectforeground=COLOR_TEXT,
            height=15,
            borderwidth=0,
            highlightthickness=0,
        )
        listbox.grid(row=1, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(
            frame,
            orient="vertical",
            command=listbox.yview,
            style="Dark.Vertical.TScrollbar",
        )
        scrollbar.grid(row=1, column=1, sticky="ns")
        listbox.config(yscrollcommand=scrollbar.set)

        for line in self.events:
            listbox.insert(tk.END, line)
        listbox.yview_moveto(1.0)

        self._history_listbox = listbox

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=10, sticky="e")

        def clear_history():
            if messagebox.askyesno("Очистить историю", "Точно очистить историю?", parent=win):
                self.events.clear()
                listbox.delete(0, tk.END)

        clear_btn = ttk.Button(btn_frame, text="Очистить", style="Dark.TButton", command=clear_history)
        clear_btn.pack(side=tk.LEFT, padx=5)

        close_btn = ttk.Button(
            btn_frame, text="Закрыть", style="Dark.TButton", command=win.destroy
        )
        close_btn.pack(side=tk.RIGHT, padx=5)

        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        win.grab_set()
        win.focus_set()


def create_tray_icon(app):
    try:
        image = Image.open(ICON_FILE)
    except Exception:
        image = Image.new("RGB", (64, 64), (80, 80, 80))

    def on_show(icon, item):
        if not app._stop:
            app.after(0, lambda: (app.deiconify(), app.lift()))

    def on_exit(icon, item):
        app.after(0, app.on_close)

    menu = pystray.Menu(
        pystray.MenuItem("Открыть", on_show),
        pystray.MenuItem("Выйти", on_exit),
    )

    icon = pystray.Icon("PingMonitor", image, "Ping Monitor", menu)
    return icon


if __name__ == "__main__":
    app = PingMonitorApp()

    tray_icon = create_tray_icon(app)
    app.tray_icon = tray_icon
    tray_thread = threading.Thread(target=tray_icon.run, daemon=True)
    tray_thread.start()

    app.mainloop()
