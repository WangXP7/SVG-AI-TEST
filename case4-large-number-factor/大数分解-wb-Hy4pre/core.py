# -*- coding: utf-8 -*-
"""
大数分解可视化工具 —— 核心层（core）
纯标准库，Python >= 3.8。

包含：
  * 输入解析与大数缩写显示
  * 素性检测（试除 + Miller-Rabin）与因子寻找（p-1 / rho / ECM）
  * 单个数的分析（完整质因数分解 / 两数相乘证合数）
  * 数量级分组扫描（含限时、暂停、跳过、停止）
  * 多进程流水线（1 组 = 1 任务 = 1 进程，结果按序号顺序回传）
  * 结果文本渲染（组内 = 号与用时列对齐）

本模块可被 GUI 层与命令行自检共用，且不依赖 tkinter。
"""
from __future__ import annotations

import math
import queue
import random
import sys
import time
import traceback
from typing import Dict, List, Optional, Sequence, Tuple

# Python 3.11+ 默认限制 int→str 最多 4300 位；本工具支持到 20 万位，必须放开
try:
    sys.set_int_max_str_digits(0)
except AttributeError:
    pass

_LOG10_2 = 0.30102999566398114
_STR_LIMIT_BITS = 20000        # 超过约 6000 位时改用估算，避免 int→str 的二次开销

# ---------------------------------------------------------------- 常量

MAX_INPUT_DIGITS = 200_000     # 输入上限：20 万个十进制位
SMALL_PRIME_LIMIT = 100_000    # 试除素数表上限（10 万以内共 9592 个素数）

MR_MAX_DIGITS = 8_000          # Miller-Rabin 超过此位数自动降级
PM1_MAX_DIGITS = 2_500         # Pollard p-1 适用上限
RHO_MAX_DIGITS = 8_000         # Pollard rho 适用上限
ECM_MAX_DIGITS = 4_000         # ECM 演示级实现适用上限

PM1_B1 = 50_000                # p-1 第一阶段界限
ECM_B1 = 2_000                 # ECM 第一阶段界限
ECM_MAX_CURVES = 30            # ECM 最多曲线数

MAX_DETAIL_ENTRIES = 20_000    # 明细模式每组最多保留的行数
MAX_DUR_ENTRIES = 200_000      # 每组耗时统计最多保留的个数

PRIMES_PER_GROUP_DEFAULT = 3


# ---------------------------------------------------------------- 素数表

def _sieve(limit: int) -> List[int]:
    bs = bytearray([1]) * (limit + 1)
    bs[0:2] = b"\x00\x00"
    for i in range(2, math.isqrt(limit) + 1):
        if bs[i]:
            bs[i * i:: i] = bytearray(len(range(i * i, limit + 1, i)))
    return [i for i in range(2, limit + 1) if bs[i]]


SMALL_PRIMES: List[int] = _sieve(SMALL_PRIME_LIMIT)   # 9592 个
_SMALL_PRIME_SET = frozenset(SMALL_PRIMES)
# 供 MR 使用的确定性底数（n < 3.3e24 时结论确定性正确）
_MR_BASES_12 = SMALL_PRIMES[:12]


def _mr_base_count(bitlen: int) -> int:
    """按位数选择 MR 底数个数（3~36）。"""
    if bitlen <= 128:
        return 36
    if bitlen <= 256:
        return 32
    if bitlen <= 512:
        return 24
    if bitlen <= 1024:
        return 16
    if bitlen <= 2048:
        return 12
    if bitlen <= 4096:
        return 8
    if bitlen <= 8192:
        return 6
    if bitlen <= 16384:
        return 4
    return 3


# ---------------------------------------------------------------- 显示宽度（中文按 2 倍）


def _is_wide(o: int) -> bool:
    return (
        0x1100 <= o <= 0x115F
        or (0x2E80 <= o <= 0xA4CF and o != 0x303F)
        or 0xAC00 <= o <= 0xD7A3
        or 0xF900 <= o <= 0xFAFF
        or 0xFE30 <= o <= 0xFE6F
        or 0xFF00 <= o <= 0xFF60
        or 0xFFE0 <= o <= 0xFFE6
        or 0x20000 <= o <= 0x3FFFD
    )


def dw(s: str) -> int:
    """字符串的显示宽度：CJK 等宽字符按 2 计算。"""
    w = 0
    for ch in s:
        w += 2 if _is_wide(ord(ch)) else 1
    return w


def pad_to(s: str, width: int) -> str:
    return s + " " * max(0, width - dw(s))


# ---------------------------------------------------------------- 大数缩写（FR-6）

def ndigits(n: int) -> int:
    """十进制位数。大数用比特长度定位 + 与 10 的幂比较校正（避免 str() 的二次开销）。"""
    if n < 0:
        n = -n
    bl = n.bit_length()
    if bl <= _STR_LIMIT_BITS:
        return len(str(n))
    e = int(bl * _LOG10_2)            # 候选指数，误差最多 ±1
    if n < _pow10(e):
        e -= 1
    elif n >= _pow10(e + 1):
        e += 1
    return e + 1


