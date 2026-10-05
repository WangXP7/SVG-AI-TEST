# -*- coding: utf-8 -*-
"""
大数分解可视化工具 v1.8 —— 独立全新实现
========================================
严格按照《产品需求设计-ZCode-GLM5.3Flash-v1.8.md》实现。

特性（对照 PRD）：
  FR-01 输入（组合框预设 + 大数解析）        FR-02 数量级扫描
  FR-03 分解模式（完整质因数分解 / 两数相乘）  FR-04 紧凑 / 明细显示
  FR-05 组内 '=' 与「用时」列对齐（中文宽度2） FR-06 大数缩写
  FR-07 多进程并行（1 数量级 = 1 进程）       FR-08 每组限时
  FR-09 暂停 / 跳过当前 / 按组跳过 / 停止 + F9/F10/F11
  FR-10 实时进度面板（逐行、秒数、试除进度、进程号、CPU%）
  FR-11 质数发现记录面板（实时累积）          FR-12 组末统计
  FR-13 任务汇总                              FR-14 资源监控（Win32）
  FR-16 每组找质数个数可配（1~20）            FR-17 算法可选（含 ECM）
  FR-18 每组 CPU/进程显示 + 忙碌/空闲统计      FR-19 三套主题并持久化
  FR-21 稳健性（多点协同取消 + 超限降级）      FR-22 可拖动分栏并持久化
  FR-20 算法调研报告见仓库《算法调研报告.md》

运行：
  python 大数分解可视化工具_v1.8.py             # 正常启动
  python 大数分解可视化工具_v1.8.py --selftest    # 无界面自检（验收清单第1条）
  python 大数分解可视化工具_v1.8.py --demo=10^12  # 带预设输入并自动开始
  python 大数分解可视化工具_v1.8.py --detail      # 默认勾选「列出合数明细」

打包：PyInstaller onefile / windowed（multiprocessing.freeze_support 已内置）。
纯标准库，Python >= 3.8，无第三方依赖。
"""
import ctypes
import json
import math
import multiprocessing
import os
import queue as queue_mod
import random
import re
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk, messagebox

FROZEN = getattr(sys, "frozen", False)

# ------------------------------------------------------------------ 全局参数
SMALL_PRIME_LIMIT = 100_000     # 试除使用的素数上限（9592 个素数）
PM1_B1 = 50_000                 # Pollard p-1 第一阶段 B1
MR_MAX_BITS = 8_000             # Miller-Rabin 最大位数（超过只试除）
PM1_MAX_BITS = 2_500            # p-1 最大位数
RHO_MAX_BITS = 8_000            # rho 最大位数
ECM_MAX_BITS = 10_000           # ECM 最大位数
ECM_B1 = 2_000                  # ECM 第一阶段 B1
ECM_MAX_CURVES = 30             # ECM 最大曲线数
MAX_DIGITS = 200_000            # 输入上限（十进制位）
CONFIRM_GROUPS = 400            # 超过此组数需二次确认
SYNC_TRIAL = 512                # 试除每 N 个素数检查一次取消/暂停
SYNC_PM1 = 512                  # p-1 每 N 个素数幂检查一次
SYNC_RHO = 128                  # rho 每个批次步数
PROGRESS_MAX_ROWS = 12          # 实时进度面板最多显示行数
STOP_GRACE = 6.0                # 停止后收尾的最大等待秒数（防永久卡死）

try:
    sys.set_int_max_str_digits(2_000_000)
except (AttributeError, ValueError):
    pass

# ------------------------------------------------------------------ 异常
class FactorTimeout(Exception):
    pass

class StopRequested(Exception):
    pass

class SkipRequested(Exception):
    pass

# ------------------------------------------------------------------ 素数表
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
    ps = get_primes()
    lo, hi = 0, len(ps)
    while lo < hi:
        mid = (lo + hi) // 2
        if ps[mid] <= PM1_B1:
            lo = mid + 1
        else:
            hi = mid
    return lo

# ------------------------------------------------------------------ 显示宽度 / 对齐（FR-5：中文按 2 倍宽度）
def disp_width(s):
    w = 0
    for ch in s:
        o = ord(ch)
        if (0x1100 <= o <= 0x115F or 0x2E80 <= o <= 0xA4CF or
                0xAC00 <= o <= 0xD7A3 or 0xF900 <= o <= 0xFAFF or
                0xFE30 <= o <= 0xFE4F or 0xFF00 <= o <= 0xFF60 or
                0xFFE0 <= o <= 0xFFE6):
            w += 2
        else:
            w += 1
    return w

def pad_s(s, width):
    return s + " " * max(0, width - disp_width(s))

# ------------------------------------------------------------------ 大数缩写（FR-6）
def pretty_num(n):
    s = str(n)
    if len(s) <= 9:
        return s
    e = len(s) - 1
    if n == 10 ** e:
        return f"10^{e}"
    offset = n - 10 ** e
    if 0 < offset < 10 ** 7:
        return f"10^{e}+{offset}"
    # 其它超长数：截取前 7 位有效数字科学计数法，去尾零（精确截断，无进位溢出）
    head = s[:7].rstrip("0")
    if not head:
        head = "1"
    if len(head) == 1:
        mant_str = head
    else:
        mant_str = head[0] + "." + head[1:]
        if "." in mant_str:
            mant_str = mant_str.rstrip("0").rstrip(".")
    return f"{mant_str}×10^{e}"

# ------------------------------------------------------------------ 输入解析（FR-1）
def parse_input(text):
    s = re.sub(r"[\s,，_]", "", text)
    if not s:
        raise ValueError("empty")
    low = s.lower()
    if low.startswith("0x"):
        return int(s, 16)
    if "^" in s:
        a, b = s.split("^", 1)
        return int(a) ** int(b)
    if low.startswith("1e") or low.startswith("0e"):
        return int(float(s))
    if re.match(r"[+-]?\d+$", s):
        return int(s)
    raise ValueError("bad format")

# ------------------------------------------------------------------ 控制对象（取消 / 暂停 / 限时 / 进度）
class Ctl:
    __slots__ = ("stop_event", "skip_event", "pause_event", "prog",
                 "worker_id", "seq", "k", "t0", "limit_seconds",
                 "paused_total", "last_note", "last_trial")

    def __init__(self, stop_event, skip_event, pause_event, limit_seconds,
                 prog, worker_id, seq, k):
        self.stop_event = stop_event
        self.skip_event = skip_event
        self.pause_event = pause_event
        self.prog = prog
        self.worker_id = worker_id
        self.seq = seq
        self.k = k
        self.t0 = time.perf_counter()
        self.limit_seconds = limit_seconds
        self.paused_total = 0.0
        self.last_note = 0.0
        self.last_trial = 0.0

    def active(self):
        return time.perf_counter() - self.paused_total

    def elapsed(self):
        return self.active() - self.t0

    def timed_out(self):
        return (self.limit_seconds is not None
                and self.limit_seconds > 0
                and self.elapsed() >= self.limit_seconds)

    def check(self):
        sp = self.skip_event
        if sp is not None and sp.is_set():
            raise SkipRequested()
        st = self.stop_event
        if st is not None and st.is_set():
            raise StopRequested()
        pa = self.pause_event
        if pa is not None and pa.is_set():
            s0 = time.perf_counter()
            while pa.is_set():
                if st is not None and st.is_set():
                    raise StopRequested()
                if sp is not None and sp.is_set():
                    raise SkipRequested()
                try:
                    pa.wait(0.2)
                except Exception:
                    time.sleep(0.1)
            self.paused_total += time.perf_counter() - s0
        if (self.limit_seconds is not None and self.limit_seconds > 0
                and self.elapsed() >= self.limit_seconds):
            raise FactorTimeout()

    # ---------------- 消息协议（result_q，供其他 AI 工具参考）：
    #  start    : ("start",   worker_id, seq, k)
    #  curnum   : ("curnum",  worker_id, seq, k, 数的缩写)
    #  trial    : ("trial",   worker_id, seq, k, 文本)            限速 10 条/秒
    #  prog     : ("prog",    worker_id, seq, k, elapsed, 文本)    限速 1 条/秒
    #  milestone: ("milestone", worker_id, seq, k, 第几个质数, 缩写, scanned)
    #  result   : ("result", worker_id, seq, k, rows, pf, scanned,
    #              stop_kind, tail, tail_tag, group_elapsed)
    #  gerror   : ("gerror", worker_id, seq, k, errtext)
    # ---------------- 进度上报方法（带限速，直接进队列，界面侧轮询）
    def _push(self, *payload):
        if self.prog is None:
            return
        try:
            self.prog.put(payload)
        except Exception:
            pass

    def send_start(self):
        self._push("start", self.worker_id, self.seq, self.k)

    def curnum(self, n):
        self._push("curnum", self.worker_id, self.seq, self.k, pretty_num(n))

    def trial(self, idx, total, p):
        now = time.perf_counter()
        if now - self.last_trial < 0.1:
            return
        self.last_trial = now
        self._push("trial", self.worker_id, self.seq, self.k,
                   f"正在试除找小因子：第 {idx}/{total} 个素数（当前试到 {p}）")

    def note(self, text):
        now = time.perf_counter()
        if now - self.last_note < 1.0:
            return
        self.last_note = now
        self._push("prog", self.worker_id, self.seq, self.k,
                   round(self.elapsed(), 1), text)

    def milestone(self, ordinal, n, scanned):
        self._push("milestone", self.worker_id, self.seq, self.k,
                   ordinal, pretty_num(n), scanned)

