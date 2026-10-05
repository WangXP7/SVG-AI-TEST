# -*- coding: utf-8 -*-
"""
大数分解可视化工具 —— 界面层（gui）
Tkinter + ttk，纯标准库。三套主题、可拖动分栏、实时进度面板、资源监控。
"""
from __future__ import annotations

import os
import time
import json
import ctypes
import tkinter as tk
from tkinter import ttk, messagebox, font as tkfont
from ctypes import wintypes

from core import (InputError, parse_number, group_count, build_tasks, Pipeline,
                  render_group, render_summary, short, fmt_sec)

APP_TITLE = "大数分解可视化工具 v1.8"
SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".bigfactor_gui.json")

PRESETS = ["10^12", "10^30", "10^50", "10^100", "10^200", "10^500",
           "2^64", "1e30", "123456789"]

TIME_LIMITS = [("不限时", 0), ("5 秒", 5), ("10 秒", 10), ("30 秒", 30),
               ("60 秒", 60), ("300 秒", 300), ("600 秒", 600),
               ("1800 秒", 1800), ("3600 秒", 3600)]

ALGOS = [("自动（p-1→rho）", "auto"),
         ("仅 Pollard rho", "rho"),
         ("仅 p-1", "pm1"),
         ("ECM 椭圆曲线", "ecm"),
         ("深度 p-1→ECM→rho", "deep")]

MODES = [("完整质因数分解", "full"), ("两数相乘（证合数）", "two")]

MAX_PROGRESS_ROWS = 12

# ------------------------------------------------------------------ 主题（FR-19）

THEMES = {
    "经典简洁": {
        "ttk": "clam",
        "bg": "#F0F0F0", "fg": "#1A1A1A", "frame": "#F0F0F0",
        "field_bg": "#FFFFFF", "field_fg": "#1A1A1A", "insert": "#1A1A1A",
        "head": "#0000CC", "prime": "#CC0000", "warn": "#D2691E",
        "aux": "#707070", "comp": "#1A1A1A", "err": "#990099",
        "prog": "#007A00", "unit": "#8B4513",
        "btn_bg": "#E8E8E8", "btn_fg": "#1A1A1A", "btn_active": "#D0D8F0",
        "trough": "#DDDDDD", "sel_bg": "#B0D0FF", "sel_fg": "#000000",
        "border": "#B0B0B0",
    },
    "科幻炫酷": {
        "ttk": "clam",
        "bg": "#0B1021", "fg": "#7DF9FF", "frame": "#0B1021",
        "field_bg": "#050A18", "field_fg": "#A8FFEA", "insert": "#39FF14",
        "head": "#4DA6FF", "prime": "#FF4D6D", "warn": "#FFA94D",
        "aux": "#6C7A89", "comp": "#A8FFEA", "err": "#FF66FF",
        "prog": "#39FF14", "unit": "#FFD866",
        "btn_bg": "#12203A", "btn_fg": "#7DF9FF", "btn_active": "#1D3A66",
        "trough": "#101A30", "sel_bg": "#1D3A66", "sel_fg": "#FFFFFF",
        "border": "#1D3A66",
    },
    "卡通二次元": {
        "ttk": "clam",
        "bg": "#FFF5F8", "fg": "#5A3E46", "frame": "#FFF5F8",
        "field_bg": "#FFFEFF", "field_fg": "#5A3E46", "insert": "#E23B6B",
        "head": "#C2185B", "prime": "#E23B6B", "warn": "#F0803C",
        "aux": "#B08FA0", "comp": "#5A3E46", "err": "#9C27B0",
        "prog": "#2FA36B", "unit": "#A9714B",
        "btn_bg": "#FFD9E6", "btn_fg": "#5A3E46", "btn_active": "#FFBFDA",
        "trough": "#FFE6F0", "sel_bg": "#FFC2DC", "sel_fg": "#5A3E46",
        "border": "#FFC2DC",
    },
}

TAG_KEYS = {"head": "head", "prime": "prime", "warn": "warn", "aux": "aux",
            "comp": "comp", "err": "err", "prog": "prog", "unit": "unit"}


# ------------------------------------------------------------------ 资源监控（FR-14，Win32 API）

class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


def _ft(ft: _FILETIME) -> float:
    return ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) * 1e-7


def _cpu_name() -> str:
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        val, _ = winreg.QueryValueEx(key, "ProcessorNameString")
        winreg.CloseKey(key)
        return " ".join(str(val).split())
    except Exception:
        return os.environ.get("PROCESSOR_IDENTIFIER", "未知 CPU")