_POW10_CACHE: Dict[int, int] = {}


def _pow10(e: int) -> int:
    v = _POW10_CACHE.get(e)
    if v is None:
        if len(_POW10_CACHE) > 8:
            _POW10_CACHE.clear()
        v = 10 ** e
        _POW10_CACHE[e] = v
    return v


def _sci(mant: float, exp10: int) -> str:
    while mant >= 10.0:
        mant /= 10.0
        exp10 += 1
    while mant < 1.0:
        mant *= 10.0
        exp10 -= 1
    m = round(mant, 6)
    if m >= 10.0:                 # 例如 9.9999999 四舍五入后进位
        m /= 10.0
        exp10 += 1
    s = ("%.6f" % m).rstrip("0").rstrip(".")
    return "%s×10^%d" % (s, exp10)


def short(n: int) -> str:
    """大数缩写：<=9 位原样；10^k → 10^k；10^k+小偏移 → 10^k+d；其余 7 位有效数字科学计数法。"""
    if n < 0:
        return "-" + short(-n)
    bl = n.bit_length()
    if bl <= _STR_LIMIT_BITS:                       # ≤ 约 6000 位：精确处理
        s = str(n)
        L = len(s)
        if L <= 9:
            return s
        e = L - 1
        base = _pow10(e)
        if n == base:
            return "10^%d" % e
        d = n - base
        if 0 < d < 10 ** 7:
            return "10^%d+%d" % (e, d)
        d2 = _pow10(L) - n
        if 0 < d2 < 10 ** 7:
            return "10^%d-%d" % (L, d2)
        mant = s[:7].rstrip("0") or s[:7]
        out = mant[0] + ("." + mant[1:] if len(mant) > 1 else "")
        return "%s×10^%d" % (out, e)
    # 超大数：只做线性复杂度的运算（比较 / 除法），避免 int→str 的二次复杂度
    exp10 = ndigits(n) - 1
    p10 = _pow10(exp10)
    if n == p10:
        return "10^%d" % exp10
    d = n - p10
    if 0 < d < 10 ** 7:
        return "10^%d+%d" % (exp10, d)
    return _sci(n / p10, exp10)


