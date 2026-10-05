# -*- coding: utf-8 -*-
"""
大数分解可视化工具 v1.8（全新实现）
按 PRD v1.8 从零构建，不依赖旧代码。
"""
import math
import multiprocessing
import os
import platform
import queue as queue_mod
import random
import json
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

# ------------------------------------------------------------------ 参数
SMALL_PRIME_LIMIT = 100_000
MR_MAX_BITS = 8_000
PM1_MAX_BITS = 2_500
RHO_MAX_BITS = 8_000
PM1_B1 = 50_000
MAX_DIGITS = 200_000
PRIMES_PER_GROUP = 3
ECM_MAX_BITS = 10_000
ECM_B1 = 2_000
ECM_MAX_CURVES = 30

try:
    sys.set_int_max_str_digits(2_000_000)
except AttributeError:
    pass

# ------------------------------------------------------------------ 异常
class FactorTimeout(Exception):
    pass

class StopRequested(Exception):
    pass

class SkipRequested(Exception):
    pass

# ------------------------------------------------------------------ 数学核心
_primes_cache = None

def get_primes():
    global _primes_cache
    if _primes_cache is None:
        n = SMALL_PRIME_LIMIT
        sieve = bytearray(b"\x01") * (n + 1)
        sieve[0] = sieve[1] = 0
        i = 2
        while i * i <= n:
            if sieve[i]:
                sieve[i * i::i] = b"\x00" * ((n - i * i) // i + 1)
            i += 1
        _primes_cache = [j for j, v in enumerate(sieve) if v]
    return _primes_cache

def _pm1_prime_count():
    return sum(1 for p in get_primes() if p <= PM1_B1)

def is_prime(n, ctl=None):
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    if n < 41 * 41:
        return True
    r = math.isqrt(n)
    if r * r == n:
        return False
    if n.bit_length() > MR_MAX_BITS:
        return None
    d = n - 1
    s = 0
    while d % 2 == 0:
        d //= 2
        s += 1
    if n < 3317044064679887385961981:
        bases = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37]
    else:
        bases = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37] + [
            random.randrange(2, n - 1) for _ in range(24)
        ]
    nb = max(3, min(len(bases), 24000 // n.bit_length()))
    bases = bases[:nb]
    for bi, a in enumerate(bases, 1):
        if ctl is not None:
            ctl.check()
            tb = time.perf_counter()
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            pass
        else:
            for _ in range(s - 1):
                x = x * x % n
                if x == n - 1:
                    break
            else:
                return False
        if ctl is not None and bi < len(bases):
            eta = (time.perf_counter() - tb) * (len(bases) - bi)
            ctl.note(f"正在验证是否为质数（第 {bi}/{len(bases)} 轮，预计还需 ~{eta:.0f} 秒）")
    return True

def pollard_pm1(n, ctl):
    total = _pm1_prime_count()
    a = 2
    for i, p in enumerate(get_primes(), 1):
        if p > PM1_B1:
            break
        pk = p
        while pk * p <= PM1_B1:
            pk *= p
        a = pow(a, pk, n)
        if ctl is not None and i % 512 == 0:
            ctl.check()
            ctl.note(f"正在用 p-1 方法找因子（进度 {i}/{total}）")
    g = math.gcd(a - 1, n)
    return g if 1 < g < n else None

def pollard_brent(n, ctl):
    if n % 2 == 0:
        return 2
    if n % 3 == 0:
        return 3
    steps = 0
    while True:
        if ctl is not None:
            ctl.check()
        y = random.randrange(1, n - 1)
        c = random.randrange(1, n - 1)
        m = 128
        g = r = q = 1
        x = ys = y
        while g == 1:
            x = y
            for _ in range(r):
                y = (y * y + c) % n
            k = 0
            while k < r and g == 1:
                ys = y
                for _ in range(min(m, r - k)):
                    y = (y * y + c) % n
                    q = q * abs(x - y) % n
                steps += min(m, r - k)
                g = math.gcd(q, n)
                k += m
                if ctl is not None:
                    ctl.check()
                    rate = steps / max(ctl.elapsed(), 1e-9)
                    ctl.note(f"正在用随机算法找因子（已试 {_fmt_wan(steps)} 步 · 约 {_fmt_wan(rate)} 步/秒）——这类因子可能要找很久")
            r <<= 1
        if g == n:
            g = 1
            while g == 1:
                ys = (ys * ys + c) % n
                g = math.gcd(abs(x - ys), n)
                if ctl is not None:
                    ctl.check()
        if g != n:
            return g

def _inv(x, n):
    old_r, r = x % n, n
    old_s, s = 1, 0
    while r:
        q = old_r // r
        old_r, r = r, old_r - q * r
        old_s, s = s, old_s - q * s
    if old_r == 1:
        return old_s % n
    raise ValueError("no inverse")

def _pt_add(P, Q, a, n):
    if P is None:
        return Q
    if Q is None:
        return P
    x1, y1 = P
    x2, y2 = Q
    if x1 == x2:
        if (y1 + y2) % n == 0:
            return None
        lam = (3 * x1 * x1 + a) * _inv(2 * y1, n) % n
    else:
        lam = (y2 - y1) * _inv((x2 - x1) % n, n) % n
    x3 = (lam * lam - x1 - x2) % n
    return (x3, (lam * (x1 - x3) - y1) % n)

def _pt_mul(k, P, a, n):
    R = None
    add = P
    while k:
        if k & 1:
            R = _pt_add(R, add, a, n)
        k >>= 1
        if k:
            add = _pt_add(add, add, a, n)
    return R

def pollard_ecm(n, ctl, b1=ECM_B1, max_curves=ECM_MAX_CURVES):
    if n % 2 == 0:
        return 2
    primes = [p for p in get_primes() if p <= b1]
    for c in range(max_curves):
        ctl.check()
        a = random.randrange(6, n)
        x0 = random.randrange(1, n)
        y0 = random.randrange(1, n)
        b = (y0 * y0 - x0 * x0 * x0 - a * x0) % n
        P = (x0, y0)
        try:
            for p in primes:
                ctl.check()
                ctl.note(f"ECM 椭圆曲线：第 {c + 1}/{max_curves} 条曲线，B1={b1}，当前素数幂 {p}")
                k = p
                while k * p <= b1:
                    k *= p
                P = _pt_mul(k, P, a, n)
                if P is None:
                    break
        except ValueError:
            g = math.gcd(abs(_inv.__self__), n) if hasattr(_inv, '__self__') else 2
            if 1 < g < n:
                return g
            continue
    return None

def factorize(n, ctl, alg="auto"):
    factors = {}
    work = [n]
    hard = []
    cur = None
    try:
        while work:
            m = work.pop()
            cur = m
            if ctl is not None:
                ctl.check()
            if m == 1:
                cur = None
                continue
            if is_prime(m, ctl):
                factors[m] = factors.get(m, 0) + 1
                cur = None
                continue
            d = None
            if alg in ("auto", "pm1", "deep") and m.bit_length() <= PM1_MAX_BITS:
                d = pollard_pm1(m, ctl)
            if d is None and alg in ("auto", "deep", "ecm") and m.bit_length() <= ECM_MAX_BITS:
                d = pollard_ecm(m, ctl)
            if d is None and alg in ("auto", "deep", "rho") and m.bit_length() <= RHO_MAX_BITS:
                d = pollard_brent(m, ctl)
            if d is None:
                hard.append(m)
                continue
            work.append(d)
            work.append(m // d)
    except (FactorTimeout, StopRequested, SkipRequested):
        if cur is not None and cur not in hard:
            hard.append(cur)
        raise
    return factors, hard

def factorize_two(n, ctl, alg="auto"):
    try:
        if is_prime(n, ctl):
            return None, None
        if n % 2 == 0:
            return 2, n // 2
        if alg in ("auto", "pm1", "deep") and n.bit_length() <= PM1_MAX_BITS:
            d = pollard_pm1(n, ctl)
            if d:
                return d, n // d
        if alg in ("auto", "deep", "ecm") and n.bit_length() <= ECM_MAX_BITS:
            d = pollard_ecm(n, ctl)
            if d:
                return d, n // d
        if alg in ("auto", "deep", "rho") and n.bit_length() <= RHO_MAX_BITS:
            d = pollard_brent(n, ctl)
            if d:
                return d, n // d
        return None, None
    except (FactorTimeout, StopRequested, SkipRequested):
        raise

def _fmt_wan(x):
    if x >= 1e8:
        return f"{x / 1e8:.2f} 亿"
    if x >= 1e4:
        return f"{x / 1e4:.0f} 万"
    return f"{x:.0f}"

def pretty_num(n, max_digits=80):
    s = str(n)
    if len(s) <= 9:
        return s
    if n >= 10 and (n & (n - 1)) == 0 and n.bit_length() <= 64:
        exp = n.bit_length() - 1
        return f"10^{exp}"
    offset = n - 10 ** (len(s) - 1)
    if len(s) >= 10 and 0 < offset < 10 ** 7:
        return f"10^{len(s)-1}+{offset}"
    if len(s) > 9:
        exp = len(s) - 1
        mant = n / (10 ** exp)
        return f"{mant:.7g}×10^{exp}"
    return s

def parse_input(text):
    text = re.sub(r"[\s,，_]", "", text)
    if text.lower().startswith("0x"):
        return int(text, 16)
    if "^" in text:
        a, b = text.split("^", 1)
        return int(a) ** int(b)
    if text.lower().startswith("1e"):
        return int(float(text))
    return int(text)

# ------------------------------------------------------------------ 控制与进度
class Ctl:
    __slots__ = ("stop_event", "skip_event", "pause_event", "deadline", "prog",
                 "seq", "widx", "t0", "last_note", "last_trial")

    def __init__(self, stop_event=None, skip_event=None, pause_event=None,
                 deadline=None, prog=None, seq=0, widx=0):
        self.stop_event = stop_event
        self.skip_event = skip_event
        self.pause_event = pause_event
        self.deadline = deadline
        self.prog = prog
        self.seq = seq
        self.widx = widx
        self.t0 = time.perf_counter()
        self.last_note = 0.0
        self.last_trial = 0.0

    def elapsed(self):
        return time.perf_counter() - self.t0

    def check(self):
        if self.skip_event is not None and self.skip_event.is_set():
            raise SkipRequested()
        if self.stop_event is not None and self.stop_event.is_set():
            raise StopRequested()
        if self.pause_event is not None and self.pause_event.is_set():
            while self.pause_event.is_set():
                if self.stop_event is not None and self.stop_event.is_set():
                    raise StopRequested()
                if self.skip_event is not None and self.skip_event.is_set():
                    raise SkipRequested()
                time.sleep(0.1)
        if self.deadline is not None and time.perf_counter() >= self.deadline:
            raise FactorTimeout()

    def note(self, text):
        if self.prog is None:
            return
        now = time.perf_counter()
        if now - self.t0 < 2.0 or now - self.last_note < 1.0:
            return
        self.last_note = now
        try:
            self.prog.put(("prog", self.seq, self.widx, round(now - self.t0, 1), text))
        except Exception:
            pass

    def trial(self, idx, total, p):
        if self.prog is None:
            return
        now = time.perf_counter()
        if now - self.last_trial < 0.1:
            return
        self.last_trial = now
        try:
            self.prog.put(("trial", self.seq, self.widx,
                           f"正在试除找小因子：第 {idx}/{total} 个素数（当前试到 {p}）"))
        except Exception:
            pass

# ------------------------------------------------------------------ 数量级扫描
def scan_magnitude(k, N, mode, ctl, detail=False, ppn=PRIMES_PER_GROUP, alg="auto"):
    primes_found = 0
    scanned = 0
    rows = []
    start = 10 ** k
    end = min(10 ** (k + 1) - 1, N)
    n = start
    ctl.note(f"数量级 10^{k}：开始逐个检查，目标前 {ppn} 个质数")
    while primes_found < ppn and n <= end:
        ctl.check()
        scanned += 1
        t0 = time.perf_counter()
        try:
            if mode == "prime":
                prime = is_prime(n, ctl)
                if prime is True:
                    kind = "prime"
                    extra = ""
                elif prime is False:
                    kind = "composite"
                    extra = ""
                else:
                    kind = "unknown"
                    extra = "【剩余部分可能是质数，也可能是两个大质数的乘积】"
            else:
                d1, d2 = factorize_two(n, ctl, alg)
                if d1 is None:
                    kind = "prime"
                    extra = ""
                else:
                    kind = "composite"
                    extra = f" = {d1} × {d2}"
        except FactorTimeout:
            kind = "timeout"
            extra = ""
        except SkipRequested:
            kind = "skipped"
            extra = ""
        except StopRequested:
            kind = "stopped"
            extra = ""
        dt = time.perf_counter() - t0
        if kind == "prime":
            primes_found += 1
            rows.append((n, kind, extra, dt, scanned))
        elif detail:
            rows.append((n, kind, extra, dt, scanned))
        elif kind in ("timeout", "stopped", "skipped", "unknown"):
            rows.append((n, kind, extra, dt, scanned))
        ctl.note(f"已检查 {scanned} 个数，找到 {primes_found}/{ppn} 个质数")
        n += 1
    stop_kind = None
    if n > end:
        pass
    elif primes_found >= ppn:
        pass
    if ctl.stop_event is not None and ctl.stop_event.is_set():
        stop_kind = "stopped"
    elif ctl.skip_event is not None and ctl.skip_event.is_set():
        stop_kind = "skipped"
    elif ctl.deadline is not None and time.perf_counter() >= ctl.deadline:
        stop_kind = "timeout"
    return rows, primes_found, scanned, stop_kind

def _do_group_task(seq, k, N, limit, mode, detail, ppn, alg,
                  stop_event, skip_event, pause_event, prog):
    deadline = None if limit is None or limit <= 0 else time.perf_counter() + limit
    ctl = Ctl(stop_event, skip_event, pause_event, deadline, prog, seq, k)
    try:
        rows, pf, scanned, stop_kind = scan_magnitude(k, N, mode, ctl, detail, ppn, alg)
    except (FactorTimeout, StopRequested, SkipRequested) as e:
        rows, pf, scanned = [], 0, 0
        stop_kind = e.__class__.__name__.replace("Requested", "").lower()
    finally:
        pass
    note = {"timeout": f"【本组限时已到：检查了 {scanned} 个数，只找到 {pf}/{ppn} 个质数——可调大「每组限时」或选「不限时」重跑】",
            "stopped": f"【已停止：检查了 {scanned} 个数，找到 {pf}/{ppn} 个质数】",
            "skipped": f"【已跳过本组：检查了 {scanned} 个数，找到 {pf}/{ppn} 个质数】"}.get(stop_kind, "")
    if pf >= ppn:
        tail = (f"  —— 本组共检查 {scanned} 个数，找到前 {ppn} 个质数\n", "muted")
    else:
        tail = (f"  —— 本组共检查 {scanned} 个数，找到 {pf}/{ppn} 个质数{note}\n", "warn")
    return seq, k, rows, pf, scanned, stop_kind, tail

# ------------------------------------------------------------------ 多进程池
class PoolRunner:
    def __init__(self, N, limit, mode, workers, q, detail, ppn, alg):
        self.N = N
        self.limit = limit
        self.mode = mode
        self.workers = max(1, workers)
        self.q = q
        self.detail = detail
        self.ppn = max(1, ppn)
        self.alg = alg
        self.pool = None
        self.manager = None
        self.task_q = None
        self.result_q = None
        self.stop_event = None
        self.skip_event = None
        self.pause_event = None
        self.feeder = None
        self.proc_infos = {}

    def start(self):
        self.manager = multiprocessing.Manager()
        self.task_q = self.manager.Queue()
        self.result_q = self.manager.Queue()
        self.stop_event = self.manager.Event()
        self.skip_event = self.manager.Event()
        self.pause_event = self.manager.Event()
        self.pool = []
        for i in range(self.workers):
            p = multiprocessing.Process(
                target=_worker_loop,
                args=(self.task_q, self.result_q, self.stop_event, self.skip_event,
                      self.pause_event, i),
                daemon=True)
            p.start()
            self.pool.append(p)
            self.proc_infos[i] = {"cpu": 0, "active": False}
        self.feeder = threading.Thread(target=self._feed_loop, daemon=True)
        self.feeder.start()

    def _feed_loop(self):
        k = 0
        digits = len(str(self.N))
        seq = 0
        while k < digits:
            if self.stop_event.is_set():
                break
            if self.skip_event.is_set():
                time.sleep(0.2)
                continue
            while self.task_q.qsize() > self.workers * 2:
                if self.stop_event.is_set():
                    return
                time.sleep(0.05)
            self.task_q.put((seq, k, self.N, self.limit, self.mode,
                             self.detail, self.ppn, self.alg))
            seq += 1
            k += 1

    def stop(self):
        if self.stop_event is not None:
            self.stop_event.set()
        if self.pool:
            for p in self.pool:
                if p.is_alive():
                    p.terminate()
            for p in self.pool:
                p.join(timeout=2)

    def skip_current(self):
        if self.skip_event is not None:
            self.skip_event.set()

    def pause(self):
        if self.pause_event is not None:
            self.pause_event.set()

    def resume(self):
        if self.pause_event is not None:
            self.pause_event.clear()

    def is_paused(self):
        return self.pause_event is not None and self.pause_event.is_set()

def _worker_loop(task_q, result_q, stop_event, skip_event, pause_event, worker_id):
    while True:
        try:
            task = task_q.get(timeout=0.5)
        except Exception:
            if stop_event.is_set():
                break
            continue
        seq, k, N, limit, mode, detail, ppn, alg = task
        try:
            res = _do_group_task(seq, k, N, limit, mode, detail, ppn, alg,
                                 stop_event, skip_event, pause_event, result_q)
            result_q.put(("result",) + res)
        except (FactorTimeout, StopRequested, SkipRequested):
            result_q.put(("gerror", seq, k, None, None))
        finally:
            pass

# ------------------------------------------------------------------ GUI
THEMES = {
    "经典简洁": {
        "bg": "#ffffff", "fg": "#000000", "field": "#ffffff", "panel": "#f5f5f5",
        "accent": "#1976d2", "prime": "#c62828", "warn": "#ef6c00",
        "muted": "#757575", "progress": "#2e7d32", "group": "#1565c0",
    },
    "科幻炫酷": {
        "bg": "#0d1117", "fg": "#c9d1d9", "field": "#161b22", "panel": "#010409",
        "accent": "#58a6ff", "prime": "#ff7b72", "warn": "#d29922",
        "muted": "#8b949e", "progress": "#3fb950", "group": "#79c0ff",
    },
    "卡通二次元": {
        "bg": "#fff0f5", "fg": "#5d4037", "field": "#ffffff", "panel": "#ffe4e1",
        "accent": "#ec407a", "prime": "#d81b60", "warn": "#ff9800",
        "muted": "#a1887f", "progress": "#66bb6a", "group": "#42a5f5",
    },
}

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("大数分解可视化工具 v1.8")
        self.root.geometry("1200x800")
        self.settings_path = os.path.join(os.path.expanduser("~"), ".factor_gui_v18.json")
        self.settings = self._load_settings()
        self.theme_name = self.settings.get("theme", "经典简洁")
        self.sash_pos = self.settings.get("sash", 700)
        self.var_theme = tk.StringVar(value=self.theme_name)
        self.N = 0
        self.mode = tk.StringVar(value="prime")
        self.detail = tk.BooleanVar(value=False)
        self.ppn = tk.IntVar(value=3)
        self.alg = tk.StringVar(value="auto")
        self.limit_var = tk.StringVar(value="10")
        self.workers_var = tk.IntVar(value=os.cpu_count() or 2)
        self.status_text = tk.StringVar(value="就绪")
        self.running = False
        self.runner = None
        self.proc_cpu = {}
        self._build_ui()
        self._build_resource_ui()
        self.apply_theme()
        self._poll_queue()
        self._poll_proc()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<F9>", lambda e: self._toggle_pause())
        self.root.bind("<F10>", lambda e: self._skip_current())
        self.root.bind("<F11>", lambda e: self._stop())

    def _load_settings(self):
        if os.path.exists(self.settings_path):
            try:
                with open(self.settings_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_settings(self):
        d = dict(self.settings)
        d["theme"] = self.theme_name
        try:
            d["sash"] = int(self._pw.sash_pos(0))
        except Exception:
            pass
        try:
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _build_ui(self):
        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Label(top, text="输入 N:").pack(side="left")
        self.cmb_n = ttk.Combobox(top, textvariable=self._input_var(), width=40,
                                  values=["10^12", "10^30", "10^50", "10^100", "10^200", "10^500", "2^64", "1e30", "123456789"])
        self.cmb_n.pack(side="left", padx=4)
        ttk.Label(top, text="每组限时(秒):").pack(side="left", padx=(12, 0))
        ttk.Combobox(top, textvariable=self.limit_var, width=6,
                     values=["不限时", "5", "10", "30", "60", "300", "600", "1800", "3600"]).pack(side="left", padx=4)
        ttk.Label(top, text="进程数:").pack(side="left", padx=(12, 0))
        ttk.Spinbox(top, from_=1, to=128, textvariable=self.workers_var, width=4).pack(side="left", padx=4)
        ttk.Label(top, text="每组找质数:").pack(side="left", padx=(12, 0))
        ttk.Spinbox(top, from_=1, to=20, textvariable=self.ppn, width=3).pack(side="left", padx=4)
        ttk.Label(top, text="算法:").pack(side="left", padx=(12, 0))
        ttk.Combobox(top, textvariable=self.alg, width=14,
                     values=["自动(p-1→rho)", "仅 Pollard rho", "仅 p-1", "ECM 椭圆曲线", "深度 p-1→ECM→rho"]).pack(side="left", padx=4)
        ttk.Label(top, text="主题:").pack(side="left", padx=(12, 0))
        ttk.Combobox(top, textvariable=self.var_theme, width=10,
                     values=list(THEMES.keys())).pack(side="left", padx=4)
        ttk.Button(top, text="开始分解", command=self._start).pack(side="left", padx=(16, 4))
        self.btn_pause = ttk.Button(top, text="暂停", command=self._toggle_pause)
        self.btn_pause.pack(side="left", padx=4)
        ttk.Button(top, text="跳过当前", command=self._skip_current).pack(side="left", padx=4)
        ttk.Button(top, text="停止", command=self._stop).pack(side="left", padx=4)

        mid = ttk.Frame(self.root)
        mid.pack(fill="x", padx=8, pady=2)
        ttk.Radiobutton(mid, text="完整质因数分解", variable=self.mode, value="prime").pack(side="left")
        ttk.Radiobutton(mid, text="两数相乘(证合数)", variable=self.mode, value="composite").pack(side="left", padx=(8, 0))
        ttk.Checkbutton(mid, text="列出合数明细", variable=self.detail).pack(side="left", padx=(12, 0))

        hint = ttk.Label(mid, text="规则：质数标红；异常标橙；辅助信息灰色；实时进度绿色。",
                         foreground="#555")
        hint.pack(side="left", padx=(12, 0))

        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        self._pw = ttk.Panedwindow(body, orient="horizontal")
        self._pw.pack(fill="both", expand=True)

        txtframe = ttk.Frame(self._pw)
        self._pw.add(txtframe, weight=3)
        ttk.Label(txtframe, text="结果区", foreground="#1976d2", font=("", 10, "bold")).pack(anchor="w")
        self.txt = tk.Text(txtframe, font=("Consolas", 10), wrap="none",
                           xscrollcommand=lambda f, l: self._hsb.set(l),
                           yscrollcommand=lambda f, l: self._vsb.set(l))
        self._hsb = ttk.Scrollbar(txtframe, orient="horizontal", command=self.txt.xview)
        self._vsb = ttk.Scrollbar(txtframe, orient="vertical", command=self.txt.yview)
        self.txt.configure(xscrollcommand=self._hsb.set, yscrollcommand=self._vsb.set)
        self._hsb.pack(side="bottom", fill="x")
        self._vsb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)

        evtframe = ttk.Frame(self._pw)
        self._pw.add(evtframe, weight=1)
        ttk.Label(evtframe, text="找到质数记录（实时累积）", foreground="#2e7d32",
                  font=("", 10, "bold")).pack(anchor="w")
        self.evt = tk.Text(evtframe, width=72, font=("Consolas", 9), wrap="none",
                           xscrollcommand=lambda f, l: self._ehsb.set(l),
                           yscrollcommand=lambda f, l: self._evsb.set(l))
        self._ehsb = ttk.Scrollbar(evtframe, orient="horizontal", command=self.evt.xview)
        self._evsb = ttk.Scrollbar(evtframe, orient="vertical", command=self.evt.yview)
        self.evt.configure(xscrollcommand=self._ehsb.set, yscrollcommand=self._evsb.set)
        self._ehsb.pack(side="bottom", fill="x")
        self._evsb.pack(side="right", fill="y")
        self.evt.pack(side="left", fill="both", expand=True)

        prog_frame = ttk.Frame(self.root)
        prog_frame.pack(fill="x", padx=8, pady=2)
        ttk.Label(prog_frame, text="实时进度", foreground="#2e7d32", font=("", 9, "bold")).pack(anchor="w")
        self.prog_text = tk.Text(prog_frame, height=6, font=("Consolas", 9),
                                 wrap="none", state="disabled")
        self.prog_text.pack(fill="x")

        # 恢复分隔条位置
        try:
            if isinstance(self.sash_pos, int) and self.sash_pos >= 200:
                self.root.after(200, lambda: self._pw.sash_pos(0, self.sash_pos))
        except Exception:
            pass

    def _input_var(self):
        v = tk.StringVar()
        self.cmb_n.configure(textvariable=v)
        return v

    def _start(self):
        text = self.cmb_n.get().strip()
        if not text:
            messagebox.showerror("错误", "请输入正整数 N")
            return
        try:
            N = parse_input(text)
        except Exception:
            messagebox.showerror("错误", "无法解析输入，请检查格式")
            return
        if N <= 0:
            messagebox.showerror("错误", "N 必须为正整数")
            return
        digits = len(str(N))
        if digits > MAX_DIGITS:
            messagebox.showerror("错误", f"输入位数 {digits} 超过上限 {MAX_DIGITS}")
            return
        if digits > 400:
            if not messagebox.askyesno("确认", f"组数 {digits} 较多，确定要继续吗？"):
                return
        limit_txt = self.limit_var.get().strip()
        limit = None if limit_txt in ("不限时", "", "0") else int(limit_txt)
        mode = "full" if self.mode.get() == "prime" else "two"
        self.txt.delete("1.0", "end")
        self.evt.delete("1.0", "end")
        self.status_text.set("正在启动...")
        self.running = True
        self._prime_events = []
        self.runner = PoolRunner(
            N, limit, mode, self.workers_var.get(),
            multiprocessing.Manager().Queue(),
            self.detail.get(), self.ppn.get(), self.alg.get()
        )
        self.runner.start()
        self._result_queue = self.runner.result_q
        self._task_queue = self.runner.task_q
        self._seq_buf = {}
        self._next_display_seq = 0
        self._group_progress = {}
        self._total_groups = len(str(N))
        self._completed_groups = 0
        self._total_scanned = 0
        self._total_primes = 0
        self._start_time = time.perf_counter()
        self.status_text.set(f"正在计算 · 共 {self._total_groups} 组 · {self.runner.workers} 进程")

    def _toggle_pause(self):
        if not self.runner:
            return
        if self.runner.is_paused():
            self.runner.resume()
            self.btn_pause.configure(text="暂停")
            self.status_text.set("已继续")
        else:
            self.runner.pause()
            self.btn_pause.configure(text="继续")
            self.status_text.set("已暂停")

    def _skip_current(self):
        if self.runner:
            self.runner.skip_current()
            self.status_text.set("正在跳过当前...")

    def _stop(self):
        if self.runner:
            self.runner.stop()
            self.status_text.set("已停止")

    def _poll_queue(self):
        if self.runner and self._result_queue:
            drained = 0
            while drained < 50:
                try:
                    msg = self._result_queue.get_nowait()
                except Exception:
                    break
                drained += 1
                self._handle_msg(msg)
            if self._completed_groups >= self._total_groups and self._completed_groups > 0:
                self._finish()
        self.root.after(80, self._poll_queue)

    def _handle_msg(self, msg):
        kind = msg[0]
        if kind == "result":
            _, seq, k, rows, pf, scanned, stop_kind, tail = msg
            self._seq_buf[seq] = (k, rows, pf, scanned, stop_kind, tail)
            self._flush_seq()
            self._completed_groups += 1
            self._total_scanned += scanned
            self._total_primes += pf
            if pf >= self.ppn.get():
                pass
            for r in rows:
                if r[1] == "prime":
                    self._append_prime(k, r)
        elif kind == "curnum":
            _, widx, abbrev = msg
            self._group_progress[widx] = {"num": abbrev, "t0": time.perf_counter()}
        elif kind == "trial":
            _, widx, text = msg
            g = self._group_progress.get(widx)
            if g is not None:
                g["trial"] = text
        elif kind == "prog":
            _, widx, elapsed, text = msg
            g = self._group_progress.get(widx)
            if g is not None:
                g["prog"] = text
                g["elapsed"] = elapsed
        elif kind == "milestone":
            _, widx, text = msg
            pass

    def _flush_seq(self):
        while self._next_display_seq in self._seq_buf:
            k, rows, pf, scanned, stop_kind, tail = self._seq_buf.pop(self._next_display_seq)
            self._render_group(k, rows, pf, scanned, stop_kind, tail)
            self._next_display_seq += 1

    def _render_group(self, k, rows, pf, scanned, stop_kind, tail):
        t = THEMES.get(self.theme_name, THEMES["经典简洁"])
        tag = "group"
        self.txt.insert("end", f"{'='*60}\n", tag)
        mode_txt = "完整质因数分解" if self.mode.get() == "prime" else "两数相乘(证合数)"
        header = f"数量级 10^{k}（共 {scanned} 个数，找到 {pf}/{self.ppn.get()} 个质数）模式：{mode_txt}\n"
        self.txt.insert("end", header, ("group",))
        for r in rows:
            n, kind, extra, dt, _ = r
            ab = pretty_num(n)
            if kind == "prime":
                self.txt.insert("end", f"  {ab} = 质数\n", ("prime",))
            elif kind == "composite":
                if self.detail.get():
                    self.txt.insert("end", f"  {ab}{extra}  用时 {dt:.3f}s\n", ("muted",))
            elif kind == "unknown":
                self.txt.insert("end", f"  {ab}{extra}  用时 {dt:.3f}s\n", ("warn",))
            elif kind in ("timeout", "stopped", "skipped"):
                note = {"timeout": "【本组限时已到】", "stopped": "【已停止】", "skipped": "【已跳过】"}.get(kind, "")
                self.txt.insert("end", f"  {ab} {note}  用时 {dt:.3f}s\n", ("warn",))
        self.txt.insert("end", tail[0], tail[1])
        self.txt.see("end")

    def _append_prime(self, k, r):
        n, kind, extra, dt, scanned = r
        text = f"[数量级 10^{k}] 找到本组质数：{pretty_num(n)}（已检查 {scanned} 个数）\n"
        self.evt.insert("end", text, ("prime",))
        self.evt.see("end")

    def _poll_proc(self):
        if self.runner and self.runner.pool:
            busy = 0
            for i, p in enumerate(self.runner.pool):
                if p.is_alive():
                    busy += 1
            idle = self.runner.workers - busy
            self.status_text.set(
                f"共 {self._total_groups} 组 · 已完成 {self._completed_groups} · 检查 {self._total_scanned} 个数 · "
                f"找到 {self._total_primes} 个质数 · 进程 {busy}  busy / {idle} idle"
            )
            lines = []
            for widx, g in sorted(self._group_progress.items()):
                num = g.get("num", "?")
                age = time.perf_counter() - g.get("t0", time.perf_counter())
                trial = g.get("trial", "")
                prog = g.get("prog", "")
                parts = [f"数量级 {pretty_num(10**widx) if False else '10^'+str(widx)}（本组已 {age:.0f} 秒）正在算 {num}"]
                if trial:
                    parts.append(trial)
                if prog:
                    parts.append(prog)
                lines.append(" · ".join(parts) + f" · 进程#{widx}")
            if not lines:
                lines = ["等待任务..."]
            self.prog_text.config(state="normal")
            self.prog_text.delete("1.0", "end")
            self.prog_text.insert("end", "\n".join(lines[:12]))
            if len(lines) > 12:
                self.prog_text.insert("end", f"\n……其余 {len(lines)-12} 组进行中")
            self.prog_text.config(state="disabled")
        self.root.after(1000, self._poll_proc)

    def _finish(self):
        self.status_text.set(f"完成！共 {self._completed_groups}/{self._total_groups} 组 · "
                             f"检查 {self._total_scanned} 个数 · 找到 {self._total_primes} 个质数")
        if self.runner:
            self.runner.stop()

    def _on_close(self):
        if self.runner:
            self.runner.stop()
        self._save_settings()
        self.root.destroy()

    def apply_theme(self, *_args):
        name = self.var_theme.get()
        if name not in THEMES:
            name = "经典简洁"
        self.theme_name = name
        t = THEMES[name]
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=t["bg"], foreground=t["fg"])
        style.configure("TFrame", background=t["bg"])
        style.configure("TLabel", background=t["bg"], foreground=t["fg"])
        style.configure("TButton", background=t["accent"], foreground="#ffffff")
        style.map("TButton", background=[("active", t["accent"])])
        style.configure("TCheckbutton", background=t["bg"], foreground=t["fg"])
        style.configure("TRadiobutton", background=t["bg"], foreground=t["fg"])
        style.configure("TCombobox", fieldbackground=t["field"], foreground=t["fg"])
        style.configure("TSpinbox", fieldbackground=t["field"], foreground=t["fg"])
        style.configure("TPanedwindow", background=t["bg"])
        self.root.configure(bg=t["bg"])
        for w in [self.txt, self.evt, self.prog_text]:
            w.configure(bg=t["panel"], fg=t["fg"], insertbackground=t["fg"])
        try:
            self.res_lbl.configure(foreground=t["muted"])
        except Exception:
            pass

    def _build_resource_ui(self):
        res_frame = ttk.Frame(self.root)
        res_frame.pack(fill="x", padx=8, pady=2)
        self.res_lbl = ttk.Label(res_frame, text="")
        self.res_lbl.pack(side="left")
        self._refresh_resource()

    def _refresh_resource(self):
        try:
            import psutil
            cpu_info = f"CPU {psutil.cpu_count()}核 | 占用 {psutil.cpu_percent(interval=0)}%"
            mem = psutil.virtual_memory()
            mem_info = f"内存 {mem.total//(1024**3)}GB | 可用 {mem.available//(1024**3)}GB ({mem.percent}%)"
            text = f"电脑：{cpu_info}｜{mem_info}"
            if self.runner and self.runner.pool:
                p = psutil.Process()
                text += f"｜本程序 {self.runner.workers} 进程 | CPU {p.cpu_percent(interval=0)}% | 内存 {p.memory_info().rss//(1024**2)}MB"
        except Exception:
            text = "资源监控：需要 psutil 库"
        try:
            self.res_lbl.configure(text=text)
        except Exception:
            pass
        self.root.after(2000, self._refresh_resource)


def main():
    root = tk.Tk()
    app = App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