# ------------------------------------------------------------------ 素性检测（第7节 / FR-21）
MR_DETER = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)

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
        bases = MR_DETER            # 确定性正确
    else:
        nb = max(3, min(36, 24000 // n.bit_length()))
        bases = MR_DETER[:nb]
    nb = len(bases)
    for bi, a in enumerate(bases, 1):
        if ctl is not None:
            ctl.check()
            tb = time.perf_counter()
        x = pow(a, d, n)
        if x != 1 and x != n - 1:
            ok2 = False
            for _ in range(s - 1):
                x = x * x % n
                if x == n - 1:
                    ok2 = True
                    break
            if not ok2:
                return False
        if ctl is not None and bi < nb:
            eta = (time.perf_counter() - tb) * (nb - bi)
            ctl.note(f"正在验证是否为质数（第 {bi}/{nb} 轮，预计还需 ~{eta:.0f} 秒）")
    return True

# ------------------------------------------------------------------ Pollard p-1（第一阶段）
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
        if i % SYNC_PM1 == 0 and ctl is not None:
            ctl.check()
            ctl.note(f"正在用 p-1 方法找因子（进展 {i}/{total}）")
    g = math.gcd(a - 1, n)
    return g if 1 < g < n else None

# ------------------------------------------------------------------ Pollard rho（Brent 变体）
def pollard_brent(n, ctl):
    if n % 2 == 0:
        return 2
    if n % 3 == 0:
        return 3
    steps = 0
    tb = time.perf_counter()
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
                if ctl is not None and steps % SYNC_RHO == 0:
                    ctl.check()
                    rate = steps / max(time.perf_counter() - tb, 1e-9)
                    ctl.note(f"正在用随机算法找因子（已试 {_fmt_wan(steps)} 步 · "
                             f"约 {_fmt_wan(rate)} 步/秒）——这类因子可能要找很久")
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

def _fmt_wan(x):
    if x >= 1e8:
        return f"{x / 1e8:.2f} 亿"
    if x >= 1e4:
        return f"{x / 1e4:.0f} 万"
    return f"{x:.0f}"

# ------------------------------------------------------------------ ECM（Montgomery 仿射坐标，纯 Python 演示级，FR-17）
def _egcd_inv(x, n):
    a, b = x % n, n
    x0, y0 = 1, 0
    while b:
        q = a // b
        a, b = b, a - q * b
        x0, y0 = y0, x0 - q * y0
    if a == 1:
        return x0 % n, None
    return None, a

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
        inv, g = _egcd_inv(2 * y1, n)
        if inv is None:
            raise ValueError(g)
        lam = (3 * x1 * x1 + a) * inv % n
    else:
        inv, g = _egcd_inv((x2 - x1) % n, n)
        if inv is None:
            raise ValueError(g)
        lam = (y2 - y1) * inv % n
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
    if n % 3 == 0:
        return 3
    primes = [p for p in get_primes() if p <= b1]
    for c in range(max_curves):
        if ctl is not None:
            ctl.check()
        a = random.randrange(6, n)
        while True:
            x0 = random.randrange(1, n)
            y0 = random.randrange(1, n)
            b = (y0 * y0 - x0 * x0 * x0 - a * x0) % n
            if (4 * a * a * a + 27 * b * b) % n != 0:
                break
        P = (x0, y0)
        try:
            for pi, p in enumerate(primes):
                if ctl is not None:
                    ctl.check()
                    if c % 4 == 0 or pi == 0:
                        ctl.note(f"正在用椭圆曲线方法找因子（第 {c + 1}/{max_curves}"
                                 f" 条曲线，B1={b1}）")
                k = p
                while k * p <= b1:
                    k *= p
                P = _pt_mul(k, P, a, n)
                if P is None:
                    break
        except ValueError as e:
            g = getattr(e, "args", [None])[0]
            if g is not None and 1 < g < n:
                return g
            continue
    return None

# ------------------------------------------------------------------ 试除 / 拆分 / 分解
def _trial_divide_all(n, ctl):
    """完整试除（<100k 素数）。返回 (factors dict, leftover)。
    leftover>1 表示剩余部分无 <100k 因子（可能是大素数或合数），由调用方继续。"""
    factors = {}
    m = n
    ps = get_primes()
    total = len(ps)
    for i, p in enumerate(ps, 1):
        if p * p > m:
            if m > 1:
                factors[m] = factors.get(m, 0) + 1
                m = 1
            break
        if m % p == 0:
            c = 0
            while m % p == 0:
                m //= p
                c += 1
            factors[p] = c
        if i % SYNC_TRIAL == 0 and ctl is not None:
            ctl.check()
        if ctl is not None:
            ctl.trial(i, total, p)
    return factors, m

def _find_factor_trial(n, ctl):
    """两数相乘模式的第一阶段：返回首个素因子或 None。"""
    ps = get_primes()
    total = len(ps)
    for i, p in enumerate(ps, 1):
        if p * p > n:
            break
        if n % p == 0:
            return p
        if i % SYNC_TRIAL == 0 and ctl is not None:
            ctl.check()
        if ctl is not None:
            ctl.trial(i, total, p)
    return None

def _split_once(m, alg, ctl):
    """按所选算法在尺寸上限内尝试拆分；返回非平凡因子或 None。"""
    if alg in ("auto", "pm1", "deep") and m.bit_length() <= PM1_MAX_BITS:
        d = pollard_pm1(m, ctl)
        if d:
            return d
    if alg in ("auto", "deep", "ecm") and m.bit_length() <= ECM_MAX_BITS:
        d = pollard_ecm(m, ctl)
        if d:
            return d
    if alg in ("auto", "deep", "rho") and m.bit_length() <= RHO_MAX_BITS:
        d = pollard_brent(m, ctl)
        if d:
            return d
    return None

def _render_factors(factors):
    parts = []
    for p in sorted(factors):
        if factors[p] == 1:
            parts.append(pretty_num(p))
        else:
            parts.append(f"{pretty_num(p)}^{factors[p]}")
    return " × ".join(parts)

def factorize_full(n, ctl, alg):
    """完整质因数分解。返回 (factors dict, hard list)。
    hard 元素为 (数, 原因)：'alg' = 所选算法未能分解；'size' = 位数超限只试除。"""
    factors, leftover = _trial_divide_all(n, ctl)
    work = []
    if leftover > 1:
        pr = is_prime(leftover, ctl)
        if pr is True:
            factors[leftover] = factors.get(leftover, 0) + 1
        elif pr is False:
            work.append(leftover)
        else:
            return factors, [(leftover, "size")]
    hard = []
    while work:
        m = work.pop()
        if ctl is not None:
            ctl.check()
        pr = is_prime(m, ctl)
        if pr is True:
            factors[m] = factors.get(m, 0) + 1
            continue
        if pr is False:
            d = _split_once(m, alg, ctl)
            if d is None:
                hard.append((m, "alg"))
                continue
            for ch in (d, m // d):
                cpr = is_prime(ch, ctl)
                if cpr is True:
                    factors[ch] = factors.get(ch, 0) + 1
                elif cpr is False:
                    work.append(ch)
                else:
                    hard.append((ch, "size"))
        else:
            hard.append((m, "size"))
    return factors, hard

def factorize_two(n, ctl, alg):
    """两数相乘（证合数）。返回 (kind, info)：
       ('prime', None) / ('composite', (d1, d2)) / ('unknown', 原因文本)。"""
    pr = is_prime(n, ctl)
    if pr is True:
        return "prime", None
    if pr is None:
        d = _find_factor_trial(n, ctl)
        if d is not None:
            return "composite", (d, n // d)
        return "unknown", "剩余部分可能是质数，也可能是两个大质数的乘积"
    d = _find_factor_trial(n, ctl)
    if d is not None:
        return "composite", (d, n // d)
    d2 = _split_once(n, alg, ctl)
    if d2 is not None:
        return "composite", (d2, n // d2)
    return "unknown", "所选算法未能分解，可换其它算法"

# ------------------------------------------------------------------ 数量级扫描（FR-2）
def scan_magnitude(k, N, mode, ppn, alg, ctl):
    rows = []
    primes_found = 0
    scanned = 0
    start = 10 ** k
    end = min(10 ** (k + 1) - 1, N)
    n = start
    while primes_found < ppn and n <= end:
        ctl.check()
        scanned += 1
        if n == 1:
            rows.append((n, "unit", "1（单位：既不是质数也不是合数）", 0.0, scanned, 0))
            n += 1
            continue
        ctl.curnum(n)
        t0 = ctl.elapsed()
        try:
            if mode == "full":
                factors, hard = factorize_full(n, ctl, alg)
                if hard:
                    if all(h[1] == "alg" for h in hard):
                        extra = "所选算法未能分解，可换其它算法"
                    else:
                        extra = "剩余部分可能是质数，也可能是两个大质数的乘积"
                    rows.append((n, "unknown", extra, ctl.elapsed() - t0, scanned, 0))
                elif len(factors) == 1 and list(factors.values())[0] == 1:
                    primes_found += 1
                    rows.append((n, "prime", "", ctl.elapsed() - t0, scanned, primes_found))
                    ctl.milestone(primes_found, n, scanned)
                else:
                    rows.append((n, "composite", _render_factors(factors),
                                 ctl.elapsed() - t0, scanned, 0))
            else:
                kind, info = factorize_two(n, ctl, alg)
                if kind == "prime":
                    primes_found += 1
                    rows.append((n, "prime", "", ctl.elapsed() - t0, scanned, primes_found))
                    ctl.milestone(primes_found, n, scanned)
                elif kind == "composite":
                    d1, d2 = info
                    rows.append((n, "composite", f"{pretty_num(d1)} × {pretty_num(d2)}",
                                 ctl.elapsed() - t0, scanned, 0))
                else:
                    rows.append((n, "unknown", info, ctl.elapsed() - t0, scanned, 0))
        except FactorTimeout:
            rows.append((n, "timeout", "", ctl.elapsed() - t0, scanned, 0))
            break
        except SkipRequested:
            rows.append((n, "skipped", "", ctl.elapsed() - t0, scanned, 0))
            break
        except StopRequested:
            rows.append((n, "stopped", "", ctl.elapsed() - t0, scanned, 0))
            break
        n += 1
    stop_kind = None
    if ctl.stop_event is not None and ctl.stop_event.is_set():
        stop_kind = "stopped"
    elif ctl.skip_event is not None and ctl.skip_event.is_set():
        stop_kind = "skipped"
    elif ctl.timed_out():
        stop_kind = "timeout"
    elif n > end and primes_found < ppn:
        stop_kind = "till_N"
    return rows, primes_found, scanned, stop_kind

def _do_group_task(seq, k, N, limit, mode, detail, ppn, alg,
                   stop_event, skip_event, pause_event, prog, worker_id):
    ctl = Ctl(stop_event, skip_event, pause_event, limit, prog, worker_id, seq, k)
    ctl.send_start()
    try:
        rows, pf, scanned, stop_kind = scan_magnitude(k, N, mode, ppn, alg, ctl)
    except (FactorTimeout, StopRequested, SkipRequested):
        rows, pf, scanned, stop_kind = [], 0, 0, "skipped"
        if not (skip_event is not None and skip_event.is_set()) and \
           not (stop_event is not None and stop_event.is_set()):
            stop_kind = "stopped"
    except Exception:
        rows, pf, scanned, stop_kind = [], 0, 0, "error"
    group_elapsed = ctl.elapsed()
    if pf >= ppn:
        tail = f"—— 本组共检查 {scanned} 个数，找到前 {ppn} 个质数\n"
        tail_tag = "muted"
    else:
        reasons = {
            "timeout": "【本组限时已到：检查了 {s} 个数，只找到 {p}/{ppn} 个质数"
                       "——可调大「每组限时」或选「不限时」重跑】".format(s=scanned, p=pf, ppn=ppn),
            "skipped": "【已跳过本组：检查了 {s} 个数，找到 {p}/{ppn} 个质数】".format(
                s=scanned, p=pf, ppn=ppn),
            "stopped": "【已停止：检查了 {s} 个数，找到 {p}/{ppn} 个质数】".format(
                s=scanned, p=pf, ppn=ppn),
            "till_N": "【该组到 N 为止：共检查 {s} 个数，找到 {p}/{ppn} 个质数】".format(
                s=scanned, p=pf, ppn=ppn),
            "error": "【本组发生内部错误】",
        }
        reason = reasons.get(stop_kind, "")
        tail = f"—— 本组共检查 {scanned} 个数，找到 {pf}/{ppn} 个质数 {reason}\n"
        tail_tag = "warn"
    return (worker_id, seq, k, rows, pf, scanned, stop_kind, tail, tail_tag,
            group_elapsed)

# ------------------------------------------------------------------ 多进程池（FR-7：1 数量级 = 1 进程）
class PoolRunner:
    def __init__(self, N, limit, mode, workers, detail, ppn, alg):
        self.N = N
        self.limit = limit
        self.mode = mode
        self.workers = max(1, workers)
        self.detail = detail
        self.ppn = max(1, min(20, ppn))
        self.alg = alg
        self.task_q = multiprocessing.Queue()
        self.result_q = multiprocessing.Queue()
        self.stop_event = multiprocessing.Event()
        self.pause_event = multiprocessing.Event()
        self.skip_events = []
        self.pool = []
        self.feeder = None
        self._lock = threading.Lock()
        self._inflight = 0
        self.halt_dispatch = threading.Event()
        self.skip_pending = False
        self.done_dispatch = False

    def start(self):
        for i in range(self.workers):
            sev = multiprocessing.Event()
            sev.clear()
            self.skip_events.append(sev)
            p = multiprocessing.Process(
                target=_worker_main,
                args=(self.task_q, self.result_q, self.stop_event,
                      self.pause_event, sev, i),
                daemon=True)
            p.start()
            self.pool.append(p)
        self.feeder = threading.Thread(target=self._feed_loop, daemon=True)
        self.feeder.start()

    def _feed_loop(self):
        digits = len(str(self.N))
        k = 0
        seq = 0
        while True:
            if self.stop_event.is_set():
                self.done_dispatch = True
                break
            if self.halt_dispatch.is_set():     # 全局跳过期间暂停派发（防级联）
                time.sleep(0.1)
                continue
            if k >= digits:
                self.done_dispatch = True
                time.sleep(0.2)
                continue
            with self._lock:
                if self._inflight >= self.workers * 3:
                    time.sleep(0.05)
                    continue
            self.task_q.put((seq, k, self.N, self.limit, self.mode,
                             self.detail, self.ppn, self.alg))
            with self._lock:
                self._inflight += 1
            seq += 1
            k += 1

    # ---------------- 控制接口（FR-9）
    def pause(self):
        self.pause_event.set()

    def resume(self):
        self.pause_event.clear()

    def is_paused(self):
        return self.pause_event.is_set()

    def skip_current(self):
        """跳过当前正在计算的所有组；期间暂停派发，完成后自动继续。"""
        if self.stop_event.is_set():
            return
        for sev in self.skip_events:
            sev.set()
        with self._lock:
            if self._inflight <= 0:
                self.halt_dispatch.clear()
                self.skip_pending = False
            else:
                self.halt_dispatch.set()
                self.skip_pending = True

    def skip_worker(self, worker_id):
        """只跳过指定进程正在计算的那一组。"""
        if 0 <= worker_id < len(self.skip_events):
            self.skip_events[worker_id].set()

    def on_result_done(self):
        with self._lock:
            self._inflight = max(0, self._inflight - 1)
            clear = self.skip_pending and self._inflight <= 0
            if clear:
                self.skip_pending = False
                self.halt_dispatch.clear()
        return clear

    def stop(self):
        """停止：立即返回，把 join/terminate 放到后台线程，避免卡住 GUI 主线程。
        仍在工作的子进程会以「已停止」结果优雅退出并回传消息。"""
        self.stop_event.set()
        self.halt_dispatch.clear()
        procs = [p for p in self.pool if p.is_alive()]
        if not procs:
            return

        def _reap():
            try:
                for p in procs:
                    try:
                        p.join(timeout=1.5)
                    except Exception:
                        pass
                for p in self.pool:
                    if p.is_alive():
                        try:
                            p.terminate()
                        except Exception:
                            pass
            except Exception:
                pass

        threading.Thread(target=_reap, daemon=True).start()

    close = stop


def _worker_main(task_q, result_q, stop_event, pause_event, skip_event, worker_id):
    while True:
        try:
            task = task_q.get(timeout=0.5)
        except queue_mod.Empty:
            if stop_event.is_set():
                break
            continue
        skip_event.clear()                      # 新任务开始时清除跳过标记（防级联）
        try:
            seq, k, N, limit, mode, detail, ppn, alg = task
        except Exception:
            continue
        try:
            res = _do_group_task(seq, k, N, limit, mode, detail, ppn, alg,
                                 stop_event, skip_event, pause_event,
                                 result_q, worker_id)
            result_q.put(("result",) + res)
        except Exception:
            result_q.put(("gerror", worker_id, seq, k, traceback.format_exc()))

# ------------------------------------------------------------------ 主题（FR-19）
THEMES = {
    "经典简洁": {
        "bg": "#ffffff", "fg": "#000000", "field": "#ffffff", "panel": "#f5f5f5",
        "accent": "#1976d2", "prime": "#c62828", "warn": "#ef6c00",
        "muted": "#757575", "progress": "#2e7d32", "group": "#1565c0",
        "btn_fg": "#ffffff", "sel": "#e3f2fd",
    },
    "科幻炫酷": {
        "bg": "#0d1117", "fg": "#c9d1d9", "field": "#161b22", "panel": "#010409",
        "accent": "#58a6ff", "prime": "#ff7b72", "warn": "#d29922",
        "muted": "#8b949e", "progress": "#3fb950", "group": "#79c0ff",
        "btn_fg": "#0d1117", "sel": "#1f2937",
    },
    "卡通二次元": {
        "bg": "#fff0f5", "fg": "#5d4037", "field": "#ffffff", "panel": "#ffe4e1",
        "accent": "#ec407a", "prime": "#d81b60", "warn": "#ff9800",
        "muted": "#a1887f", "progress": "#66bb6a", "group": "#42a5f5",
        "btn_fg": "#ffffff", "sel": "#fce4ec",
    },
}

ALG_LABELS = {
    "auto": "自动（p-1→rho）【默认】",
    "rho": "仅 Pollard rho",
    "pm1": "仅 p-1",
    "ecm": "ECM 椭圆曲线",
    "deep": "深度 p-1→ECM→rho",
}
ALG_VALUES = list(ALG_LABELS.keys())
ALG_DISPLAY = list(ALG_LABELS.values())

# ------------------------------------------------------------------ 资源监控（FR-14，Win32，纯标准库）
def _win_get_system_times():
    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLow", ctypes.c_uint32), ("dwHigh", ctypes.c_uint32)]
    kt, it, ut = FILETIME(), FILETIME(), FILETIME()
    ok = ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(it),
                                               ctypes.byref(kt),
                                               ctypes.byref(ut))
    if not ok:
        return None
    return ((kt.dwHigh << 32) | kt.dwLow,
            (it.dwHigh << 32) | it.dwLow,
            (ut.dwHigh << 32) | ut.dwLow)

def _win_memory_status():
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_uint32),
                    ("dwMemoryLoad", ctypes.c_uint32),
                    ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64),
                    ("ullTotalPageFile", ctypes.c_uint64),
                    ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64),
                    ("ullAvailVirtual", ctypes.c_uint64),
                    ("ullAvailExtendedVirtual", ctypes.c_uint64)]
    ms = MEMORYSTATUSEX()
    ms.dwLength = ctypes.sizeof(ms)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
        return ms
    return None

class WinResources:
    def __init__(self):
        self.sys_last = None
        self.proc_last = {}
        self._handles = {}

    def cpu_model(self):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            try:
                name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            finally:
                winreg.CloseKey(key)
            return " ".join(name.split())
        except Exception:
            return "未知 CPU"

    def system(self):
        try:
            st = _win_get_system_times()
            ms = _win_memory_status()
            if st is None or ms is None:
                return None
            kernel, idle, user = st
            total = kernel + idle + user
            cpu = 0.0
            if self.sys_last is not None:
                d_total = total - self.sys_last[0]
                d_idle = idle - self.sys_last[1]
                if d_total > 0:
                    cpu = max(0.0, min(100.0, 100.0 * (1.0 - d_idle / d_total)))
            self.sys_last = (kernel, idle, user)
            return (cpu, ms.ullTotalPhys / (1024 ** 3),
                    ms.ullAvailPhys / (1024 ** 3), ms.dwMemoryLoad)
        except Exception:
            return None

    def _open(self, pid):
        h = self._handles.get(pid)
        if h is None:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            try:
                h = ctypes.windll.kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            except Exception:
                h = 0
            self._handles[pid] = h
        return h

    def process(self, pid):
        """返回 (cpu%, mem_mb) 或 None。"""
        try:
            class FILETIME(ctypes.Structure):
                _fields_ = [("dwLow", ctypes.c_uint32), ("dwHigh", ctypes.c_uint32)]
            h = self._open(pid)
            if not h:
                return None
            ct, et, kt, ut = FILETIME(), FILETIME(), FILETIME(), FILETIME()
            if not ctypes.windll.kernel32.GetProcessTimes(
                    h, ctypes.byref(ct), ctypes.byref(et),
                    ctypes.byref(kt), ctypes.byref(ut)):
                return None
            cpu_ns = ((kt.dwHigh << 32) | kt.dwLow) + ((ut.dwHigh << 32) | ut.dwLow)
            now = time.perf_counter()
            prev = self.proc_last.get(pid)
            cpu = 0.0
            if prev is not None and now > prev[1]:
                d_wall = now - prev[1]
                if d_wall > 0:
                    cpu = (cpu_ns - prev[0]) / 1e7 / d_wall * 100.0
            self.proc_last[pid] = (cpu_ns, now)

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_uint32),
                            ("PageFaultCount", ctypes.c_uint32),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]
            pmc = PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(pmc)
            mem_mb = 0.0
            if ctypes.windll.psapi.GetProcessMemoryInfo(
                    h, ctypes.byref(pmc), pmc.cb):
                mem_mb = pmc.WorkingSetSize / (1024 ** 2)
            return (cpu, mem_mb)
        except Exception:
            return None

    def close(self):
        for h in self._handles.values():
            try:
                if h:
                    ctypes.windll.kernel32.CloseHandle(h)
            except Exception:
                pass
        self._handles.clear()