def _fmt_bytes(b: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024 or unit == "TB":
            return "%.1f %s" % (b, unit)
        b /= 1024.0
    return "%.1f TB" % b


class SysMon:
    """整机与进程级 CPU / 内存采样（Windows 用 Win32 API，其它平台尽力而为）。"""

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def __init__(self):
        self.cpu_name = _cpu_name()
        self.cores = os.cpu_count() or 1
        self.total_mem = 0
        self._prev_sys = None
        self._prev_proc = {}
        self._prev_t = None
        self._is_win = os.name == "nt"
        if self._is_win:
            try:
                self._k32 = ctypes.windll.kernel32
                self._psapi = ctypes.windll.psapi
            except Exception:
                self._is_win = False

    # ---- 整机
    def _sys_times(self):
        fi, fk, fu = _FILETIME(), _FILETIME(), _FILETIME()
        if not self._k32.GetSystemTimes(ctypes.byref(fi), ctypes.byref(fk), ctypes.byref(fu)):
            return None
        return (time.time(), _ft(fi), _ft(fk) + _ft(fu))

    def _mem_status(self):
        ms = _MEMORYSTATUSEX()
        ms.dwLength = ctypes.sizeof(ms)
        if not self._k32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            return None, None
        return ms.ullTotalPhys, ms.ullAvailPhys

    def _proc_times(self, pid: int):
        h = self._k32.OpenProcess(self.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return None
        try:
            fc, fe, fk, fu = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
            if not self._k32.GetProcessTimes(h, ctypes.byref(fc), ctypes.byref(fe),
                                             ctypes.byref(fk), ctypes.byref(fu)):
                return None
            pmc = _PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(pmc)
            rss = 0
            if self._psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                rss = pmc.WorkingSetSize
            return _ft(fk) + _ft(fu), rss
        finally:
            self._k32.CloseHandle(h)

    def sample(self, pids):
        """返回 (整机CPU%, 总内存, 可用内存, 本程序CPU核数, 本程序内存字节, {pid: cpu%})。"""
        out = {"sys_cpu": 0.0, "total_mem": 0, "avail_mem": 0,
               "proc_cpu": 0.0, "proc_mem": 0, "per_pid": {}}
        now = time.time()
        if self._is_win:
            st = self._sys_times()
            if st and self._prev_sys:
                dt = st[0] - self._prev_sys[0]
                if dt > 0.05:
                    d_idle = st[1] - self._prev_sys[1]
                    d_tot = st[2] - self._prev_sys[2]
                    busy = max(0.0, d_tot - d_idle)
                    out["sys_cpu"] = min(100.0, 100.0 * busy / (dt * self.cores))
            if st:
                self._prev_sys = st
            tm, am = self._mem_status()
            if tm:
                out["total_mem"], out["avail_mem"] = tm, am
                self.total_mem = tm
            tot_cpu = 0.0
            tot_mem = 0
            for pid in pids:
                r = self._proc_times(pid)
                if r is None:
                    continue
                cpu, rss = r
                tot_mem += rss
                prev = self._prev_proc.get(pid)
                if prev is not None:
                    pcpu, pt = prev
                    dt = now - pt
                    if dt > 0.05:
                        c = max(0.0, cpu - pcpu) / dt
                        out["per_pid"][pid] = c
                        tot_cpu += c
                self._prev_proc[pid] = (cpu, now)
            out["proc_cpu"] = tot_cpu
            out["proc_mem"] = tot_mem
            # 清理已退出进程的历史
            alive = set(pids)
            for pid in list(self._prev_proc):
                if pid not in alive:
                    self._prev_proc.pop(pid, None)
        else:
            try:
                with open("/proc/meminfo") as f:
                    info = {}
                    for line in f:
                        k, _, v = line.partition(":")
                        info[k] = int(v.split()[0]) * 1024
                out["total_mem"] = info.get("MemTotal", 0)
                out["avail_mem"] = info.get("MemAvailable", info.get("MemFree", 0))
            except Exception:
                pass
            try:
                with open("/proc/loadavg") as f:
                    out["sys_cpu"] = float(f.read().split()[0]) * 100.0 / self.cores
            except Exception:
                pass
            try:
                with open("/proc/self/status") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            out["proc_mem"] = int(line.split()[1]) * 1024
            except Exception:
                pass
        self._prev_t = now
        return out


# ------------------------------------------------------------------ 设置持久化（FR-22）

def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {}


def save_settings(d: dict):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ------------------------------------------------------------------ 主界面

class App:
    def __init__(self, root: tk.Tk, auto_start: str = "", detail_default: bool = False):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1280x860")

        self.st = load_settings()
        self.theme = self.st.get("theme", "经典简洁")
        if self.theme not in THEMES:
            self.theme = "经典简洁"

        self.mon = SysMon()
        self.pipe: Pipeline = None
        self.running = False
        self.t_wall0 = 0.0
        self.calc_total = 0.0
        self.checked_total = 0
        self.primes_total = 0
        self.incomplete = 0
        self.saw_stop = False
        self._stop_t = 0.0
        self.last_total = 0
        self.info: dict = {}        # seq -> 进度信息
        self.slot_seq = [None] * MAX_PROGRESS_ROWS
        self.last_cpu = {}
        self._auto_scroll = True
        self._last_prog_render = 0.0

        self._build()
        self.apply_theme(self.theme)
        self._restore_layout()

        if detail_default:
            self.detail_var.set(True)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<F9>", lambda e: self.toggle_pause())
        self.root.bind("<F10>", lambda e: self.on_skip_current())
        self.root.bind("<F11>", lambda e: self.on_stop())

        self.root.after(80, self._tick)
        self.root.after(1200, self._res_tick)

        if auto_start:
            self.n_var.set(auto_start)
            self.root.after(400, lambda: self.on_start(force=True))

    # ---------------------------------------------------------------- 构建界面
    def _build(self):
        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        top = ttk.Frame(self.root, padding=(8, 6))
        top.pack(side="top", fill="x")

        # --- 第一行：输入 / 限时 / 进程数 / 控制按钮
        r0 = ttk.Frame(top)
        r0.pack(fill="x", pady=2)
        ttk.Label(r0, text="输入 N：").pack(side="left")
        self.n_var = tk.StringVar(value=self.st.get("last_n", "10^50"))
        self.n_combo = ttk.Combobox(r0, textvariable=self.n_var, width=34,
                                    values=PRESETS)
        self.n_combo.pack(side="left", padx=(0, 10))

        ttk.Label(r0, text="每组限时：").pack(side="left")
        self.tl_var = tk.StringVar(value=self.st.get("tlimit", "10 秒"))
        self.tl_combo = ttk.Combobox(r0, textvariable=self.tl_var, width=8,
                                     values=[t[0] for t in TIME_LIMITS], state="readonly")
        if self.tl_var.get() not in [t[0] for t in TIME_LIMITS]:
            self.tl_var.set("10 秒")
        self.tl_combo.pack(side="left", padx=(0, 10))

        ttk.Label(r0, text="进程数：").pack(side="left")
        maxp = max(1, (os.cpu_count() or 1) * 2)
        self.proc_var = tk.IntVar(value=min(maxp, self.st.get("procs", os.cpu_count() or 4)))
        self.proc_spin = tk.Spinbox(r0, from_=1, to=maxp, width=5,
                                    textvariable=self.proc_var)
        self.proc_spin.pack(side="left", padx=(0, 12))

        self.btn_start = ttk.Button(r0, text="开始分解", width=10, command=self.on_start)
        self.btn_start.pack(side="left", padx=2)
        self.btn_pause = ttk.Button(r0, text="暂停 (F9)", width=10,
                                    command=self.toggle_pause, state="disabled")
        self.btn_pause.pack(side="left", padx=2)
        self.btn_skip = ttk.Button(r0, text="跳过当前 (F10)", width=12,
                                   command=self.on_skip_current, state="disabled")
        self.btn_skip.pack(side="left", padx=2)
        self.btn_stop = ttk.Button(r0, text="停止 (F11)", width=10,
                                   command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=2)

        # --- 第二行：模式 / 明细 / 算法 / 每组质数 / 主题
        r1 = ttk.Frame(top)
        r1.pack(fill="x", pady=2)
        ttk.Label(r1, text="分解模式：").pack(side="left")
        self.mode_var = tk.StringVar(value=self.st.get("mode", "full"))
        for text, val in MODES:
            ttk.Radiobutton(r1, text=text, value=val,
                            variable=self.mode_var).pack(side="left", padx=(0, 8))
        self.detail_var = tk.BooleanVar(value=self.st.get("detail", False))
        ttk.Checkbutton(r1, text="列出合数明细",
                        variable=self.detail_var).pack(side="left", padx=(0, 12))
        ttk.Label(r1, text="找因子算法：").pack(side="left")
        self.algo_var = tk.StringVar(value=self.st.get("algo", "auto"))
        self.algo_combo = ttk.Combobox(r1, textvariable=self.algo_var, width=18,
                                       values=[a[1] for a in ALGOS])
        self.algo_combo.pack(side="left", padx=(0, 12))
        ttk.Label(r1, text="每组找质数：").pack(side="left")
        self.target_var = tk.IntVar(value=self.st.get("target", 3))
        tk.Spinbox(r1, from_=1, to=20, width=4,
                   textvariable=self.target_var).pack(side="left", padx=(0, 12))
        ttk.Label(r1, text="主题：").pack(side="left")
        self.theme_var = tk.StringVar(value=self.theme)
        tc = ttk.Combobox(r1, textvariable=self.theme_var, width=12,
                          values=list(THEMES.keys()), state="readonly")
        tc.pack(side="left")
        tc.bind("<<ComboboxSelected>>", lambda e: self.apply_theme(self.theme_var.get()))

        # --- 第三行：说明
        hint = ("规则：从 10^k 起逐个往上检查，每个数量级找出前 N 个质数；"
                "颜色：蓝=组头 红=质数 橙=异常/未完成 灰=辅助；"
                "大数缩写：10^21 / 10^21+1 / 5×10^103。"
                "快捷键：F9 暂停·继续，F10 跳过当前，F11 停止。")
        self.lbl_hint = ttk.Label(top, text=hint, wraplength=1250, justify="left")
        self.lbl_hint.pack(fill="x", pady=(2, 4))

        # --- 分栏：结果区 | 质数记录面板
        self.paned = ttk.Panedwindow(self.root, orient="horizontal")
        self.paned.pack(side="top", fill="both", expand=True, padx=8, pady=(0, 4))

        left = ttk.Frame(self.paned)
        self.txt = tk.Text(left, wrap="none", height=18, undo=False)
        vs = ttk.Scrollbar(left, orient="vertical", command=self.txt.yview)
        hs = ttk.Scrollbar(left, orient="horizontal", command=self.txt.xview)
        self.txt.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.txt.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.txt.bind("<Button-1>", self._on_text_click)
        self.left = left
        self.paned.add(left, weight=3)

        right = ttk.Frame(self.paned)
        self.lbl_rec = ttk.Label(right, text="找到质数记录（实时累积）")
        self.lbl_rec.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 2))
        self.rec = tk.Text(right, wrap="none", width=72, height=18)
        rvs = ttk.Scrollbar(right, orient="vertical", command=self.rec.yview)
        rhs = ttk.Scrollbar(right, orient="horizontal", command=self.rec.xview)
        self.rec.configure(yscrollcommand=rvs.set, xscrollcommand=rhs.set)
        self.rec.grid(row=1, column=0, sticky="nsew")
        rvs.grid(row=1, column=1, sticky="ns")
        rhs.grid(row=2, column=0, sticky="ew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        self.right = right
        self.paned.add(right, weight=1)

        # --- 实时进度面板（FR-10）
        pf = ttk.Frame(self.root, padding=(8, 2))
        pf.pack(fill="x")
        self.prog_head = ttk.Label(pf, text="（当前没有在计算的数量级）", anchor="w")
        self.prog_head.pack(fill="x")
        self.prog_frame = ttk.Frame(pf)
        self.prog_frame.pack(fill="x")
        self.prog_rows = []
        for i in range(MAX_PROGRESS_ROWS):
            row = ttk.Frame(self.prog_frame)
            lab = ttk.Label(row, anchor="w", text="")
            btn = ttk.Button(row, text="跳过", width=5,
                             command=lambda i=i: self.on_skip_row(i))
            lab.pack(side="left", fill="x", expand=True)
            btn.pack(side="right")
            row.pack(fill="x")
            row.pack_forget()
            self.prog_rows.append((row, lab, btn))
        self.prog_overflow = ttk.Label(pf, text="", anchor="w")
        self.prog_overflow.pack(fill="x")

        # --- 资源监控（FR-14）
        self.lbl_sys = ttk.Label(self.root, text="电脑：", anchor="w", padding=(8, 0))
        self.lbl_sys.pack(fill="x")
        self.lbl_me = ttk.Label(self.root, text="本程序：", anchor="w", padding=(8, 0))
        self.lbl_me.pack(fill="x")

        # --- 进度条 + 状态栏
        bot = ttk.Frame(self.root, padding=(8, 4))
        bot.pack(fill="x")
        self.pbar = ttk.Progressbar(bot, orient="horizontal", mode="determinate")
        self.pbar.pack(fill="x")
        self.status = tk.StringVar(value="就绪：请输入 N 后点「开始分解」。")
        self.lbl_status = ttk.Label(bot, textvariable=self.status, anchor="w")
        self.lbl_status.pack(fill="x")

        self._mono = None

    def _mono_font(self):
        if self._mono is None:
            fam = "Consolas"
            try:
                avail = set(tkfont.families())
                for cand in ("Consolas", "Cascadia Mono", "Courier New", "SimHei"):
                    if cand in avail:
                        fam = cand
                        break
            except Exception:
                pass
            self._mono = tkfont.Font(family=fam, size=10)
        return self._mono

    def _on_text_click(self, event=None):
        # 用户手动滚动后暂停自动滚动
        try:
            pos = self.txt.yview()[1]
            self._auto_scroll = pos > 0.995
        except Exception:
            pass

    # ---------------------------------------------------------------- 主题
    def apply_theme(self, name: str):
        th = THEMES.get(name, THEMES["经典简洁"])
        self.theme = name
        self.theme_var.set(name)
        c = th
        st = self.style
        try:
            st.theme_use(c["ttk"])
        except Exception:
            pass
        st.configure(".", background=c["bg"], foreground=c["fg"],
                     fieldbackground=c["field_bg"], bordercolor=c["border"],
                     troughcolor=c["trough"])
        st.configure("TFrame", background=c["frame"])
        st.configure("TLabelframe", background=c["frame"], foreground=c["fg"])
        st.configure("TLabelframe.Label", background=c["frame"], foreground=c["fg"])
        st.configure("TLabel", background=c["frame"], foreground=c["fg"])
        st.configure("TButton", background=c["btn_bg"], foreground=c["btn_fg"],
                     bordercolor=c["border"], lightcolor=c["btn_bg"],
                     darkcolor=c["btn_bg"])
        st.map("TButton", background=[("active", c["btn_active"]),
                                      ("disabled", c["bg"])])
        st.configure("TRadiobutton", background=c["frame"], foreground=c["fg"])
        st.map("TRadiobutton", background=[("active", c["frame"])])
        st.configure("TCheckbutton", background=c["frame"], foreground=c["fg"])
        st.map("TCheckbutton", background=[("active", c["frame"])])
        st.configure("TCombobox", fieldbackground=c["field_bg"],
                     background=c["field_bg"], foreground=c["field_fg"],
                     arrowcolor=c["fg"])
        st.configure("TScrollbar", background=c["btn_bg"], troughcolor=c["trough"],
                     bordercolor=c["border"], arrowcolor=c["fg"])
        st.configure("TProgressbar", background=c["head"], troughcolor=c["trough"],
                     bordercolor=c["border"])
        st.configure("TPanedwindow", background=c["bg"])
        st.configure("Sash", background=c["bg"])

        self.root.configure(background=c["bg"])
        for w in (self.txt, self.rec):
            w.configure(background=c["field_bg"], foreground=c["field_fg"],
                        insertbackground=c["insert"],
                        selectbackground=c["sel_bg"], selectforeground=c["sel_fg"],
                        font=self._mono_font(), relief="flat",
                        highlightbackground=c["border"], highlightthickness=1)
        for key in TAG_KEYS:
            col = c[TAG_KEYS[key]]
            self.txt.tag_configure(key, foreground=col)
            self.rec.tag_configure(key, foreground=col)
        self.txt.tag_configure("head", font=self._mono_font())
        self.txt.tag_configure("prime", font=self._mono_font())
        self.rec.tag_configure("head", font=self._mono_font())
        self.rec.tag_configure("prime", font=self._mono_font())
        # 实时进度区绿字（FR-10）
        st.configure("Prog.TLabel", background=c["frame"], foreground=c["prog"])
        for _row, lab, _btn in self.prog_rows:
            lab.configure(style="Prog.TLabel", font=self._mono_font())
        self.prog_head.configure(style="Prog.TLabel", font=self._mono_font())
        self.prog_overflow.configure(style="Prog.TLabel", font=self._mono_font())
        self.lbl_hint.configure(foreground=c["aux"])
        self.lbl_rec.configure(foreground=c["head"])
        self.st["theme"] = name

    # ---------------------------------------------------------------- 布局持久化
    def _restore_layout(self):
        try:
            g = self.st.get("geometry")
            if g:
                self.root.geometry(g)
        except Exception:
            pass
        self.root.update_idletasks()
        self.root.after(300, self._apply_sash)

    def _apply_sash(self):
        pos = self.st.get("sash")
        try:
            w = self.paned.winfo_width()
            if not pos or not w:
                return
            pos = max(120, min(int(pos), max(120, w - 120)))
            self.paned.sashpos(0, pos)
        except Exception:
            pass

    def _save_layout(self):
        try:
            self.st["geometry"] = self.root.geometry()
            self.paned.update_idletasks()
            pos = self.paned.sashpos(0)
            if pos and pos > 0:
                self.st["sash"] = pos
        except Exception:
            pass
        self.st.update({
            "last_n": self.n_var.get(), "tlimit": self.tl_var.get(),
            "procs": int(self.proc_var.get()), "mode": self.mode_var.get(),
            "detail": bool(self.detail_var.get()), "algo": self.algo_var.get(),
            "target": int(self.target_var.get()), "theme": self.theme,
        })
        save_settings(self.st)

    # ---------------------------------------------------------------- 输出
    def _append(self, widget, lines):
        if not lines:
            return
        widget.configure(state="normal")
        for text, tag in lines:
            widget.insert("end", text + "\n", tag)
        widget.configure(state="disabled")
        if widget is self.txt and self._auto_scroll:
            widget.see("end")
        elif widget is self.rec:
            widget.see("end")

    def _log(self, text, tag="aux"):
        self._append(self.txt, [(text, tag)])

    # ---------------------------------------------------------------- 控制
    def _tlimit_sec(self) -> float:
        for name, sec in TIME_LIMITS:
            if name == self.tl_var.get():
                return float(sec)
        return 10.0

    def _algo(self) -> str:
        v = self.algo_var.get()
        for _t, key in ALGOS:
            if key == v or _t == v:
                return key
        return "auto"

    def on_start(self, force=False):
        if self.running:
            return
        try:
            n = parse_number(self.n_var.get())
        except InputError as e:
            messagebox.showerror("输入有误", str(e))
            return
        groups = group_count(n)
        if groups > 400 and not force:
            if not messagebox.askyesno(
                    "确认", "N 有 %d 位，将产生 %d 个数量级任务（超过 400 个）。\n"
                           "任务数量很大，确定要开始吗？" % (groups, groups)):
                return
        procs = max(1, min(int(self.proc_var.get()), (os.cpu_count() or 1) * 2))
        self.proc_var.set(procs)
        target = max(1, min(20, int(self.target_var.get())))

        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")
        self.rec.configure(state="normal")
        self.rec.delete("1.0", "end")
        self.rec.configure(state="disabled")
        self._auto_scroll = True

        self.info = {}
        self.calc_total = 0.0
        self.checked_total = 0
        self.primes_total = 0
        self.incomplete = 0
        self.saw_stop = False
        self.t_wall0 = time.time()
        self.start_time = time.strftime("%Y-%m-%d %H:%M:%S")

        tl = self._tlimit_sec()
        algo = self._algo()
        algo_s = dict((k, t) for t, k in ALGOS)[algo]
        mode_s = "完整质因数分解" if self.mode_var.get() == "full" else "两数相乘（证合数）"
        self.cur_mode_s, self.cur_algo_s = mode_s, algo_s
        self.cur_tlimit_s = self.tl_var.get()
        self.cur_target = target
        self.cur_nproc = procs
        self.cur_n = n

        self._append(self.txt, [
            ("【开始】N = %s（%d 位，共 %d 个数量级）｜模式：%s ｜算法：%s ｜"
             "每组限时：%s ｜每组找质数：%d 个 ｜并行进程：%d"
             % (short(n), groups, groups, mode_s, algo_s, self.tl_var.get(),
                target, procs), "head"),
            ("", "aux")])

        self.pipe = Pipeline(build_tasks(n), procs, target, self.mode_var.get(),
                             tl, algo, bool(self.detail_var.get()))
        self.pipe.start()
        self.running = True
        self.btn_start.configure(state="disabled")
        self.btn_pause.configure(state="normal")
        self.btn_skip.configure(state="normal")
        self.btn_stop.configure(state="normal")
        self.btn_pause.configure(text="暂停 (F9)")
        self.pbar.configure(maximum=max(1, groups), value=0)
        self._set_status()

    def toggle_pause(self):
        if not self.running or self.pipe is None:
            return
        if self.pipe.paused:
            self.pipe.resume()
            self.btn_pause.configure(text="暂停 (F9)")
        else:
            self.pipe.pause()
            self.btn_pause.configure(text="继续 (F9)")
        self._set_status()

    def on_skip_current(self):
        if not self.running or self.pipe is None:
            return
        self.pipe.skip_all()

    def on_skip_row(self, i):
        if not self.running or self.pipe is None:
            return
        seq = self.slot_seq[i]
        if seq is not None:
            self.pipe.skip_seq(seq)

    def on_stop(self):
        if not self.running or self.pipe is None:
            return
        self.saw_stop = True
        self._stop_t = time.time()
        self.pipe.stop()
        self.btn_stop.configure(state="disabled")
        self.btn_skip.configure(state="disabled")
        self.btn_pause.configure(state="disabled")

    def on_close(self):
        self._save_layout()
        try:
            if self.pipe is not None:
                self.pipe.terminate_all(timeout=2.0)
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    # ---------------------------------------------------------------- 主循环
    def _tick(self):
        try:
            if self.running and self.pipe is not None:
                msgs = self.pipe.pump()
                self._handle_msgs(msgs)
                for res in self.pipe.ordered_results():
                    self._emit_group(res)
                self._check_finish()
        except Exception as e:
            import traceback
            self._log("【界面循环异常】%s" % traceback.format_exc(limit=2), "err")
        self.root.after(80, self._tick)

    def _handle_msgs(self, msgs):
        now = time.time()
        dirty = False
        for m in msgs:
            tag = m[0]
            if tag == "curnum":
                seq, widx, num = m[1], m[2], m[3]
                inf = self.info.get(seq)
                if inf is None:
                    inf = {"widx": widx, "gstart": now}
                    self.info[seq] = inf
                inf["widx"] = widx
                inf["num"] = num
                inf["num_start"] = now
                inf["trial"] = ""
                inf["stage"] = "逐个检查中"
                dirty = True
            elif tag == "trial":
                seq, widx, text = m[1], m[2], m[3]
                inf = self.info.setdefault(seq, {"widx": widx, "gstart": now,
                                                 "num": "", "num_start": now,
                                                 "trial": "", "stage": ""})
                inf["widx"] = widx
                inf["trial"] = text
                dirty = True
            elif tag == "prog":
                seq, widx, elapsed, text = m[1], m[2], m[3], m[4]
                inf = self.info.setdefault(seq, {"widx": widx, "gstart": now,
                                                 "num": "", "num_start": now,
                                                 "trial": "", "stage": ""})
                inf["widx"] = widx
                inf["stage"] = text
                if elapsed is not None:
                    inf["num_start"] = now - float(elapsed)
                dirty = True
            elif tag == "milestone":
                self.primes_total += 1
                self._append(self.rec, [(m[3], "prime")])
            elif tag == "error":
                self._log("【计算进程异常】%s" % m[3], "err")
        if dirty or self.info:
            t = time.time()
            if t - self._last_prog_render >= 0.25:
                self._last_prog_render = t
                self._render_progress()

    def _render_progress(self):
        if not self.running or self.pipe is None:
            return
        total = self.pipe.total
        nproc = self.cur_nproc
        active = self.pipe.active
        self.prog_head.configure(
            text="[正在计算] 共 %d 组 · 并行 %d 进程（每组占 1 个）" % (total, nproc))
        seqs = sorted(active.keys())
        show = seqs[:MAX_PROGRESS_ROWS]
        now = time.time()
        for i in range(MAX_PROGRESS_ROWS):
            row, lab, btn = self.prog_rows[i]
            if i < len(show):
                seq = show[i]
                self.slot_seq[i] = seq
                inf = self.info.get(seq, {})
                gsec = now - inf.get("gstart", now)
                nsec = now - inf.get("num_start", now)
                detail = inf.get("trial") or inf.get("stage") or "逐个检查中"
                widx = inf.get("widx", 0)
                pid = None
                try:
                    pid = self.pipe.procs[widx].pid
                except Exception:
                    pid = None
                cpu = self.last_cpu.get(pid)
                cpus = "（CPU %d%%）" % int(round(cpu)) if cpu is not None else ""
                lab.configure(
                    text="数量级 10^%d（本组已 %d 秒）正在算 %s（该数已 %.1f 秒，%s）"
                         "· 进程#%d%s" % (seq, int(gsec), inf.get("num", "?"),
                                         nsec, detail, widx, cpus))
                row.pack(fill="x")
            else:
                self.slot_seq[i] = None
                row.pack_forget()
        rest = len(seqs) - len(show)
        self.prog_overflow.configure(
            text=("……其余 %d 组进行中" % rest) if rest > 0 else "")
        if not seqs:
            self.prog_head.configure(
                text="[等待中] 共 %d 组 · 并行 %d 进程（每组占 1 个）——当前没有在计算的数量级"
                     % (total, nproc))

    def _emit_group(self, res):
        self.calc_total += res.get("elapsed", 0.0)
        self.checked_total += res.get("checked", 0)
        self.primes_total_from_res = getattr(self, "primes_total_from_res", 0)
        if res["nprime"] < res["target"]:
            self.incomplete += 1
        self.info.pop(res["seq"], None)
        self._append(self.txt, render_group(res, bool(self.detail_var.get())))
        self._append(self.txt, [("", "aux")])
        self.pbar["value"] = res["seq"] + 1
        self._set_status()

    def _check_finish(self):
        if not self.running or self.pipe is None:
            return
        p = self.pipe
        if p.done_count >= p.total:
            for res in p.ordered_results():
                self._emit_group(res)
            if p.next_seq >= p.total:
                self._finish()
            return
        # 停止：等计算进程全部退出（或最多等 5 秒）后收尾，允许结果有缺口
        if p.stopping and (not p.is_alive() or time.time() - self._stop_t > 5.0):
            self._handle_msgs(p.pump())
            for res in p.flush_all():
                self._emit_group(res)
            self._finish()

    def _finish(self):
        wall = time.time() - self.t_wall0
        self.last_total = self.pipe.total
        summary = {
            "mode_s": self.cur_mode_s, "algo_s": self.cur_algo_s,
            "nproc": self.cur_nproc, "tlimit_s": self.cur_tlimit_s,
            "target": self.cur_target, "ngroups": self.pipe.total,
            "checked": self.checked_total, "nprimes": self.primes_total,
            "incomplete": self.incomplete, "calc": self.calc_total,
            "wall": wall, "stopped": self.saw_stop,
        }
        self._append(self.txt, render_summary(summary))
        try:
            self.pipe.terminate_all(timeout=3.0)
        except Exception:
            pass
        self.running = False
        self.pipe = None
        self.btn_start.configure(state="normal")
        self.btn_pause.configure(state="disabled", text="暂停 (F9)")
        self.btn_skip.configure(state="disabled")
        self.btn_stop.configure(state="disabled")
        for row, lab, btn in self.prog_rows:
            row.pack_forget()
            lab.configure(text="")
        self.slot_seq = [None] * MAX_PROGRESS_ROWS
        self.prog_head.configure(text="（当前没有在计算的数量级）")
        self.prog_overflow.configure(text="")
        self._set_status(done=True)
        self._save_layout()

    def _set_status(self, done=False):
        if not self.running:
            if not done:
                self.status.set("就绪：请输入 N 后点「开始分解」。")
            else:
                self.status.set("已完成：共 %d 个数量级，检查 %d 个数，找到质数 %d 个。"
                                % (getattr(self, "last_total", 0),
                                   self.checked_total, self.primes_total))
            return
        total = self.pipe.total
        done_n = self.pipe.done_count
        cur = ""
        if self.info:
            seq = min(self.info)
            cur = " · 正在算：10^%d 附近" % seq
        pause = " · 【已暂停】" if self.pipe.paused else ""
        self.status.set("已完成 %d/%d 个数量级 · 已检查 %d 个数 · 找到质数 %d 个%s%s"
                        % (done_n, total, self.checked_total,
                           self.primes_total, cur, pause))

    # ---------------------------------------------------------------- 资源监控刷新
    def _res_tick(self):
        try:
            pids = [os.getpid()]
            if self.pipe is not None:
                pids += self.pipe.child_pids()
            s = self.mon.sample(pids)
            self.last_cpu = s["per_pid"]
            self.lbl_sys.configure(
                text="电脑：%s ｜ 线程数 %d ｜ 整机 CPU 占用 %.0f%% ｜ 内存 总量 %s / 可用 %s"
                     % (self.mon.cpu_name, self.mon.cores, s["sys_cpu"],
                        _fmt_bytes(s["total_mem"]), _fmt_bytes(s["avail_mem"])))
            nproc = len(pids)
            busy = len(self.pipe.active) if self.pipe is not None else 0
            pct = (100.0 * s["proc_cpu"] / self.mon.cores) if self.mon.cores else 0.0
            self.lbl_me.configure(
                text="本程序：进程数 %d（忙碌 %d / 空闲 %d）· CPU 折合 %.2f 核（占整机 %.0f%%）"
                     "· 内存 %s"
                     % (nproc, busy, max(0, nproc - 1 - busy), s["proc_cpu"], pct,
                        _fmt_bytes(s["proc_mem"])))
        except Exception:
            pass
        self.root.after(2000, self._res_tick)