def fmt_sec(t: float) -> str:
    """把秒数格式化为易读文本。"""
    if t < 0:
        t = 0.0
    if t < 1:
        return "%.2f 秒" % t
    if t < 60:
        return "%.1f 秒" % t
    if t < 3600:
        return "%d 分 %.0f 秒" % (int(t // 60), t % 60)
    return "%.2f 小时" % (t / 3600.0)


# ---------------------------------------------------------------- 输入解析（FR-1）

class InputError(Exception):
    pass


def parse_number(text: str) -> int:
    """解析正整数：可含逗号/空格/下划线，支持 10^100 / 2^64 / 1e50 写法。"""
    s = (text or "").strip()
    if not s:
        raise InputError("请输入一个正整数（例如 10^50、123456789）。")
    t = s.replace(",", "").replace(" ", "").replace("_", "").replace("　", "")

    if "^" in t or "**" in t:
        sep = "^" if "^" in t else "**"
        parts = t.split(sep)
        if len(parts) != 2:
            raise InputError("幂运算写法只能有一个 %s，例如 10^50。" % sep)
        a, b = parts
        if not (a.isdigit() and b.lstrip("+").isdigit()):
            raise InputError("幂运算写法要求底数与指数都是整数，例如 10^50。")
        base = int(a)
        exp = int(b)
        if base < 2:
            raise InputError("底数必须大于 1。")
        if exp < 0:
            raise InputError("指数不能为负。")
        if exp > MAX_INPUT_DIGITS:
            raise InputError("指数过大：最多 %d 位十进制。" % MAX_INPUT_DIGITS)
        return base ** exp

    if "e" in t.lower():
        try:
            from decimal import Decimal, InvalidOperation
            d = Decimal(t)
        except Exception:
            raise InputError("科学计数法解析失败，例如 1e50。")
        if d < 1:
            raise InputError("请输入大于等于 1 的整数。")
        if d != d.to_integral_value():
            raise InputError("科学计数法的结果必须是整数，例如 1e50（不支持 1.5e50）。")
        n = int(d)
    else:
        if not t.isdigit():
            raise InputError("无法识别的输入：只接受整数、10^100、2^64、1e50 这类写法。")
        n = int(t)

    if n < 1:
        raise InputError("请输入大于等于 1 的正整数。")
    if ndigits(n) > MAX_INPUT_DIGITS:
        raise InputError("数字太大：最多 %d 个十进制位（当前 %d 位）。"
                         % (MAX_INPUT_DIGITS, ndigits(n)))
    return n


def group_count(n: int) -> int:
    """数量级（组）的个数 = N 的十进制位数。"""
    return ndigits(n)


def build_tasks(n: int) -> List[Dict]:
    """把 N 切成若干数量级任务：第 k 组覆盖 [10^k, min(10^(k+1)-1, N)]（k=0 时下界为 1）。"""
    tasks: List[Dict] = []
    p = 1
    D = ndigits(n)
    for k in range(D):
        lo = 1 if k == 0 else p
        nxt = p * 10
        hi = nxt - 1
        if hi > n:
            hi = n
        tasks.append({"seq": k, "k": k, "lo": lo, "hi": hi})
        p = nxt
    return tasks


# ---------------------------------------------------------------- 素性检测与因子寻找

class Abort(Exception):
    """计算被中止（停止 / 跳过 / 限时到）。"""

    def __init__(self, reason: str):
        Exception.__init__(self, reason)
        self.reason = reason


def is_prime(n: int, ctx: Optional["Ctx"] = None) -> Optional[bool]:
    """Miller-Rabin。返回 True/False；位数过大无法判定时返回 None。"""
    if n < 2:
        return False
    if n in _SMALL_PRIME_SET:
        return True
    for p in _SMALL_PRIME_SET:
        if n % p == 0:
            return False
    if ndigits(n) > MR_MAX_DIGITS:
        return None
    d = n - 1
    r = 0
    while d % 2 == 0:
        d //= 2
        r += 1
    if n < 3317044064679887385961981:
        bases = _MR_BASES_12          # 确定性正确
    else:
        bases = SMALL_PRIMES[:_mr_base_count(n.bit_length())]
    for a in bases:
        if ctx is not None:
            ctx.tick()
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def pollard_pm1(n: int, b1: int, ctx: Optional["Ctx"] = None) -> Optional[int]:
    """Pollard p-1 第一阶段。每 512 个素数幂检查一次取消。"""
    if n % 2 == 0:
        return 2
    a = 2
    for i, p in enumerate(SMALL_PRIMES):
        if p > b1:
            break
        pe = p
        while pe * p <= b1:
            pe *= p
        a = pow(a, pe, n)
        if i % 512 == 511:
            if ctx is not None:
                ctx.tick()
            g = math.gcd(a - 1, n)
            if 1 < g < n:
                return g
    g = math.gcd(a - 1, n)
    if 1 < g < n:
        return g
    return None


def pollard_rho_brent(n: int, ctx: Optional["Ctx"] = None) -> Optional[int]:
    """Pollard rho（Brent 变体）。每 128 步检查一次取消。"""
    if n % 2 == 0:
        return 2
    if n % 3 == 0:
        return 3
    for _attempt in range(40):
        y = random.randrange(1, n)
        c = random.randrange(1, n)
        m = 100
        g = 1
        r = 1
        q = 1
        x = 0
        ys = 0
        steps = 0
        while g == 1:
            x = y
            for _ in range(r):
                y = (y * y + c) % n
            k = 0
            while k < r and g == 1:
                ys = y
                lim = min(m, r - k)
                for _ in range(lim):
                    y = (y * y + c) % n
                    q = q * (x - y) % n
                steps += lim
                if steps >= 128:
                    steps = 0
                    if ctx is not None:
                        ctx.tick()
                    g = math.gcd(q, n)
                k += m
            r *= 2
            if r > 1 << 24:
                break
        if g == n:
            g = 1
            while g == 1:
                ys = (ys * ys + c) % n
                g = math.gcd(x - ys, n)
                if ctx is not None:
                    ctx.tick()
        if 1 < g < n:
            return g
    return None


def _madd(xp: int, zp: int, xq: int, zq: int, xd: int, zd: int, n: int):
    s = (xp - zp) * (xq + zq) % n
    t = (xp + zp) * (xq - zq) % n
    return (zd * (s + t) * (s + t) % n, xd * (s - t) * (s - t) % n)


def _mdbl(x: int, z: int, n: int, a24: int):
    s = (x + z) * (x + z) % n
    t = (x - z) * (x - z) % n
    u = (s - t) % n
    return (s * t % n, u * (t + a24 * u) % n)


def _mont_mul(k: int, x: int, z: int, n: int, a24: int):
    """Montgomery 阶梯：返回 k*(x:z)。"""
    x0, z0 = x, z
    x1, z1 = _mdbl(x, z, n, a24)
    for b in bin(k)[3:]:
        if b == "1":
            x0, z0 = _madd(x1, z1, x0, z0, x, z, n)
            x1, z1 = _mdbl(x1, z1, n, a24)
        else:
            x1, z1 = _madd(x0, z0, x1, z1, x, z, n)
            x0, z0 = _mdbl(x0, z0, n, a24)
    return x0, z0


def ecm_factor(n: int, b1: int = ECM_B1, maxcurves: int = ECM_MAX_CURVES,
               ctx: Optional["Ctx"] = None) -> Optional[int]:
    """ECM 椭圆曲线分解（演示级）：Montgomery 曲线仿射/投影坐标，仅第一阶段。"""
    if n % 2 == 0:
        return 2
    if n % 3 == 0:
        return 3
    primes = [p for p in SMALL_PRIMES if p <= b1]
    powers = []
    for p in primes:
        pe = p
        while pe * p <= b1:
            pe *= p
        powers.append(pe)
    for sigma in range(6, 6 + maxcurves):
        if ctx is not None:
            ctx.tick()
        u = (sigma * sigma - 5) % n
        v = (4 * sigma) % n
        u3 = u * u % n * u % n
        v3 = v * v % n * v % n
        den = 16 * u3 % n * v % n
        if den == 0:
            g = math.gcd(n, sigma)
            if 1 < g < n:
                return g
            continue
        num = ((v - u) ** 3 % n) * (3 * u + v) % n
        try:
            a24 = num * pow(den, -1, n) % n
        except ValueError:
            g = math.gcd(den, n)
            if 1 < g < n:
                return g
            continue
        x, z = u3, v3
        try:
            for i, pe in enumerate(powers):
                x, z = _mont_mul(pe, x, z, n, a24)
                z %= n
                if z == 0:
                    break
                if i % 64 == 63:
                    if ctx is not None:
                        ctx.tick()
                    g = math.gcd(z, n)
                    if 1 < g < n:
                        return g
        except ValueError:
            pass
        g = math.gcd(z, n)
        if 1 < g < n:
            return g
    return None


# ---------------------------------------------------------------- 计算上下文（取消 / 暂停 / 进度）

class Ctx:
    """传给算法层的上下文：负责取消检查、暂停冻结、进度上报。"""

    def __init__(self, stop_ev, skip_ev, pause_ev, seq: int, widx: int, q, deadline=None):
        self.stop_ev = stop_ev
        self.skip_ev = skip_ev
        self.pause_ev = pause_ev
        self.seq = seq
        self.widx = widx
        self.q = q
        self.deadline = deadline
        self.paused = 0.0
        self.num_t0 = time.time()
        self.stage = "逐个检查中"
        self._last_trial = 0.0
        self._last_prog = 0.0

    def now(self) -> float:
        return time.time() - self.paused

    def tick(self):
        if self.stop_ev.is_set():
            raise Abort("stop")
        if self.skip_ev.is_set():
            raise Abort("skip")
        if self.deadline is not None and self.now() >= self.deadline:
            raise Abort("timeout")
        if self.pause_ev.is_set():
            t0 = time.time()
            while self.pause_ev.is_set():
                if self.stop_ev.is_set():
                    raise Abort("stop")
                if self.skip_ev.is_set():
                    raise Abort("skip")
                time.sleep(0.04)
            self.paused += time.time() - t0

    def _put(self, msg):
        try:
            self.q.put_nowait(msg)
        except Exception:
            pass

    def progress(self, stage: str):
        self.stage = stage
        t = self.now()
        if t - self._last_prog >= 1.0:
            self._last_prog = t
            self._put(("prog", self.seq, self.widx,
                       round(t - self.num_t0, 1), stage))

    def trial_tick(self, i: int, p: int, total: int):
        if i % 64 == 0:
            self.tick()
        t = self.now()
        if t - self._last_trial >= 0.1:
            self._last_trial = t
            self._put(("trial", self.seq, self.widx,
                       "正在试除找小因子：第 %d/%d 个素数（当前试到 %d）" % (i + 1, total, p)))


def _q_put(q, msg):
    try:
        q.put_nowait(msg)
    except Exception:
        pass


def _trial_count(digits: int) -> int:
    """按位数自适应试除素数个数，避免超大数上试除本身成为瓶颈。"""
    if digits <= 60:
        return len(SMALL_PRIMES)
    if digits <= 200:
        return 6000
    if digits <= 600:
        return 3000
    if digits <= 2000:
        return 1500
    if digits <= 10000:
        return 600
    if digits <= 50000:
        return 200
    return 100


def fmt_factors(facs: Sequence[Tuple[int, int]]) -> str:
    parts = []
    for p, e in facs:
        s = short(p)
        parts.append(s if e == 1 else "%s^%d" % (s, e))
    return " × ".join(parts)


def _merge(facs: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    d: Dict[int, int] = {}
    for p, e in facs:
        d[p] = d.get(p, 0) + e
    return sorted(d.items())


# ---------------------------------------------------------------- 单个数分析

def find_factor(m: int, algo: str, ctx: Ctx) -> Tuple[Optional[int], str]:
    """按所选算法寻找 m 的一个非平凡因子。返回 (因子, 原因)，原因 ∈ {ok, toobig, failed}。"""
    digits = ndigits(m)
    if algo == "pm1":
        plan = ["pm1"]
    elif algo == "rho":
        plan = ["rho"]
    elif algo == "ecm":
        plan = ["ecm"]
    elif algo == "deep":
        plan = ["pm1", "ecm", "rho"]
    else:
        plan = ["pm1", "rho"]        # 自动
    ran_any = False
    toobig = False
    for step in plan:
        if step == "pm1":
            if digits > PM1_MAX_DIGITS:
                toobig = True
                continue
            ctx.progress("正在用 p-1 方法找因子")
            d = pollard_pm1(m, PM1_B1, ctx)
        elif step == "ecm":
            if digits > ECM_MAX_DIGITS:
                toobig = True
                continue
            ctx.progress("正在用椭圆曲线找因子")
            d = ecm_factor(m, ECM_B1, ECM_MAX_CURVES, ctx)
        else:
            if digits > RHO_MAX_DIGITS:
                toobig = True
                continue
            ctx.progress("正在用随机算法找因子")
            d = pollard_rho_brent(m, ctx)
        ran_any = True
        if d and 1 < d < m:
            return d, "ok"
    return None, ("failed" if ran_any else "toobig")


def _factor_rec(m: int, out: List[Tuple[int, int]], algo: str, ctx: Ctx,
                depth: int) -> Tuple[bool, Optional[int]]:
    """递归分解 m，结果累加到 out。返回 (是否完全分解, 未分解余因子)。"""
    ctx.progress("正在验证是否为质数")
    r = is_prime(m, ctx)
    if r is True:
        out.append((m, 1))
        return True, None
    if r is None:
        return False, m
    if depth >= 8:
        return False, m
    d, why = find_factor(m, algo, ctx)
    if not d or d in (1, m):
        return False, m
    ok1, res1 = _factor_rec(d, out, algo, ctx, depth + 1)
    if not ok1:
        return False, res1
    ok2, res2 = _factor_rec(m // d, out, algo, ctx, depth + 1)
    if not ok2:
        return False, res2
    return True, None


def analyze(n: int, mode: str, algo: str, ctx: Ctx) -> Tuple[str, str]:
    """分析单个数。返回 (标签, 显示文本)。
    标签：prime / unit / composite / partial / fail
    """
    if n == 1:
        return ("unit", "单位：既不是质数也不是合数")
    if n == 2 or n == 3:
        return ("prime", "质数")

    facs: List[Tuple[int, int]] = []
    m = n
    tp = SMALL_PRIMES[:_trial_count(ndigits(n))]
    total = len(tp)
    last_p = 2
    ctx.trial_tick(0, 2, total)
    for i, p in enumerate(tp):
        ctx.trial_tick(i, p, total)
        last_p = p
        if p * p > m:
            break                      # 剩下的 m 没有更小的因子 → m 是质数
        if m % p:
            continue
        e = 0
        while m % p == 0:
            m //= p
            e += 1
        facs.append((p, e))
        if m == 1:
            break

    m_is_prime: Optional[bool] = None
    if m > 1:
        if facs and last_p * last_p > m:
            m_is_prime = True
        else:
            ctx.progress("正在验证是否为质数")
            m_is_prime = is_prime(m, ctx)

    if m == 1:
        if mode == "two":
            d = facs[0][0]
            return ("composite", "%s × %s" % (short(d), short(n // d)))
        return ("composite", fmt_factors(_merge(facs)))
    if m_is_prime is True:
        if not facs:
            return ("prime", "质数")
        if mode == "two":
            d = facs[0][0]
            return ("composite", "%s × %s" % (short(d), short(n // d)))
        return ("composite", fmt_factors(_merge(facs + [(m, 1)])))
    if m_is_prime is None:
        tail = ("【剩余部分（%s）位数过大未能判定：可能是质数，也可能是两个大质数的乘积】"
                % short(m))
        if facs:
            return ("partial", fmt_factors(_merge(facs)) + " × " + tail)
        return ("partial", tail)

    # m 确定是合数，但还有未分解的部分
    if mode == "two":
        if facs:
            d = facs[0][0]
            return ("composite", "%s × %s" % (short(d), short(n // d)))
        d, why = find_factor(m, algo, ctx)
        if d and 1 < d < n:
            return ("composite", "%s × %s" % (short(d), short(n // d)))
        if why == "toobig":
            return ("partial",
                    "【位数过大未能判定：可能是质数，也可能是两个大质数的乘积】")
        return ("fail", "【所选算法未能分解，可换其它算法】")

    out: List[Tuple[int, int]] = list(facs)
    ok, residual = _factor_rec(m, out, algo, ctx, 0)
    merged = _merge(out)
    if ok:
        return ("composite", fmt_factors(merged))
    if residual is not None and ndigits(residual) > MR_MAX_DIGITS:
        tag = "partial"
        tail = ("【剩余部分（%s）位数过大未能判定：可能是质数，也可能是两个大质数的乘积】"
                % short(residual))
    else:
        tag = "fail"
        tail = ("【剩余部分（%s）未能分解：可能是质数，也可能是两个大质数的乘积，"
                "或可换其它算法重试】" % short(residual if residual else m))
    s = fmt_factors(merged)
    return (tag, (s + " × " if s else "") + tail)


# ---------------------------------------------------------------- 组扫描（worker 侧）

def run_group(task: Dict, widx: int, stop_ev, pause_ev, skip_ev, q) -> Dict:
    """扫描一个数量级：从 lo 起逐个 +1，直到找满 target 个质数或组终止。"""
    seq = task["seq"]
    k = task["k"]
    lo = task["lo"]
    hi = task["hi"]
    target = task["target"]
    mode = task["mode"]
    tlimit = task["tlimit"]
    algo = task["algo"]
    detail = task["detail"]

    t0 = time.time()
    deadline = (t0 + tlimit) if (tlimit and tlimit > 0) else None
    ctx = Ctx(stop_ev, skip_ev, pause_ev, seq, widx, q, deadline)

    entries: List[Tuple] = []
    durs: List[Tuple[str, float]] = []
    ncomp = 0
    nprime = 0
    status = "done"
    n = lo
    try:
        while n <= hi and nprime < target:
            ctx.tick()
            ctx.num_t0 = ctx.now()
            ctx.stage = "逐个检查中"
            _q_put(q, ("curnum", seq, widx, short(n)))
            try:
                tag, expr = analyze(n, mode, algo, ctx)
            except Abort as e:
                status = {"stop": "stopped", "skip": "skipped",
                          "timeout": "timeout"}[e.reason]
                break
            dt = ctx.now() - ctx.num_t0
            if len(durs) < MAX_DUR_ENTRIES:
                durs.append((short(n), dt))
            if tag == "prime":
                nprime += 1
                entries.append(("prime", short(n), expr, dt))
                _q_put(q, ("milestone", seq, widx,
                           "[数量级 10^%d] 找到本组第 %d 个质数：%s（已检查 %d 个数）"
                           % (k, nprime, short(n), len(durs))))
            elif tag == "unit":
                entries.append(("unit", short(n), expr, dt))
            else:
                ncomp += 1
                if tag in ("partial", "fail", "error"):
                    entries.append((tag, short(n), expr, dt))
                elif detail and len(entries) < MAX_DETAIL_ENTRIES:
                    entries.append((tag, short(n), expr, dt))
            n += 1
        else:
            if nprime < target:
                status = "range_end"
    except Exception:
        entries.append(("error", short(n),
                        "内部错误：" + traceback.format_exc(limit=1).strip().replace("\n", " "),
                        0.0))
        status = "error"

    return {
        "seq": seq, "k": k, "lo_s": short(lo), "hi_s": short(hi),
        "lo": lo, "hi": hi,
        "entries": entries, "durs": durs,
        "ncomp": ncomp, "nprime": nprime, "checked": len(durs),
        "target": target, "status": status,
        "elapsed": max(0.0, ctx.now() - t0), "widx": widx,
    }


def worker_main(widx: int, task_q, result_q, stop_ev, pause_ev, skip_evs):
    """计算进程主循环：不断取任务执行，直到收到 None 哨兵或停止事件。"""
    try:
        while True:
            if stop_ev.is_set():
                break
            try:
                task = task_q.get(timeout=0.25)
            except queue.Empty:
                continue
            except (EOFError, OSError):
                break
            if task is None:
                break
            if stop_ev.is_set():
                break
            skip_evs[widx].clear()          # 关键：新任务开始先清除跳过标记，防级联
            res = run_group(task, widx, stop_ev, pause_ev, skip_evs[widx], result_q)
            try:
                result_q.put(("result", task["seq"], widx, "group", res, None, time.time()))
            except Exception:
                break
    except Exception:
        try:
            result_q.put(("result", -1, widx, "werror",
                          traceback.format_exc(), None, time.time()))
        except Exception:
            pass


# ---------------------------------------------------------------- 结果渲染（FR-4 / FR-5 / FR-12）

_TAGMAP = {"prime": "prime", "unit": "unit", "composite": "comp",
           "partial": "warn", "fail": "warn", "error": "err"}


def render_group(res: Dict, detail: bool) -> List[Tuple[str, str]]:
    """把一组结果渲染为 (文本, 标签) 列表。同一组内 = 号与用时列对齐。"""
    out: List[Tuple[str, str]] = []
    k = res["k"]
    target = res["target"]
    entries = res["entries"]

    out.append(("【数量级 10^%d】（%s ~ %s）" % (k, res["lo_s"], res["hi_s"]), "head"))

    primes = [e for e in entries if e[0] == "prime"]
    units = [e for e in entries if e[0] == "unit"]
    abnormal = [e for e in entries if e[0] in ("partial", "fail", "error")]
    comps = [e for e in entries if e[0] == "composite"]
    shown = primes + units + abnormal + (comps if detail else [])

    if shown:
        w1 = max(dw(e[1]) for e in shown)
        w2 = max(dw(e[2]) for e in shown)
        for e in shown:
            line = "  %s = %s    用时 %s" % (pad_to(e[1], w1), pad_to(e[2], w2),
                                             fmt_sec(e[3]))
            out.append((line, _TAGMAP.get(e[0], "comp")))
    else:
        out.append(("  （本组没有可显示的结果）", "aux"))

    if res.get("ncomp") and not detail:
        out.append(("  —— 另扫描合数 %d 个，均已按所选模式分解完毕"
                    "（勾选「列出合数明细」可逐个查看）" % res["ncomp"], "aux"))

    durs = sorted(res["durs"], key=lambda x: -x[1])[:3]
    if durs:
        seg = "、".join("%s（用时 %s）" % (nm, fmt_sec(dd)) for nm, dd in durs)
        out.append(("  —— 本组耗时最长的 3 个数：%s" % seg, "aux"))

    stat = "  —— 本组共检查 %d 个数，找到前 %d 个质数（本组耗时 %s）" % (
        res["checked"], res["nprime"], fmt_sec(res["elapsed"]))
    st = res["status"]
    if st == "done":
        out.append((stat, "aux"))
    else:
        out.append(("  —— 本组共检查 %d 个数，只找到 %d/%d 个质数（本组耗时 %s）" % (
            res["checked"], res["nprime"], target, fmt_sec(res["elapsed"])), "warn"))
        if st == "timeout":
            out.append(("  【本组限时已到：检查了 %d 个数，只找到 %d/%d 个质数"
                        "——可调大「每组限时」或选「不限时」重跑】"
                        % (res["checked"], res["nprime"], target), "warn"))
        elif st == "skipped":
            out.append(("  【已跳过本组：检查了 %d 个数，找到 %d/%d 个质数】"
                        % (res["checked"], res["nprime"], target), "warn"))
        elif st == "stopped":
            out.append(("  【已停止：本组检查了 %d 个数，找到 %d/%d 个质数】"
                        % (res["checked"], res["nprime"], target), "warn"))
        elif st == "range_end":
            out.append(("  【本数量级已扫到末尾（受 N 大小限制），只找到 %d/%d 个质数】"
                        % (res["nprime"], target), "warn"))
        elif st == "error":
            out.append(("  【本组计算出现异常，详见上方错误行】", "warn"))
    return out


def render_summary(summary: Dict) -> List[Tuple[str, str]]:
    """任务汇总（FR-13）。"""
    L: List[Tuple[str, str]] = []
    L.append(("=" * 64, "aux"))
    L.append(("【任务汇总】", "head"))
    L.append(("  分解模式：%s ｜ 找因子算法：%s" % (summary["mode_s"], summary["algo_s"]), "aux"))
    L.append(("  并行进程数：%d ｜ 每组限时：%s ｜ 每组找质数：%d 个"
              % (summary["nproc"], summary["tlimit_s"], summary["target"]), "aux"))
    L.append(("  检查了 %d 个数量级、共 %d 个数，找到质数 %d 个"
              % (summary["ngroups"], summary["checked"], summary["nprimes"]), "aux"))
    if summary["incomplete"]:
        L.append(("  有 %d 个数量级未找满 %d 个质数（原因：限时到 / 被跳过 / 被停止 / "
                  "已扫到 N 的末尾）" % (summary["incomplete"], summary["target"]), "warn"))
    else:
        L.append(("  全部数量级都找满了 %d 个质数" % summary["target"], "aux"))
    L.append(("  分解计算总用时 %s ｜ 全程耗时 %s"
              % (fmt_sec(summary["calc"]), fmt_sec(summary["wall"])), "aux"))
    if summary.get("stopped"):
        L.append(("  【本次任务已停止】", "warn"))
    return L


# ---------------------------------------------------------------- 多进程流水线

class Pipeline:
    """1 个数量级 = 1 个任务 = 1 个计算进程；滑动窗口提交；结果按序号顺序回传。"""

    def __init__(self, tasks: List[Dict], nproc: int, target: int, mode: str,
                 tlimit: float, algo: str, detail: bool):
        self.tasks = tasks
        self.nproc = max(1, nproc)
        self.target = target
        self.mode = mode
        self.tlimit = tlimit
        self.algo = algo
        self.detail = detail
        self.total = len(tasks)

        import multiprocessing as mp
        self._mp = mp
        self.ctxm = mp.get_context("spawn")
        self.task_q = self.ctxm.Queue()
        self.result_q = self.ctxm.Queue()
        self.stop_ev = self.ctxm.Event()
        self.pause_ev = self.ctxm.Event()
        self.skip_evs = [self.ctxm.Event() for _ in range(self.nproc)]
        self.procs: List = []
        self.feeder: Optional[object] = None

        import threading
        self._lock = threading.Lock()
        self._inflight = 0
        self._skip_hold = threading.Event()
        self._next = 0
        self._stopped = False
        self.active: Dict[int, int] = {}      # seq -> widx
        self.buffer: Dict[int, Dict] = {}
        self.next_seq = 0                     # 下一个待顺序输出的组序号
        self.done_count = 0
        self._skip_targets: Dict[int, int] = {}
        self._skip_pending: set = set()
        self._stopping = False

    # ---- 生命周期
    def start(self):
        for i in range(self.nproc):
            p = self.ctxm.Process(
                target=worker_main,
                args=(i, self.task_q, self.result_q, self.stop_ev,
                      self.pause_ev, self.skip_evs),
                daemon=True)
            p.start()
            self.procs.append(p)
        import threading
        self.feeder = threading.Thread(target=self._feed, daemon=True)
        self.feeder.start()

    def _feed(self):
        n = len(self.tasks)
        i = 0
        while i < n:
            if self.stop_ev.is_set():
                break
            while self._skip_hold.is_set():
                time.sleep(0.05)
                if self.stop_ev.is_set():
                    break
            if self.stop_ev.is_set():
                break
            with self._lock:
                busy = self._inflight
            if busy >= self.nproc + 2:
                time.sleep(0.05)
                continue
            task = dict(self.tasks[i])
            task.update({"target": self.target, "mode": self.mode,
                         "tlimit": self.tlimit, "algo": self.algo,
                         "detail": self.detail})
            self.task_q.put(task)
            with self._lock:
                self._inflight += 1
            i += 1
        for _ in range(self.nproc):
            try:
                self.task_q.put(None)
            except Exception:
                pass

    def pump(self) -> List[Tuple]:
        """非阻塞排空消息队列，返回消息列表。"""
        msgs: List[Tuple] = []
        while True:
            try:
                m = self.result_q.get_nowait()
            except queue.Empty:
                break
            except (EOFError, OSError):
                break
            tag = m[0]
            if tag == "result":
                with self._lock:
                    self._inflight = max(0, self._inflight - 1)
                seq, widx, kind = m[1], m[2], m[3]
                self.active.pop(seq, None)
                if kind == "group":
                    self.buffer[seq] = m[4]
                    # 若这是被指定跳过的组，清除该进程的跳过标记
                    if self._skip_targets.get(widx) == seq:
                        self.skip_evs[widx].clear()
                        self._skip_targets.pop(widx, None)
                    if self._skip_pending and seq in self._skip_pending:
                        self._skip_pending.discard(seq)
                        if not self._skip_pending:
                            for ev in self.skip_evs:
                                ev.clear()
                            self._skip_hold.clear()
                elif kind == "werror":
                    msgs.append(("error", -1, widx, m[4], None, None))
                self.done_count += 1
            else:
                if tag in ("curnum", "trial", "prog", "milestone"):
                    self.active[m[1]] = m[2]
            msgs.append(m)
        return msgs

    def ordered_results(self) -> List[Dict]:
        """按序号顺序取出已就绪的组结果。"""
        out = []
        while self.next_seq in self.buffer:
            out.append(self.buffer.pop(self.next_seq))
            self.next_seq += 1
        return out

    def flush_all(self) -> List[Dict]:
        """停止后收尾：按序号升序取出缓冲区里的全部结果（允许中间有缺口）。"""
        out = []
        for seq in sorted(self.buffer):
            out.append(self.buffer.pop(seq))
            self.next_seq = max(self.next_seq, seq + 1)
        return out

    # ---- 控制
    def pause(self):
        self.pause_ev.set()

    def resume(self):
        self.pause_ev.clear()

    @property
    def paused(self) -> bool:
        return self.pause_ev.is_set()

    def skip_all(self):
        """放弃当前正在计算的所有组；期间暂停派发防止级联跳过。"""
        if not self.active:
            return
        self._skip_pending = set(self.active.keys())
        for seq, widx in self.active.items():
            self._skip_targets[widx] = seq
            self.skip_evs[widx].set()
        self._skip_hold.set()

    def skip_seq(self, seq: int):
        widx = self.active.get(seq)
        if widx is None:
            return
        self._skip_targets[widx] = seq
        self.skip_evs[widx].set()

    def stop(self):
        self._stopping = True
        self.stop_ev.set()
        for ev in self.skip_evs:
            try:
                ev.clear()
            except Exception:
                pass
        self._skip_hold.clear()
        self.active.clear()      # 停止后不再显示进行中的组（进程可能来不及回报）
        self._skip_targets.clear()
        self._skip_pending.clear()
        try:
            for _ in range(self.nproc):
                self.task_q.put(None)
        except Exception:
            pass

    @property
    def stopping(self) -> bool:
        return self._stopping

    def is_alive(self) -> bool:
        return any(p.is_alive() for p in self.procs)

    def terminate_all(self, timeout: float = 2.0):
        self.stop()
        t0 = time.time()
        for p in self.procs:
            try:
                p.join(max(0.1, timeout - (time.time() - t0)))
            except Exception:
                pass
        for p in self.procs:
            if p.is_alive():
                try:
                    p.terminate()
                except Exception:
                    pass

    def child_pids(self) -> List[int]:
        return [p.pid for p in self.procs if p.pid]