# ------------------------------------------------------------------ GUI
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("大数分解可视化工具 v1.8")
        self.root.geometry("1280x840")
        self.settings_dir = os.path.join(os.getenv("APPDATA", os.path.expanduser("~")),
                                         "大数分解可视化工具")
        self.settings_path = os.path.join(self.settings_dir, "settings.json")
        self.settings = self._load_settings()
        self.theme_name = self.settings.get("theme", "经典简洁")
        if self.theme_name not in THEMES:
            self.theme_name = "经典简洁"
        self.sash_pos = self.settings.get("sash")

        self.var_theme = tk.StringVar(value=self.theme_name)
        self.var_theme.trace_add("write", self.apply_theme)
        self.var_mode = tk.StringVar(value="full")
        self.var_detail = tk.BooleanVar(value=False)
        self.var_ppn = tk.IntVar(value=3)
        self.var_alg = tk.StringVar(value="auto")
        self.var_limit = tk.StringVar(value="10")
        core_n = os.cpu_count() or 2
        self.var_workers = tk.IntVar(value=core_n)
        self.var_status = tk.StringVar(value="就绪")
        self.var_input = tk.StringVar(value="10^50")

        self.N = 0
        self.runner = None
        self._pause_accum = 0.0
        self._pause_start = None
        self._total_groups = 0
        self._completed = 0
        self._total_scanned = 0
        self._total_primes = 0
        self._shortfall_groups = 0
        self._reason_counts = {}
        self._inflight_local = 0
        self._seq_buf = {}
        self._next_seq = 0
        self._gs_rows = {}
        self._result_queue = None
        self._start_wall = 0.0
        self._sum_group_time = 0.0
        self._stopping = False
        self._stop_at = 0.0
        self._finished = False
        self._cached_cpu = {}
        self._winres = WinResources()

        self._build_ui()
        self.apply_theme(self.theme_name)
        self._poll_queue()
        self._poll_proc()
        self._refresh_resource()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<F9>", lambda e: self._toggle_pause())
        self.root.bind("<F10>", lambda e: self._skip_current())
        self.root.bind("<F11>", lambda e: self._stop())

    # ---------------- 设置持久化（FR-19 / FR-22）
    def _load_settings(self):
        try:
            if os.path.exists(self.settings_path):
                with open(self.settings_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_settings(self):
        try:
            os.makedirs(self.settings_dir, exist_ok=True)
            d = dict(self.settings)
            d["theme"] = self.theme_name
            try:
                d["sash"] = int(self._pw.sash_pos(0))
            except Exception:
                pass
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ---------------- 界面构建（第4节）
    def _build_ui(self):
        self.lbl_title = ttk.Label(self.root, text="大数分解-OpenCode-BigPickle",
                                   font=("Microsoft YaHei UI", 12, "bold"))
        self.lbl_title.pack(anchor="w", padx=8, pady=(8, 0))
        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Label(top, text="输入 N:").pack(side="left")
        self.cmb_n = ttk.Combobox(top, textvariable=self.var_input, width=34,
                                  values=["10^12", "10^30", "10^50", "10^100",
                                          "10^200", "10^500", "2^64", "1e30",
                                          "123456789"])
        self.cmb_n.pack(side="left", padx=4)
        ttk.Label(top, text="每组限时(秒):").pack(side="left", padx=(10, 0))
        ttk.Combobox(top, textvariable=self.var_limit, width=6, state="readonly",
                     values=["不限时", "5", "10", "30", "60", "300",
                             "600", "1800", "3600"]).pack(side="left", padx=4)
        ttk.Label(top, text="进程数:").pack(side="left", padx=(10, 0))
        ttk.Spinbox(top, from_=1, to=max(1, 2 * (os.cpu_count() or 2)),
                    textvariable=self.var_workers, width=4).pack(side="left", padx=4)
        ttk.Label(top, text="每组找质数:").pack(side="left", padx=(10, 0))
        ttk.Spinbox(top, from_=1, to=20, textvariable=self.var_ppn,
                    width=3).pack(side="left", padx=4)
        ttk.Label(top, text="算法:").pack(side="left", padx=(10, 0))
        self.cmb_alg = ttk.Combobox(top, textvariable=self.var_alg, width=20,
                                    values=ALG_DISPLAY, state="readonly")
        self.cmb_alg.pack(side="left", padx=4)
        self.cmb_alg.bind("<<ComboboxSelected>>", lambda e: self._sync_alg_var())
        ttk.Label(top, text="主题:").pack(side="left", padx=(10, 0))
        ttk.Combobox(top, textvariable=self.var_theme, width=10,
                     values=list(THEMES.keys()), state="readonly").pack(
            side="left", padx=4)
        self.btn_start = ttk.Button(top, text="开始分解", command=self._start)
        self.btn_start.pack(side="left", padx=(14, 4))
        self.btn_pause = ttk.Button(top, text="暂停", command=self._toggle_pause)
        self.btn_pause.pack(side="left", padx=4)
        ttk.Button(top, text="跳过当前", command=self._skip_current).pack(
            side="left", padx=4)
        self.btn_stop = ttk.Button(top, text="停止", command=self._stop)
        self.btn_stop.pack(side="left", padx=4)

        mid = ttk.Frame(self.root)
        mid.pack(fill="x", padx=8, pady=2)
        ttk.Radiobutton(mid, text="完整质因数分解", variable=self.var_mode,
                        value="full").pack(side="left")
        ttk.Radiobutton(mid, text="两数相乘（证合数）", variable=self.var_mode,
                        value="two").pack(side="left", padx=(8, 0))
        ttk.Checkbutton(mid, text="列出合数明细", variable=self.var_detail).pack(
            side="left", padx=(12, 0))
        self.lbl_hint = ttk.Label(
            mid,
            text="质数标红；异常标橙；辅助信息灰色；实时进度绿色。"
                 "缩写：10^21 / 10^21+7 / 9.09×10^101。输入支持逗号/空格/下划线、"
                 "10^100、2^64、1e50。")
        self.lbl_hint.pack(side="left", padx=(14, 0))

        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        self._pw = ttk.Panedwindow(body, orient="horizontal")
        self._pw.pack(fill="both", expand=True)

        txtframe = ttk.Frame(self._pw)
        self._pw.add(txtframe, weight=4)
        ttk.Label(txtframe, text="结果区（等宽 · 不自动换行 · 组内 = 与「用时」对齐）",
                  ).pack(anchor="w")
        self.txt = tk.Text(txtframe, font=("Consolas", 10), wrap="none",
                           borderwidth=0, highlightthickness=0)
        self.txt.pack(fill="both", expand=True)
        self._hsb = ttk.Scrollbar(txtframe, orient="horizontal",
                                  command=self.txt.xview)
        self._vsb = ttk.Scrollbar(txtframe, orient="vertical",
                                  command=self.txt.yview)
        self.txt.configure(xscrollcommand=self._hsb.set,
                           yscrollcommand=self._vsb.set)
        self._hsb.pack(side="bottom", fill="x")
        self._vsb.pack(side="right", fill="y")

        evtframe = ttk.Frame(self._pw)
        self._pw.add(evtframe, weight=1)
        self.lbl_evt = ttk.Label(evtframe, text="找到质数记录（实时累积）")
        self.lbl_evt.pack(anchor="w")
        self.evt = tk.Text(evtframe, width=72, font=("Consolas", 9), wrap="none",
                           borderwidth=0, highlightthickness=0)
        self.evt.pack(fill="both", expand=True)
        self._ehsb = ttk.Scrollbar(evtframe, orient="horizontal",
                                   command=self.evt.xview)
        self._evsb = ttk.Scrollbar(evtframe, orient="vertical",
                                   command=self.evt.yview)
        self.evt.configure(xscrollcommand=self._ehsb.set,
                           yscrollcommand=self._evsb.set)
        self._ehsb.pack(side="bottom", fill="x")
        self._evsb.pack(side="right", fill="y")

        if isinstance(self.sash_pos, (int, float)) and self.sash_pos > 100:
            self.root.after(200, lambda: self._pw.sash_pos(0, self.sash_pos))

        # 实时进度面板（FR-10 / FR-18）
        pf = ttk.Frame(self.root)
        pf.pack(fill="x", padx=8, pady=(2, 2))
        self.prog_label = tk.Label(pf, font=("Consolas", 9), anchor="w")
        self.prog_label.pack(fill="x")
        self.prog_rows = tk.Frame(pf)
        self.prog_rows.pack(fill="x")
        self._prog_row_widgets = {}
        self._prog_ellipsis = None

        # 资源监控（FR-14）
        rf = ttk.Frame(self.root)
        rf.pack(fill="x", padx=8, pady=2)
        self.res_lbl = ttk.Label(rf, font=("", 9))
        self.res_lbl.pack(anchor="w")

        # 进度条 + 状态栏
        sb = ttk.Frame(self.root)
        sb.pack(fill="x", padx=8, pady=(0, 6))
        self.progressbar = ttk.Progressbar(sb, orient="horizontal",
                                           mode="determinate")
        self.progressbar.pack(fill="x", side="top")
        self.status_lbl = ttk.Label(sb, textvariable=self.var_status, anchor="w")
        self.status_lbl.pack(fill="x", side="top", pady=(3, 0))

        self.cmb_alg.set(ALG_LABELS[self.var_alg.get()])
        self._set_ui_running(False)

    def _sync_alg_var(self):
        idx = self.cmb_alg.current()
        if 0 <= idx < len(ALG_VALUES):
            self.var_alg.set(ALG_VALUES[idx])

    def _set_ui_running(self, running):
        st = "disabled" if running else "normal"
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")
        self.btn_pause.configure(state="normal" if running else "disabled")

    # ---------------- 主题应用（FR-19）
    def apply_theme(self, *_args, force=False):
        name = self.var_theme.get()
        if name not in THEMES:
            name = "经典简洁"
        self.theme_name = name
        t = THEMES[name]
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(".", background=t["bg"], foreground=t["fg"])
        style.configure("TFrame", background=t["bg"])
        style.configure("TLabel", background=t["bg"], foreground=t["fg"])
        style.configure("TButton", background=t["accent"], foreground=t["btn_fg"])
        style.map("TButton",
                  background=[("active", t["accent"]), ("pressed", t["accent"])],
                  foreground=[("active", t["btn_fg"]), ("disabled", "#9e9e9e")])
        style.configure("TCheckbutton", background=t["bg"], foreground=t["fg"])
        style.configure("TRadiobutton", background=t["bg"], foreground=t["fg"])
        style.configure("TCombobox", fieldbackground=t["field"],
                        foreground=t["fg"], selectbackground=t["sel"],
                        selectforeground=t["fg"])
        style.configure("TSpinbox", fieldbackground=t["field"],
                        foreground=t["fg"])
        style.configure("TProgressbar", background=t["accent"],
                        troughcolor=t["panel"])
        self.root.configure(bg=t["bg"])
        for w in (self.txt, self.evt):
            w.configure(bg=t["panel"], fg=t["fg"], insertbackground=t["fg"])
        self.prog_label.configure(bg=t["bg"], fg=t["progress"])
        self.txt.tag_configure("group", foreground=t["group"],
                               font=("Consolas", 10, "bold"))
        self.txt.tag_configure("prime", foreground=t["prime"])
        self.txt.tag_configure("warn", foreground=t["warn"])
        self.txt.tag_configure("muted", foreground=t["muted"])
        self.txt.tag_configure("normal", foreground=t["fg"])
        self.evt.tag_configure("eprime", foreground=t["prime"],
                               font=("Consolas", 9, "bold"))
        self.evt.tag_configure("normal", foreground=t["fg"])
        try:
            self.res_lbl.configure(foreground=t["muted"])
            self.lbl_hint.configure(foreground=t["muted"])
            self.lbl_evt.configure(foreground=t["group"])
        except Exception:
            pass
        if self.settings.get("theme") != name:
            self.settings["theme"] = name
            self._save_settings()

    # ---------------- 开始（FR-1）
    def _start(self):
        if self.runner is not None:
            return
        text = self.cmb_n.get().strip()
        if not text:
            messagebox.showerror("错误", "请输入正整数 N")
            return
        try:
            N = parse_input(text)
        except Exception:
            messagebox.showerror("错误", "无法解析输入。支持直接整数、逗号/空格/"
                                        "下划线、10^100、2^64、1e50。")
            return
        if N <= 0:
            messagebox.showerror("错误", "N 必须为正整数")
            return
        digits = len(str(N))
        if digits > MAX_DIGITS:
            messagebox.showerror("错误", f"输入位数 {digits} 超过上限 {MAX_DIGITS}")
            return
        if digits > CONFIRM_GROUPS:
            if not messagebox.askyesno(
                    "确认",
                    f"组数 {digits} 较多（并行 {self.var_workers.get()} 进程），"
                    "可能耗时很长，确定要继续吗？"):
                return
        limit_txt = self.var_limit.get().strip()
        if limit_txt in ("不限时", "", "0"):
            limit = None
        else:
            try:
                limit = int(limit_txt)
            except Exception:
                messagebox.showerror("错误", "每组限时必须是整数秒或「不限时」")
                return
        mode = "full" if self.var_mode.get() == "full" else "two"
        self._sync_alg_var()
        alg = self.var_alg.get()
        if alg not in ALG_VALUES:
            alg = "auto"

        self.N = N
        self._total_groups = digits
        self._completed = 0
        self._total_scanned = 0
        self._total_primes = 0
        self._shortfall_groups = 0
        self._reason_counts = {}
        self._inflight_local = 0
        self._seq_buf = {}
        self._next_seq = 0
        self._gs_rows = {}
        self._start_wall = time.perf_counter()
        self._sum_group_time = 0.0
        self._stopping = False
        self._stop_at = 0.0
        self._finished = False
        self._pause_accum = 0.0
        self._pause_start = None
        self._prog_row_widgets = {}
        self.txt.delete("1.0", "end")
        self.evt.delete("1.0", "end")
        self._set_ui_running(True)

        self.runner = PoolRunner(N, limit, mode, self.var_workers.get(),
                                 self.var_detail.get(), self.var_ppn.get(), alg)
        self._result_queue = self.runner.result_q
        self.runner.start()
        self.progressbar.configure(maximum=digits, value=0)
        self.prog_label.configure(
            text=f"[正在计算] 共 {self._total_groups} 组 · 并行 {self.runner.workers}"
                 f" 进程（每组占 1 个）")
        self.var_status.set(f"正在计算 · 共 {self._total_groups} 组 · "
                            f"{self.runner.workers} 进程（每组占 1 个）")

    # ---------------- 运行控制（FR-9）
    def _active_now(self):
        n = time.perf_counter() - self._pause_accum
        if self._pause_start is not None:
            n -= (time.perf_counter() - self._pause_start)
        return n

    def _manual_elapsed(self, t0):
        return self._active_now() - t0

    def _toggle_pause(self):
        if not self.runner or self._stopping:
            return
        if self.runner.is_paused():
            self.runner.resume()
            if self._pause_start is not None:
                self._pause_accum += time.perf_counter() - self._pause_start
                self._pause_start = None
            self.btn_pause.configure(text="暂停")
            self.var_status.set("已继续")
        else:
            self.runner.pause()
            self._pause_start = time.perf_counter()
            self.btn_pause.configure(text="继续")
            self.var_status.set("已暂停")

    def _skip_current(self):
        if self.runner:
            self.runner.skip_current()
            self.var_status.set("正在跳过当前…")

    def _stop(self):
        if not self.runner or self._stopping:
            return
        self._stopping = True
        self._stop_at = time.perf_counter()
        self.runner.stop()
        self.var_status.set("正在停止…")

    # ---------------- 消息排空（每 80ms）
    def _poll_queue(self):
        if self.runner and self._result_queue:
            n = 0
            while n < 200:
                try:
                    msg = self._result_queue.get_nowait()
                except queue_mod.Empty:
                    break
                try:
                    self._handle_msg(msg)
                except Exception:
                    pass
                n += 1
            if (self._stopping and not self._finished
                    and ((self._inflight_local <= 0
                          and time.perf_counter() - self._stop_at > 1.0)
                         or time.perf_counter() - self._stop_at > STOP_GRACE)):
                self._finish("已停止")
        self.root.after(80, self._poll_queue)

    def _handle_msg(self, msg):
        kind = msg[0]
        if kind == "start":
            _, wid, seq, k = msg
            self._gs_rows[wid] = {"k": k, "t0": self._active_now(),
                                  "num": "…", "num_t0": self._active_now(),
                                  "trial": "", "prog": ""}
        elif kind == "curnum":
            _, wid, seq, k, ab = msg
            g = self._gs_rows.get(wid)
            if g is not None:
                g["num"] = ab
                g["num_t0"] = self._active_now()
                g["trial"] = ""
                g["prog"] = ""
        elif kind == "trial":
            _, wid, seq, k, text = msg
            g = self._gs_rows.get(wid)
            if g is not None:
                g["trial"] = text
        elif kind == "prog":
            _, wid, seq, k, elapsed, text = msg
            g = self._gs_rows.get(wid)
            if g is not None:
                g["prog"] = text
        elif kind == "milestone":
            _, wid, seq, k, ordinal, ab, scanned = msg
            self.evt.insert("end",
                            f"[数量级 10^{k}] 找到本组第 {ordinal} 个质数：{ab}"
                            f"（已检查 {scanned} 个数）\n", "eprime")
            self.evt.see("end")
        elif kind == "result":
            _, wid, seq, k, rows, pf, scanned, stop_kind, tail, tail_tag, gtime = msg
            if self.runner:
                self.runner.on_result_done()
            self._inflight_local = max(0, self._inflight_local - 1)
            self._seq_buf[seq] = (wid, k, rows, pf, scanned, stop_kind,
                                  tail, tail_tag, gtime)
            self._flush_seq()
            self._gs_rows.pop(wid, None)
        elif kind == "gerror":
            _, wid, seq, k, errtext = msg
            if self.runner:
                self.runner.on_result_done()
            self._inflight_local = max(0, self._inflight_local - 1)
            tail = "—— 本组发生内部错误：" + errtext.strip().splitlines()[-1] + "\n"
            self._seq_buf[seq] = (wid, k, [], 0, 0, "error", tail, "warn", 0.0)
            self._flush_seq()
            self._gs_rows.pop(wid, None)

    def _flush_seq(self):
        while self._next_seq in self._seq_buf:
            wid, k, rows, pf, scanned, stop_kind, tail, tail_tag, gtime = \
                self._seq_buf.pop(self._next_seq)
            self._render_group(k, rows, pf, scanned, stop_kind, tail, tail_tag)
            self._completed += 1
            self._total_scanned += scanned
            self._total_primes += pf
            self._sum_group_time += gtime or 0.0
            if pf < self.var_ppn.get():
                self._shortfall_groups += 1
                self._reason_counts[stop_kind] = \
                    self._reason_counts.get(stop_kind, 0) + 1
            self.progressbar.configure(value=self._completed)
            self._next_seq += 1
            if self._completed >= self._total_groups:
                self._finish("完成")

    # ---------------- 组渲染（FR-4 / FR-5 / FR-12）
    def _render_group(self, k, rows, pf, scanned, stop_kind, tail, tail_tag):
        ppn = self.var_ppn.get()
        mode_txt = "完整质因数分解" if self.var_mode.get() == "full" else "两数相乘（证合数）"
        self.txt.insert("end",
                        f"──── 数量级 10^{k}（{mode_txt}）已检查 {scanned} 个数，"
                        f"找到 {pf}/{ppn} 个质数 ────\n", "group")
        detail = self.var_detail.get()
        lines = []
        for r in rows:
            n, kind, extra, dt, sc, ordinal = r
            if kind == "unit":
                continue
            if kind not in ("prime", "composite", "unknown",
                            "timeout", "stopped", "skipped"):
                continue
            if kind == "composite" and not detail:
                continue
            if kind == "prime":
                right = "质数"
            elif kind == "unknown":
                right = extra
            elif kind == "timeout":
                right = "【本组限时已到】"
            elif kind == "stopped":
                right = "【已停止】"
            elif kind == "skipped":
                right = "【已跳过】"
            else:
                right = extra
            lines.append((pretty_num(n), kind, right, dt))
        for r in rows:
            if r[1] == "unit":
                self.txt.insert("end", "  " + r[2] + "\n", "muted")
        # 组内 = 与「用时」列对齐（FR-5：中文按 2 倍显示宽度）
        name_w = max([disp_width(x[0]) for x in lines], default=0)
        right_w = max([disp_width(x[2]) for x in lines], default=0)
        time_w = max([len(f"用时 {x[3]:.3f}s") for x in lines], default=0)
        tagmap = {"prime": "prime", "unknown": "warn", "timeout": "warn",
                  "stopped": "warn", "skipped": "warn", "composite": "muted"}
        for ab, kind, right, dt in lines:
            lf = "  " + pad_s(ab, name_w) + " = " + pad_s(right, right_w)
            ts = f"用时 {dt:.3f}s"
            self.txt.insert("end", lf + "  " + ts.rjust(time_w) + "\n",
                            tagmap.get(kind, "normal"))
        comp = sum(1 for r in rows if r[1] == "composite")
        unk = sum(1 for r in rows if r[1] == "unknown")
        if comp:
            self.txt.insert("end",
                            f"—— 另扫描合数 {comp} 个，均已按所选模式分解完毕"
                            f"（勾选「列出合数明细」可逐个查看）\n", "muted")
        if unk:
            self.txt.insert("end",
                            f"—— 其中有 {unk} 个数未完整分解（橙色行），余因子*"
                            f"可能是质数，也可能是两个大质数的乘积\n", "warn")
        top3 = sorted(rows, key=lambda r: r[3], reverse=True)[:3]
        if top3:
            items = "、".join(f"{pretty_num(r[0])}（用时 {r[3]:.3f}s）" for r in top3)
            self.txt.insert("end",
                            f"—— 本组耗时最长的 {len(top3)} 个数：{items}\n", "muted")
        self.txt.insert("end", tail, tail_tag)
        self.txt.see("end")

    # ---------------- 进度面板（FR-10 / FR-18）
    def _poll_proc(self):
        self._update_progress_rows()
        if not self._finished:
            self._update_status_line()
        self.root.after(1000, self._poll_proc)

    def _update_status_line(self):
        if self.runner is None or self._finished:
            if self.runner is None:
                self.var_status.set("就绪")
            return
        busy = self._inflight_local
        text = (f"已完成 {self._completed}/{self._total_groups} 组 · 检查中 {busy} 组 ·"
                f" 已检查 {self._total_scanned} 个数 · 找到 {self._total_primes} 个质数 ·"
                f" 忙碌 {busy}/{self.runner.workers} 进程")
        if self.runner.is_paused():
            text += " · 已暂停"
        self.var_status.set(text)

    def _update_progress_rows(self):
        if self.runner is None or self._finished:
            self.prog_label.configure(
                text="[空闲] 等待任务…（输入 N 后点「开始分解」）")
            self._clear_prog_rows()
            return
        rows = []
        for wid in sorted(self._gs_rows):
            g = self._gs_rows[wid]
            k = g["k"]
            age = self._manual_elapsed(g["t0"])
            num_age = self._manual_elapsed(g["num_t0"])
            stage = g["trial"] or g["prog"] or "逐个检查中"
            cpu = "—"
            if wid < len(self.runner.pool):
                pid = self.runner.pool[wid].pid
                if pid is not None and pid in self._cached_cpu:
                    cpu = f"{self._cached_cpu[pid][0]:.0f}%"
            text = (f"数量级 10^{k}（本组已 {age:.0f} 秒）正在算 {g.get('num', '…')}"
                    f"（该数已 {num_age:.0f} 秒，{stage}）· 进程#{wid}（CPU {cpu}）")
            rows.append((wid, text))
        self._set_prog_rows(rows)

    def _set_prog_rows(self, rows):
        if self.runner is not None:
            busy = self._inflight_local
            self.prog_label.configure(
                text=f"[正在计算] 共 {self._total_groups} 组 · 并行 {self.runner.workers}"
                     f" 进程（每组占 1 个）· 忙碌 {busy}/{self.runner.workers}")
        t = THEMES[self.theme_name]
        show = rows[:PROGRESS_MAX_ROWS]
        remain = len(rows) - PROGRESS_MAX_ROWS
        shown_wids = set(wid for wid, _ in show)
        for wid, text in show:
            w = self._prog_row_widgets.get(wid)
            if w is None:
                fr = tk.Frame(self.prog_rows, bg=t["bg"])
                fr.pack(fill="x", anchor="w")
                lbl = tk.Label(fr, text=text, font=("Consolas", 9),
                               anchor="w", bg=t["bg"], fg=t["progress"])
                lbl.pack(side="left")
                but = ttk.Button(fr, text="跳过", width=4,
                                 command=lambda wpi=wid: self._skip_prog_group(wpi))
                but.pack(side="right")
                self._prog_row_widgets[wid] = (fr, lbl)
            else:
                w[1].configure(text=text)
        if remain > 0:
            if self._prog_ellipsis is None:
                self._prog_ellipsis = tk.Label(self.prog_rows,
                                               font=("Consolas", 9), anchor="w",
                                               bg=t["bg"], fg=t["muted"])
                self._prog_ellipsis.pack(fill="x", anchor="w")
            self._prog_ellipsis.configure(text=f"……其余 {remain} 组进行中")
        else:
            if self._prog_ellipsis is not None:
                self._prog_ellipsis.pack_forget()
                self._prog_ellipsis = None
        for wid in list(self._prog_row_widgets):
            if wid not in shown_wids:
                fr, _ = self._prog_row_widgets.pop(wid)
                try:
                    fr.destroy()
                except Exception:
                    pass

    def _clear_prog_rows(self):
        for fr, _ in list(self._prog_row_widgets.values()):
            try:
                fr.destroy()
            except Exception:
                pass
        self._prog_row_widgets = {}
        if self._prog_ellipsis is not None:
            try:
                self._prog_ellipsis.destroy()
            except Exception:
                pass
            self._prog_ellipsis = None

    def _skip_prog_group(self, wid):
        if self.runner:
            self.runner.skip_worker(wid)

    # ---------------- 资源监控（FR-14）
    def _refresh_resource(self):
        try:
            if sys.platform == "win32":
                cpu = self._winres.cpu_model()
                threads = os.cpu_count() or 0
                st = self._winres.system()
                if st:
                    sys_usage, total_gb, avail_gb, mem_pct = st
                    line1 = (f"电脑：{cpu}｜线程 {threads}｜整机占用 {sys_usage:.0f}%｜"
                             f"内存 {total_gb:.1f}GB / 可用 {avail_gb:.1f}GB（{mem_pct}%）")
                else:
                    line1 = f"电脑：{cpu}｜线程 {threads}｜整机占用 —｜内存 —"
                pids = [os.getpid()]
                if self.runner:
                    pids += [p.pid for p in self.runner.pool if p.pid]
                self._cached_cpu = {}
                nproc = 0
                cores = 0.0
                mem_mb = 0.0
                for pid in pids:
                    px = self._winres.process(pid)
                    if px is None:
                        continue
                    c, m = px
                    nproc += 1
                    cores += c / 100.0
                    mem_mb += m
                    self._cached_cpu[pid] = (c, m)
                busy = self._inflight_local
                idle = max(0, (self.runner.workers if self.runner else 0) - busy)
                line2 = (f"本程序：{nproc} 进程｜CPU 折合 {cores:.1f} 核｜"
                         f"内存 {mem_mb:.0f}MB｜忙碌 {busy} / 空闲 {idle}")
                self.res_lbl.configure(text=line1 + "  ｜  " + line2)
            else:
                self.res_lbl.configure(text="资源监控：仅 Windows 支持")
        except Exception:
            self.res_lbl.configure(text="资源监控：读取失败")
        self.root.after(2000, self._refresh_resource)

    # ---------------- 汇总（FR-13）
    def _finish(self, how):
        if self._finished:
            return
        self._finished = True
        wall = time.perf_counter() - self._start_wall
        ppn = self.var_ppn.get()
        mode_txt = ("完整质因数分解" if self.var_mode.get() == "full"
                    else "两数相乘（证合数）")
        limit_txt = self.var_limit.get().strip() or "不限时"
        alg = self.var_alg.get()
        alg_disp = ALG_LABELS.get(alg, alg)
        m = {"timeout": "限时", "skipped": "跳过", "stopped": "停止",
             "till_N": "到 N", "error": "内部错误"}
        reason_parts = "、".join(f"{m.get(kk, kk)} {vv}"
                                 for kk, vv in self._reason_counts.items())
        reason_txt = f"（原因：{reason_parts}）" if reason_parts else ""
        self.txt.insert("end", "\n" + "=" * 60 + "\n", "group")
        self.txt.insert("end", f"【任务汇总】状态：{'已停止' if how == '已停止' else '完成'}\n",
                        "group")
        self.txt.insert("end",
                        f"输入 N：{pretty_num(self.N)}（{self._total_groups} 位）\n"
                        f"分解模式：{mode_txt}｜进程数：{self.runner.workers if self.runner else 0}"
                        f"｜每组限时：{limit_txt}｜每组找质数：{ppn}｜算法：{alg_disp}\n"
                        f"共检查 {self._completed}/{self._total_groups} 个数量级，"
                        f"合计检查 {self._total_scanned} 个数\n"
                        f"找到质数：{self._total_primes} 个\n"
                        f"未找满 {ppn} 个质数的组：{self._shortfall_groups} 个{reason_txt}\n"
                        f"计算总用时：{self._sum_group_time:.1f}s｜"
                        f"全程耗时：{wall:.1f}s\n", "normal")
        self.txt.insert("end", "=" * 60 + "\n", "group")
        self.txt.see("end")
        if self.runner:
            self.runner.close()
        self.runner = None
        self._result_queue = None
        self._stopping = False
        self._pause_accum = 0.0
        self._pause_start = None
        self._set_ui_running(False)
        self._clear_prog_rows()
        self.prog_label.configure(
            text="[空闲] 等待任务…（输入 N 后点「开始分解」）")
        self.progressbar.configure(value=self._total_groups)
        self.var_status.set(f"{how}！共 {self._completed} 组 · 找到 {self._total_primes} 个质数")

    # ---------------- 关闭
    def _on_close(self):
        if self.runner:
            self.runner.close()
        self._save_settings()
        try:
            self._winres.close()
        except Exception:
            pass
        self.root.destroy()

# ------------------------------------------------------------------ 自检（验收清单第 1 条）
def _run_selftest():
    results = []

    def t(name, fn):
        try:
            ok, msg = fn()
            results.append((name, bool(ok), msg))
        except Exception as e:
            results.append((name, False, repr(e)))

    def test_parse():
        assert parse_input("10^12") == 10 ** 12
        assert parse_input("2^64") == 2 ** 64
        assert parse_input("1e3") == 1000
        assert parse_input("1,234,567") == 1234567
        assert parse_input("1_000") == 1000
        assert parse_input("  987 ") == 987
        assert parse_input("0x10") == 16
        return True, "ok"
    t("parse_input", test_parse)

    def test_abbrev():
        assert pretty_num(10 ** 21) == "10^21"
        assert pretty_num(10 ** 21 + 123) == "10^21+123"
        assert pretty_num(10 ** 12 + 4567) == "10^12+4567"
        assert pretty_num(5 * 10 ** 103) == "5×10^103"
        assert pretty_num(123456789) == "123456789"
        assert pretty_num(9999999999) == "9.999999×10^9"
        return True, "ok"
    t("pretty_num", test_abbrev)

    def test_align():
        assert disp_width("中文abc") == 7
        assert disp_width("12") == 2
        assert pad_s("12", 4) == "12  "
        assert pad_s("中文", 4) == "中文"
        return True, "ok"
    t("disp_width/pad_s", test_align)

    def test_prime():
        assert is_prime(2) is True
        assert is_prime(3) is True
        assert is_prime(4) is False
        assert is_prime(1) is False
        assert is_prime(2147483647) is True
        assert is_prime(2147483646) is False
        assert is_prime(1022117) is False          # 1009*1013
        assert is_prime(2 ** 127 - 1) is True
        assert is_prime((10 ** 30 + 7) * (10 ** 30 + 9)) is False
        return True, "ok"
    t("is_prime", test_prime)

    def test_factor_full():
        n = 1234567890
        f, h = factorize_full(n, None, "auto")
        chk = 1
        for p, e in f.items():
            chk *= p ** e
        assert chk == n and not h, f"{f}{h}"
        a, b = 1009, 1013
        f, h = factorize_full(a * b, None, "auto")
        chk = 1
        for p, e in f.items():
            chk *= p ** e
        assert chk == a * b and not h, f"semiprime: {f}{h}"
        f, h = factorize_full(2 ** 64, None, "auto")
        assert f == {2: 64} and not h
        return True, "ok"
    t("factorize_full", test_factor_full)

    def test_factor_two():
        kind, info = factorize_two(1009 * 1013, None, "auto")
        assert kind == "composite" and info
        d1, d2 = info
        assert d1 * d2 == 1009 * 1013
        kind, _ = factorize_two(2 ** 127 - 1, None, "auto")
        assert kind == "prime"
        kind, info = factorize_two(99991 * 99991, None, "auto")
        assert kind == "composite" and info[0] == 99991
        return True, "ok"
    t("factorize_two", test_factor_two)

    def test_scan():
        N = 10 ** 4
        ctl = Ctl(None, None, None, None, None, 0, 0, 0)
        rows, pf, scanned, sk = scan_magnitude(0, N, "full", 3, "auto", ctl)
        kinds = [r[1] for r in rows]
        assert kinds[0] == "unit" and kinds[1] == "prime", kinds
        assert pf == 3 and sk is None, (pf, sk)
        rows, pf, scanned, sk = scan_magnitude(4, N, "two", 3, "auto", ctl)
        assert pf == 0 and sk == "till_N", (pf, sk)
        return True, "ok"
    t("scan_magnitude", test_scan)

    def test_limit():
        random.seed(7)
        for att in range(5):
            def rp(bits):
                while True:
                    p = random.getrandbits(bits) | (1 << (bits - 1)) | 1
                    if is_prime(p):
                        return p
            p, q = rp(58), rp(58)
            ctl = Ctl(None, None, None, 0.3, None, 0, 0, 0)
            try:
                factorize_two(p * q, ctl, "deep")
            except FactorTimeout:
                return True, "ok"
        return False, "5 次尝试均未被限时打断（意外全部很快分解）"
    t("factorize limit", test_limit)

    def test_skip():
        ev = multiprocessing.Event()
        ev.set()
        ctl = Ctl(None, ev, None, None, None, 0, 0, 0)
        try:
            ctl.check()
            return False, "skip not raised"
        except SkipRequested:
            return True, "ok"
    t("skip control", test_skip)

    def test_pipeline():
        r = PoolRunner(10 ** 6, None, "two", 2, False, 3, "auto")
        r.start()
        got = {}
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                msg = r.result_q.get(timeout=1)
            except queue_mod.Empty:
                continue
            if msg[0] == "result":
                _, wid, seq, k, rows, pf, scanned, sk, tail, tag, gt = msg
                got[seq] = k
                r.on_result_done()
            elif msg[0] == "gerror":
                r.on_result_done()
            if len(got) >= 7:
                break
        r.close()
        if len(got) != 7:
            return False, f"只收到 {len(got)}/7 组"
        seqs = sorted(got)
        if seqs != list(range(7)) or [got[s] for s in seqs] != list(range(7)):
            return False, f"序号/组号不连续 {got}"
        return True, "ok"
    t("pipeline 10^6", test_pipeline)

    def test_stop_fast():
        r = PoolRunner(10 ** 19, None, "two", 2, False, 3, "auto")
        r.start()
        time.sleep(0.5)
        t0 = time.perf_counter()
        r.stop()
        elapsed = time.perf_counter() - t0
        if elapsed > 1.0:
            r.close()
            return False, f"stop() 阻塞了 {elapsed:.2f}s（主线程卡死风险）"
        got = set()
        deadline = time.time() + 12
        while time.time() < deadline:
            if not any(p.is_alive() for p in r.pool):
                break
            try:
                msg = r.result_q.get(timeout=0.2)
            except queue_mod.Empty:
                continue
            if msg[0] in ("result", "gerror"):
                r.on_result_done()
                got.add(msg[2])
        r.close()
        alive = [p for p in r.pool if p.is_alive()]
        if alive:
            return False, f"stop()={elapsed:.2f}s 但仍有 {len(alive)} 个进程未退出"
        return True, f"stop()={elapsed:.2f}s，收尾完成（收到 {len(got)} 组结果）"
    t("stop 响应/收尾", test_stop_fast)

    bad = [r for r in results if not r[1]]
    lines = []
    for name, ok, msg in results:
        lines.append(f"[{'PASS' if ok else 'FAIL'}] {name}: {msg}")
    out = "\n".join(lines)
    out += f"\n\n共 {len(results)} 项，失败 {len(bad)} 项。"
    return out, not bad

def _write_selftest_log(text):
    if FROZEN:
        try:
            path = os.path.join(os.path.dirname(sys.executable),
                                "大数分解可视化selftest结果.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return path
        except Exception:
            pass
    return None

def main():
    multiprocessing.freeze_support()
    if "--selftest" in sys.argv:
        text, ok = _run_selftest()
        print(text)
        path = _write_selftest_log(text)
        if path:
            print("已写入:", path)
        sys.exit(0 if ok else 1)
    root = tk.Tk()
    app = App(root)
    if "--detail" in sys.argv:
        app.var_detail.set(True)
    for a in sys.argv:
        if a.startswith("--demo="):
            val = a.split("=", 1)[1]
            app.var_input.set(val)
            app.cmb_n.set(val)
            root.after(400, app._start)
    root.mainloop()

if __name__ == "__main__":
    main()