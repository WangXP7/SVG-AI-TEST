#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大数分解可视化工具 v1.8（千问办公 独立实现）
依据《产品需求设计》v1.8 全部功能需求（FR-1 ~ FR-22）实现。
纯 Python 标准库，无第三方依赖；Windows 优先；PyInstaller onefile/windowed 可打包。

用法：
  双击 / python factor_gui.py                 打开图形界面
  python factor_gui.py --selftest             自检（验收清单第 1 条）
  python factor_gui.py --demo=10^12           打开界面并自动开始分解 10^12
  python factor_gui.py --cli=10^12            无界面命令行跑一遍（紧凑显示）
  --detail                                    启动即勾选「列出合数明细」
"""
import sys


def _lift_int_limit():
    # 允许 20 万位级别的大整数与字符串互转（Python 3.11+ 默认 4300 位上限）
    try:
        sys.set_int_max_str_digits(0)
    except (AttributeError, ValueError):
        pass


_lift_int_limit()

import os
import re
import json
import math
import time
import queue
import random
import platform
import threading
import traceback
import argparse
import unicodedata
import multiprocessing as mp

APP_TITLE = '大数分解可视化工具 v1.8'
PRIMES_LIMIT = 100000          # 试除素数表上限（10 万以内共 9592 个素数）
MAX_DIGITS = 200000            # 输入位数上限
MAX_MR_DIGITS = 8000           # Miller-Rabin / rho 位数上限（超出自动降级）
MAX_PM1_DIGITS = 2500          # p-1 位数上限
MAX_ECM_DIGITS = 1000          # ECM（演示级）位数上限
DEFAULT_PER_GROUP = 3          # 每组找质数个数默认值
SETTING_FILE = '分解工具设置-千问办公-Qwen3.8-Max.json'
ALGO_AUTO = '自动（p-1→rho）'
ALGO_RHO = '仅 Pollard rho'
ALGO_PM1 = '仅 p-1'
ALGO_ECM = 'ECM 椭圆曲线'
ALGO_DEEP = '深度 p-1→ECM→rho'
ALGOS = [ALGO_AUTO, ALGO_RHO, ALGO_PM1, ALGO_ECM, ALGO_DEEP]
ALGO_ORDER = {
    ALGO_AUTO: ('pm1', 'rho'),
    ALGO_RHO: ('rho',),
    ALGO_PM1: ('pm1',),
    ALGO_ECM: ('ecm',),
    ALGO_DEEP: ('pm1', 'ecm', 'rho'),
}
STAGE_NAME = {
    'trial': '正在试除找小因子',
    'mr': '正在验证是否为质数',
    'pm1': '正在用 p-1 方法找因子',
    'rho': '正在用随机算法找因子',
    'ecm': '正在用椭圆曲线找因子',
}
LIMIT_CHOICES = ['不限时', '5 秒', '10 秒', '30 秒', '60 秒', '300 秒', '600 秒', '1800 秒', '3600 秒']
PRESETS = ['10^12', '10^30', '10^50', '10^100', '10^200', '10^500', '2^64', '1e30', '123456789']
DEFAULT_INPUT = '10^50'


class CancelStop(Exception):
    pass


class CancelSkip(Exception):
    pass


class CancelTimeout(Exception):
    pass


# ---------------------------------------------------------------- 基础工具

def sieve(n=PRIMES_LIMIT):
    """埃氏筛，返回 ≤n 的全部素数列表（10 万以内共 9592 个）。"""
    bs = bytearray([1]) * (n + 1)
    bs[0:2] = b'\x00\x00'
    for i in range(2, int(n ** 0.5) + 1):
        if bs[i]:
            bs[i * i::i] = bytearray(len(bs[i * i::i]))
    return [i for i in range(n + 1) if bs[i]]


PRIMES = sieve()
assert len(PRIMES) == 9592, '素数表数量应为 9592'
DET_LIMIT = 3317044064679887385961981   # < 3.3e24 时 12 个固定底数可确定性判素


def digit_len(n):
    return len(str(n))


def abbrev(n, hint_k=None):
    """大数缩写（FR-6）。≤9 位原样显示；hint_k：若已知 n 接近 10^k，可避免大数转字符串。"""
    if n < 0:
        return '-' + abbrev(-n)
    if n < 10 ** 9:                       # ≤9 位照原样
        return str(n)
    if hint_k is not None:
        off = n - 10 ** hint_k
        if off == 0:
            return '10^%d' % hint_k
        if 0 < off < 10 ** 7:
            return '10^%d+%d' % (hint_k, off)
    s = str(n)
    L = len(s)
    if s[0] == '1' and s.count('0') == L - 1:
        return '10^%d' % (L - 1)
    if s[0] == '1':
        off = n - 10 ** (L - 1)
        if off < 10 ** 7:
            return '10^%d+%d' % (L - 1, off)
    head, tail = s[0], s[1:7].rstrip('0')
    mtxt = head + ('.' + tail if tail else '')
    return '%s×10^%d' % (mtxt, L - 1)


def dw(s):
    """显示宽度：中文（全角）按 2 计。"""
    w = 0
    for ch in s:
        w += 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
    return w


def pad(s, width):
    return s + ' ' * max(0, width - dw(s))


def parse_input(text):
    """解析输入（FR-1）：整数（可含逗号/空格/下划线）、a^b、科学计数法。"""
    t = (text or '').strip().replace(',', '').replace('，', '').replace(' ', '').replace('_', '')
    if not t:
        raise ValueError('输入为空')
    m = re.fullmatch(r'(\d+)\^(\d+)', t)
    if m:
        base, exp = int(m.group(1)), int(m.group(2))
        if base < 1:
            raise ValueError('底数必须 ≥1')
        if exp > 300000:
            raise ValueError('幂指数过大')
        n = base ** exp
    else:
        m = re.fullmatch(r'(\d+)(?:\.(\d+))?[eE]\+?(\d+)', t)
        if m:
            ip, fp, ex = m.group(1), m.group(2) or '', int(m.group(3))
            if ex < len(fp):
                raise ValueError('科学计数法结果不是整数')
            digits = ip + fp
            n = int(digits) * 10 ** (ex - len(fp))
        elif re.fullmatch(r'\d+', t):
            n = int(t)
        else:
            raise ValueError('无法识别的输入，支持：大整数、10^100、2^64、1e50')
    if n <= 0:
        raise ValueError('请输入正整数')
    if digit_len(n) > MAX_DIGITS:
        raise ValueError('超过 %d 万位上限' % (MAX_DIGITS // 10000))
    return n


# ---------------------------------------------------------------- 数论算法

def mr_bases(n):
    if n < DET_LIMIT:
        return PRIMES[:12]          # 固定 12 底数：确定性正确
    d = digit_len(n)
    cnt = max(3, min(36, 36 - (d - 25) // 25))
    return PRIMES[:cnt]


def is_prime(n, cancel=None):
    """Miller-Rabin。n<3.3e24 确定性；更大按位数 3~36 个底数（概率性，误差 ~4^-底数数）。"""
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d = n - 1
    r = 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for a in mr_bases(n):
        if cancel is not None:
            cancel()                # FR-21：MR 每个底数一个取消检查点
        x = pow(a % n, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = (x * x) % n
            if x == n - 1:
                break
        else:
            return False
    return True


def pm1_factor(n, cancel=None, B1=50000):
    """Pollard p-1 第一阶段（FR/§7：B1=50000，仅对 ≤2500 位）。"""
    if digit_len(n) > MAX_PM1_DIGITS:
        return None
    a = 2
    steps = 0
    for p in PRIMES:
        if p > B1:
            break
        e = p
        while e * p <= B1:
            e *= p
        a = pow(a, e, n)
        steps += 1
        if steps % 512 == 0:        # FR-21：每 512 个素数幂一个检查点
            if cancel is not None:
                cancel()
            g = math.gcd(a - 1, n)
            if 1 < g < n:
                return g
    g = math.gcd(a - 1, n)
    return g if 1 < g < n else None


def rho_factor(n, cancel=None, attempts=16):
    """Pollard rho（Brent 变体，随机；仅对 ≤8000 位）。"""
    if digit_len(n) > MAX_MR_DIGITS:
        return None
    if n % 2 == 0:
        return 2
    for _ in range(attempts):
        c = random.randrange(1, n - 1)
        y = random.randrange(2, n - 1)
        m = 128                     # FR-21：每 128 步一批做取消检查
        g = r = q = 1
        x = ys = None
        while g == 1:
            x = y
            for _ in range(r):
                y = (y * y + c) % n
            k = 0
            q = 1
            while k < r and g == 1:
                ys = y
                for _ in range(min(m, r - k)):
                    y = (y * y + c) % n
                    q = (q * abs(x - y)) % n
                if cancel is not None:
                    cancel()
                g = math.gcd(q, n)
                k += m
            r *= 2
            if r > (1 << 24) and g == 1:
                break               # 单次尝试过长则换个随机参数重来
        if g == n:
            g = 1
            while g == 1:
                ys = (ys * ys + c) % n
                g = math.gcd(abs(x - ys), n)
        if 1 < g < n:
            return g
    return None


class _EcmFactor(Exception):
    def __init__(self, g):
        self.g = g


class _EcmCurveFail(Exception):
    pass


def _ecm_inv(a, n):
    a %= n
    g = math.gcd(a, n)
    if g == 1:
        return pow(a, -1, n)
    if g == n:
        raise _EcmCurveFail()
    raise _EcmFactor(g)


def _ecm_double(P, A, n):
    if P is None:
        return None
    x1, y1 = P
    if y1 == 0:
        return None
    lam = ((3 * x1 * x1 + 2 * A * x1 + 1) % n) * _ecm_inv(2 * y1, n) % n
    x3 = (lam * lam - A - 2 * x1) % n
    y3 = (lam * (x1 - x3) - y1) % n
    return (x3, y3)


def _ecm_add(P, Q, A, n):
    if P is None:
        return Q
    if Q is None:
        return P
    x1, y1 = P
    x2, y2 = Q
    if x1 == x2:
        if (y1 + y2) % n == 0:
            return None
        return _ecm_double(P, A, n)
    lam = ((y2 - y1) % n) * _ecm_inv((x2 - x1) % n, n) % n
    x3 = (lam * lam - A - x1 - x2) % n
    y3 = (lam * (x1 - x3) - y1) % n
    return (x3, y3)


def _ecm_mul(P, e, A, n):
    R = None
    Q = P
    while e:
        if e & 1:
            R = _ecm_add(R, Q, A, n)
        Q = _ecm_double(Q, A, n)
        e >>= 1
    return R


def ecm_factor(n, cancel=None, B1=2000, max_curves=30):
    """ECM 演示级实现（FR-17）：Montgomery 曲线 By^2=x^3+Ax^2+x（B=1）仿射坐标，
    第一阶段 B1=2000，≤30 条曲线；靠模逆失败（gcd>1）发现因子。"""
    if digit_len(n) > MAX_ECM_DIGITS:
        return None
    for _ in range(max_curves):
        try:
            sigma = random.randrange(6, max(8, n - 1))
            u = (sigma * sigma - 5) % n
            v = (4 * sigma) % n
            if u == 0 or v == 0:
                continue
            num = pow((v - u) % n, 3, n) * ((3 * u + v) % n) % n
            den = 4 * pow(u, 3, n) % n * v % n
            A = (num * _ecm_inv(den, n) - 2) % n
            P = (pow(u, 3, n), pow(v, 3, n))
            cnt = 0
            for p in PRIMES:
                if p > B1:
                    break
                e = p
                while e * p <= B1:
                    e *= p
                P = _ecm_mul(P, e, A, n)
                if P is None:
                    break
                cnt += 1
                if cnt % 16 == 0 and cancel is not None:
                    cancel()
            if P is not None:
                g = math.gcd(P[0], n)
                if 1 < g < n:
                    return g
        except _EcmFactor as f:
            if 1 < f.g < n:
                return f.g
        except _EcmCurveFail:
            continue
    return None


def find_factor(m, algo, ctx=None):
    """按所选算法顺序（FR-17）寻找任一非平凡因子；找不到返回 None。"""
    for stage in ALGO_ORDER.get(algo, ('pm1', 'rho')):
        if ctx is not None:
            ctx.stage(STAGE_NAME[stage])
        if stage == 'pm1':
            d = pm1_factor(m, ctx.cancel if ctx else None)
        elif stage == 'rho':
            d = rho_factor(m, ctx.cancel if ctx else None)
        else:
            d = ecm_factor(m, ctx.cancel if ctx else None)
        if d:
            return d
    return None


def fmt_factor(p):
    return str(p) if p < 10 ** 7 else abbrev(p)


def fmt_factors(factors):
    parts = []
    for p, e in sorted(factors):
        parts.append(fmt_factor(p) + ('^%d' % e if e > 1 else ''))
    return ' × '.join(parts)


# ---------------------------------------------------------------- 单数分析

def analyze_number(n, k, cfg, ctx):
    """分析单个数 n（FR-3/FR-21）。返回 dict(style,left,right,dt,prime)。"""
    mode = cfg['mode']
    algo = cfg['algo']
    t0 = ctx.now()
    left = abbrev(n, k)

    def out(style, right, prime=False):
        return {'style': style, 'left': left, 'right': right, 'dt': ctx.now() - t0, 'prime': prime}

    if n == 1:
        return out('unit', '（单位：既不是质数也不是合数）')

    big = digit_len(n) > MAX_MR_DIGITS          # 位数过大 → 只做试除（FR-21）
    factors = []
    had_factor = False
    m = n
    proved = False
    ctx.stage(STAGE_NAME['trial'])
    ntot = len(PRIMES)
    for i, p in enumerate(PRIMES):
        if i % 4096 == 0:                       # FR-21：试除每 4096 个素数一个检查点
            ctx.cancel()
        if i % 256 == 0:
            ctx.emit_trial('第 %d/%d 个素数（当前试到 %d）' % (i + 1, ntot, p))
        if p * p > m:
            proved = True
            break
        if m % p == 0:
            e = 0
            while m % p == 0:
                m //= p
                e += 1
            factors.append((p, e))
            had_factor = True
            if mode == 'two':                   # 两数相乘（证合数）：第一个非平凡因子即可
                return out('plain', '%s × %s' % (abbrev(p), abbrev(n // p)))
    if proved:
        if m > 1:
            factors.append((m, 1))
        if not had_factor:
            return out('prime', '质数', True)
        return out('plain', fmt_factors(factors))
    if big:
        if factors:
            right = fmt_factors(factors) + ' × 剩余大数（位数过大未深入判定：剩余部分可能是质数，也可能是两个大质数的乘积）'
        else:
            right = '（位数过大：只做小因子试除。剩余部分可能是质数，也可能是两个大质数的乘积）'
        return out('warn', right)
    ctx.stage(STAGE_NAME['mr'])
    if is_prime(m, ctx.cancel):
        factors.append((m, 1))
        if not had_factor:
            return out('prime', '质数', True)
        return out('plain', fmt_factors(factors))
    d = find_factor(m, algo, ctx)
    if d is None:
        if factors:
            right = fmt_factors(factors) + ' × 剩余大数（所选算法未能分解，可换其它算法）'
        else:
            right = '（所选算法未能分解，可换其它算法）'
        return out('warn', right)
    if mode == 'two':
        return out('plain', '%s × %s' % (abbrev(d), abbrev(n // d)))
    # 完整质因数分解：对 d 与 m/d 递归
    acc = []
    ok = _deep_factor(d, algo, ctx, acc)
    ok = _deep_factor(m // d, algo, ctx, acc) and ok
    if not ok:
        if factors:
            right = fmt_factors(factors) + ' × 剩余大数（部分因子未能继续分解：可能是质数，也可能是两个大质数的乘积）'
        else:
            right = '剩余大数（部分因子未能继续分解：可能是质数，也可能是两个大质数的乘积）'
        return out('warn', right)
    from collections import Counter
    cnt = Counter(acc)
    full = factors + [(p, e) for p, e in cnt.items()]
    return out('plain', fmt_factors(full))


def _deep_factor(m, algo, ctx, acc):
    if m == 1:
        return True
    if digit_len(m) > MAX_MR_DIGITS:
        return False
    if is_prime(m, ctx.cancel):
        acc.append(m)
        return True
    d = find_factor(m, algo, ctx)
    if not d:
        return False
    ok1 = _deep_factor(d, algo, ctx, acc)
    ok2 = _deep_factor(m // d, algo, ctx, acc)
    return ok1 and ok2


# ---------------------------------------------------------------- 组任务

def run_group(k, cfg, ctx):
    """扫描数量级 10^k 这一组（FR-2/FR-12）。返回 payload。"""
    N = cfg['N']
    per = cfg['per_group']
    limit = cfg['limit']
    detail = cfg['detail']
    start = 10 ** k
    end = min(10 ** (k + 1) - 1, N)
    nlist = []
    primes_found = 0
    checked = 0
    reason = 'done'
    g0 = ctx.now()
    deadline = None if limit is None else g0 + limit
    m = start
    try:
        while m <= end:
            ctx.cancel(deadline)
            ctx.curnum(abbrev(m, k))
            ctx.stage('逐个检查中')
            res = analyze_number(m, k, cfg, ctx)
            checked += 1
            nlist.append(res)
            if res['prime']:
                primes_found += 1
                ctx.emit('milestone', '[数量级 10^%d] 找到本组第 %d 个质数：%s（已检查 %d 个数）'
                         % (k, primes_found, abbrev(m, k), checked))
                if primes_found >= per:
                    break
            m += 1
    except CancelStop:
        reason = 'stopped'
    except CancelSkip:
        reason = 'skipped'
    except CancelTimeout:
        reason = 'timeout'
    if reason == 'done' and primes_found < per:
        reason = 'endN'
    gdt = ctx.now() - g0

    # ---------------- 组装显示行（组内对齐，FR-5）
    rows = []
    ht = '10^%d' % k if k >= 1 else '个位（1~9）'
    if end == start:
        rng = abbrev(start, k)
    elif k >= 9 and end == 10 ** (k + 1) - 1:
        rng = '10^%d ~ 10^%d-1' % (k, k + 1)
    else:
        rng = '%s ~ %s' % (abbrev(start, k), abbrev(end, k))
    rows.append(('header', '━━ 数量级 %s（%s）· 目标：前 %d 个质数 · 模式：%s ━━'
                 % (ht, rng, per, cfg['mode_name'])))
    if nlist:
        wl = max(dw(r['left']) for r in nlist)
        wr = max((dw(r['right']) for r in nlist if r['style'] != 'unit'), default=0)
        shown = nlist if detail else [r for r in nlist if r['style'] in ('prime', 'unit', 'warn')]
        for r in shown:
            if r['style'] == 'unit':
                rows.append(('unit', pad(r['left'], wl) + ' ' + r['right']))
            else:
                txt = pad(r['left'], wl) + ' = ' + pad(r['right'], wr) + '（用时 %.3f 秒）' % r['dt']
                rows.append((r['style'], txt))
    composites = sum(1 for r in nlist if not r['prime'])
    bad = sum(1 for r in nlist if r['style'] == 'warn')
    if not detail and composites:
        if bad:
            rows.append(('info', '—— 另扫描合数 %d 个（其中 %d 个未能完全分解，见橙色行）' % (composites, bad)))
        else:
            rows.append(('info', '—— 另扫描合数 %d 个，均已按所选模式分解完毕（勾选「列出合数明细」可逐个查看）' % composites))
    if nlist:
        top3 = sorted(nlist, key=lambda r: -r['dt'])[:3]
        rows.append(('info', '—— 本组耗时最长的 %d 个数：%s'
                     % (len(top3), '、'.join('%s（用时 %.3f 秒）' % (r['left'], r['dt']) for r in top3))))
    if reason == 'done':
        rows.append(('info', '—— 本组共检查 %d 个数，找到前 %d 个质数（本组用时 %.2f 秒）' % (checked, per, gdt)))
    elif reason == 'endN':
        rows.append(('info', '—— 本组共检查 %d 个数（已到达输入的 N），找到 %d/%d 个质数' % (checked, primes_found, per)))
    elif reason == 'timeout':
        rows.append(('warn', '【本组限时已到：检查了 %d 个数，只找到 %d/%d 个质数——可调大「每组限时」或选「不限时」重跑】'
                     % (checked, primes_found, per)))
    elif reason == 'skipped':
        rows.append(('warn', '【已跳过本组：检查了 %d 个数，找到 %d/%d 个质数】' % (checked, primes_found, per)))
    elif reason == 'stopped':
        rows.append(('warn', '【已停止：检查了 %d 个数，找到 %d/%d 个质数】' % (checked, primes_found, per)))
    return {'k': k, 'reason': reason, 'checked': checked, 'primes': primes_found,
            'gdt': gdt, 'rows': rows}


# ---------------------------------------------------------------- 运行上下文

class WorkerCtx:
    """计算进程内的上下文：时钟（扣除暂停时间）、取消检查、消息回传（限速）。"""

    def __init__(self, widx, result_q, stop_ev, pause_ev, skip_ev):
        self.widx = widx
        self.result_q = result_q
        self.stop_ev = stop_ev
        self.pause_ev = pause_ev
        self.skip_ev = skip_ev
        self.seq = -1
        self._pacc = 0.0
        self._pstart = None
        self._last_prog = -10.0
        self._last_trial = -10.0
        self._cur_t0 = None

    def now(self):
        if self._pstart is not None:
            return self._pstart - self._pacc
        return time.time() - self._pacc

    def cancel(self, deadline=None):
        if self.stop_ev.is_set():
            raise CancelStop()
        if self.skip_ev.is_set():
            raise CancelSkip()
        if self.pause_ev.is_set():
            if self._pstart is None:
                self._pstart = time.time()
            while self.pause_ev.is_set():
                if self.stop_ev.is_set():
                    raise CancelStop()
                time.sleep(0.05)
            self._pacc += time.time() - self._pstart
            self._pstart = None
        if deadline is not None and self.now() >= deadline:
            raise CancelTimeout()

    def _put(self, msg):
        if self.result_q is not None:
            self.result_q.put(msg)

    def curnum(self, label):
        self._cur_t0 = self.now()
        self._put(('curnum', self.seq, self.widx, label, None, None, time.time()))

    def _num_elapsed(self):
        if self._cur_t0 is None:
            return 0.0
        return max(0.0, self.now() - self._cur_t0)

    def stage(self, name):
        t = self.now()
        if t - self._last_prog >= 1.0:
            self._last_prog = t
            self._put(('prog', self.seq, self.widx, '该数已 %.0f 秒，%s' % (self._num_elapsed(), name),
                       None, None, time.time()))

    def emit_trial(self, sub):
        t = self.now()
        if t - self._last_trial >= 0.1:
            self._last_trial = t
            self._put(('trial', self.seq, self.widx,
                       '该数已 %.0f 秒，正在试除找小因子：%s' % (self._num_elapsed(), sub),
                       None, None, time.time()))

    def emit(self, kind, text):
        self._put((kind, self.seq, self.widx, text, None, None, time.time()))


class LocalCtx(WorkerCtx):
    """无进程/无事件的本地上下文（CLI 与自检用）。"""

    def __init__(self, verbose=False):
        super().__init__(0, None, None, None, None)
        self.verbose = verbose
        self.forced = None          # 自检注入：'skip' / 'stop'

    def cancel(self, deadline=None):
        if self.forced == 'stop':
            raise CancelStop()
        if self.forced == 'skip':
            raise CancelSkip()
        if deadline is not None and self.now() >= deadline:
            raise CancelTimeout()

    def _put(self, msg):
        if self.verbose and msg[0] == 'milestone':
            print('  ' + msg[3])


def _worker(widx, task_q, result_q, stop_ev, pause_ev, skip_ev):
    """计算进程主循环：1 个数量级 = 1 个任务 = 1 个计算进程（FR-7）。"""
    try:
        random.seed(os.getpid() ^ int(time.time() * 1000))
        ctx = WorkerCtx(widx, result_q, stop_ev, pause_ev, skip_ev)
        result_q.put(('hello', widx, os.getpid(), None, None, None, time.time()))
        while True:
            task = task_q.get()
            if task is None:
                break
            seq, k, cfg = task
            skip_ev.clear()                     # 新任务开始时清除跳过标记（防级联，FR-9）
            ctx.seq = seq
            ctx._cur_t0 = None
            try:
                payload = run_group(k, cfg, ctx)
                result_q.put(('result', seq, widx, 'group', payload, None, time.time()))
            except Exception:
                result_q.put(('result', seq, widx, 'gerror',
                              {'k': k, 'error': traceback.format_exc(limit=4)}, None, time.time()))
    except Exception as e:      # 不允许静默死亡（FR-21）
        try:
            result_q.put(('result', -1, widx, 'gerror', {'error': repr(e)}, None, time.time()))
        except Exception:
            pass


# ---------------------------------------------------------------- 资源监控（FR-14，Win32 API）

class SysMon:
    def __init__(self):
        self.ok = False
        self.cpu_name = platform.processor() or '未知 CPU'
        self.threads = os.cpu_count() or 1
        self.total_mb = 0
        self.prev_sys = None
        self.prev_proc = {}
        self.prev_wall = None
        if os.name == 'nt':
            try:
                import ctypes
                import winreg
                self.ctypes = ctypes
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                        r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
                        self.cpu_name = winreg.QueryValueEx(key, 'ProcessorNameString')[0].strip()
                except Exception:
                    pass

                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                                ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                                ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                                ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                                ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]

                self._mscls = MEMORYSTATUSEX
                ms = MEMORYSTATUSEX()
                ms.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
                    self.total_mb = ms.ullTotalPhys / 1048576
                self.ok = True
            except Exception:
                self.ok = False

    def _sys_times(self):
        ct = self.ctypes
        idle, kern, user = ct.c_ulonglong(), ct.c_ulonglong(), ct.c_ulonglong()
        if ct.windll.kernel32.GetSystemTimes(ct.byref(idle), ct.byref(kern), ct.byref(user)):
            return idle.value, kern.value, user.value
        return None

    def _proc_times(self, pid):
        ct = self.ctypes
        k32 = ct.windll.kernel32
        h = k32.OpenProcess(0x0410, False, pid)     # QUERY_INFORMATION | VM_READ
        if not h:
            return None
        try:
            class FILETIME(ct.Structure):
                _fields_ = [('lo', ct.c_ulong), ('hi', ct.c_ulong)]

            c_, x_, k_, u_ = FILETIME(), FILETIME(), FILETIME(), FILETIME()
            if not k32.GetProcessTimes(h, ct.byref(c_), ct.byref(x_), ct.byref(k_), ct.byref(u_)):
                return None
            cpu = ((k_.hi << 32) | k_.lo) + ((u_.hi << 32) | u_.lo)   # 100ns 单位

            class PMC(ct.Structure):
                _fields_ = [('cb', ct.c_ulong), ('PageFaultCount', ct.c_ulong),
                            ('PeakWorkingSetSize', ct.c_size_t), ('WorkingSetSize', ct.c_size_t),
                            ('QuotaPeakPagedPoolUsage', ct.c_size_t), ('QuotaPagedPoolUsage', ct.c_size_t),
                            ('QuotaPeakNonPagedPoolUsage', ct.c_size_t), ('QuotaNonPagedPoolUsage', ct.c_size_t),
                            ('PagefileUsage', ct.c_size_t), ('PeakPagefileUsage', ct.c_size_t)]

            pmc = PMC()
            pmc.cb = ct.sizeof(PMC)
            mem = 0
            try:
                if ct.windll.psapi.GetProcessMemoryInfo(h, ct.byref(pmc), pmc.cb):
                    mem = pmc.WorkingSetSize
            except Exception:
                pass
            return cpu, mem
        except Exception:
            return None
        finally:
            k32.CloseHandle(h)

    def tick(self, pids, pid2widx):
        """每 2 秒刷新一次。返回 (整机行, 本程序行, {widx: cpu%})。"""
        if not self.ok:
            return ('电脑：非 Windows 环境，资源监控不可用', '本程序：—', {})
        ct = self.ctypes
        now = time.time()
        sys_cpu = None
        s = self._sys_times()
        if s and self.prev_sys:
            d_idle = s[0] - self.prev_sys[0]
            d_tot = (s[1] + s[2]) - (self.prev_sys[1] + self.prev_sys[2])
            if d_tot > 0:
                sys_cpu = max(0.0, min(100.0, 100.0 * (1 - d_idle / d_tot)))
        self.prev_sys = s
        avail_mb = 0
        try:
            ms = self._mscls()
            ms.dwLength = ct.sizeof(self._mscls)
            if ct.windll.kernel32.GlobalMemoryStatusEx(ct.byref(ms)):
                avail_mb = ms.ullAvailPhys / 1048576
        except Exception:
            pass
        line1 = '电脑：%s｜%d 线程｜整机占用 %s｜内存 %.1f GB，可用 %.1f GB' % (
            self.cpu_name, self.threads,
            ('%.0f%%' % sys_cpu) if sys_cpu is not None else '…',
            self.total_mb / 1024, avail_mb / 1024)
        dt_wall = (now - self.prev_wall) * 1e7 if self.prev_wall else None
        self.prev_wall = now
        cores = 0.0
        mem_sum = 0
        wcpu = {}
        alive = []
        for pid in pids:
            pt = self._proc_times(pid)
            if pt is None:
                continue
            alive.append(pid)
            cpu100, mem = pt
            mem_sum += mem
            prev = self.prev_proc.get(pid)
            if prev is not None and dt_wall:
                cores += max(0.0, (cpu100 - prev) / dt_wall)
                if pid in pid2widx:
                    wcpu[pid2widx[pid]] = max(0.0, min(100.0, 100.0 * (cpu100 - prev) / dt_wall))
            self.prev_proc[pid] = cpu100
        for pid in list(self.prev_proc):
            if pid not in alive:
                self.prev_proc.pop(pid, None)
        share = ''
        if sys_cpu:
            share = '，占整机 %.0f%%' % min(100.0, cores / self.threads * 100.0)
        line2 = '本程序：%d 个计算进程 · CPU 折合 %.1f 核%s · 内存 %.0f MB' % (
            len(alive), cores, share, mem_sum / 1048576)
        return line1, line2, wcpu


# ---------------------------------------------------------------- 设置持久化（FR-19/FR-22）

def settings_path():
    base = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
    p = os.path.join(base, SETTING_FILE)
    try:
        with open(p, 'a'):
            pass
        return p
    except Exception:
        return os.path.join(os.path.expanduser('~'), SETTING_FILE)


def load_settings():
    try:
        with open(settings_path(), 'r', encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_settings(d):
    try:
        with open(settings_path(), 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


THEMES = {
    '经典简洁': dict(bg='#f5f6f7', fg='#202124', entry_bg='#ffffff', btn_bg='#ffffff', active='#e2e8f0',
                    result_bg='#ffffff', result_fg='#202124', header='#1a56c4', prime='#d02020',
                    warn='#cc7a00', info='#8a8f98', prog_bg='#f0fff0', prog_fg='#0a7a2f',
                    accent='#1a73e8', hint='#8a8f98'),
    '科幻炫酷': dict(bg='#0b1024', fg='#bfe8ff', entry_bg='#101a36', btn_bg='#13204a', active='#1d3a7a',
                    result_bg='#070c1c', result_fg='#9fd8ff', header='#39c5ff', prime='#ff5d7a',
                    warn='#ffb347', info='#5f7391', prog_bg='#03140a', prog_fg='#2dff7b',
                    accent='#00e5ff', hint='#5f7391'),
    '卡通二次元': dict(bg='#fff3f7', fg='#8a4a68', entry_bg='#fff9fb', btn_bg='#ffe3ee', active='#ffd0e2',
                      result_bg='#fffafc', result_fg='#7c4460', header='#e06aa8', prime='#e03a5e',
                      warn='#e8963a', info='#b795a6', prog_bg='#f2fff3', prog_fg='#4aa06a',
                      accent='#ff7fb0', hint='#b795a6'),
}
THEME_NAMES = list(THEMES.keys())


# ---------------------------------------------------------------- 图形界面

def run_gui(args):
    import tkinter as tk
    from tkinter import ttk, messagebox

    class App:
        def __init__(self, root):
            self.root = root
            self.set = load_settings()
            self.cores = os.cpu_count() or 4
            root.title(APP_TITLE)
            root.geometry('1280x820')
            self._build()
            self.apply_theme(self.set.get('theme', '经典简洁'))
            self._reset_state()
            root.protocol('WM_DELETE_WINDOW', self.on_close)
            root.bind('<F9>', lambda e: self.on_pause())
            root.bind('<F10>', lambda e: self.on_skip_all())
            root.bind('<F11>', lambda e: self.on_stop())
            self.root.after(80, self.poll)
            self.root.after(1000, self.tick_progress)
            self.res_mon = SysMon()
            self.res_thread = threading.Thread(target=self._res_loop, daemon=True)
            self.res_thread.start()
            if getattr(args, 'demo', None):
                self.combo_input.set(args.demo)
                self.root.after(500, self.on_start)

        # ---------- 构建控件
        def _build(self):
            root = self.root
            self.style = ttk.Style(root)
            try:
                self.style.theme_use('clam')
            except Exception:
                pass
            pad = dict(padx=4, pady=2)
            row0 = ttk.Frame(root)
            row0.pack(fill='x', **pad)
            ttk.Label(row0, text='输入 N：').pack(side='left')
            self.combo_input = ttk.Combobox(row0, values=PRESETS, width=16)
            self.combo_input.set(self.set.get('input', DEFAULT_INPUT))
            self.combo_input.pack(side='left')
            ttk.Label(row0, text='  每组限时：').pack(side='left')
            self.combo_limit = ttk.Combobox(row0, values=LIMIT_CHOICES, width=7, state='readonly')
            self.combo_limit.set(self.set.get('limit', '10 秒'))
            self.combo_limit.pack(side='left')
            ttk.Label(row0, text='  进程数：').pack(side='left')
            self.spin_procs = ttk.Spinbox(row0, from_=1, to=2 * self.cores, width=4)
            self.spin_procs.set(self.set.get('procs', self.cores))
            self.spin_procs.pack(side='left')
            ttk.Label(row0, text='  每组找质数：').pack(side='left')
            self.spin_per = ttk.Spinbox(row0, from_=1, to=20, width=4)
            self.spin_per.set(self.set.get('per_group', DEFAULT_PER_GROUP))
            self.spin_per.pack(side='left')
            ttk.Label(row0, text='  算法：').pack(side='left')
            self.combo_algo = ttk.Combobox(row0, values=ALGOS, width=16, state='readonly')
            self.combo_algo.set(self.set.get('algo', ALGO_AUTO))
            self.combo_algo.pack(side='left')
            self.btn_start = ttk.Button(row0, text='开始分解', command=self.on_start)
            self.btn_start.pack(side='left', padx=(10, 2))
            self.btn_pause = ttk.Button(row0, text='暂停', command=self.on_pause, state='disabled')
            self.btn_pause.pack(side='left', padx=2)
            self.btn_skip = ttk.Button(row0, text='跳过当前', command=self.on_skip_all, state='disabled')
            self.btn_skip.pack(side='left', padx=2)
            self.btn_stop = ttk.Button(row0, text='停止', command=self.on_stop, state='disabled')
            self.btn_stop.pack(side='left', padx=2)

            row1 = ttk.Frame(root)
            row1.pack(fill='x', **pad)
            ttk.Label(row1, text='分解模式：').pack(side='left')
            self.mode_var = tk.StringVar(value=self.set.get('mode', 'full'))
            ttk.Radiobutton(row1, text='完整质因数分解', value='full', variable=self.mode_var).pack(side='left')
            ttk.Radiobutton(row1, text='两数相乘（证合数）', value='two', variable=self.mode_var).pack(side='left', padx=(8, 0))
            self.detail_var = tk.BooleanVar(value=bool(self.set.get('detail', getattr(args, 'detail', False))))
            ttk.Checkbutton(row1, text='列出合数明细', variable=self.detail_var).pack(side='left', padx=(12, 0))
            ttk.Label(row1, text='    主题：').pack(side='left')
            self.combo_theme = ttk.Combobox(row1, values=THEME_NAMES, width=10, state='readonly')
            self.combo_theme.set(self.set.get('theme', '经典简洁'))
            self.combo_theme.pack(side='left')
            self.combo_theme.bind('<<ComboboxSelected>>', lambda e: self.apply_theme(self.combo_theme.get()))

            self.hint = tk.Label(root, anchor='w', justify='left',
                                  text='规则：对每个数量级从 10^k 起逐个 +1 检查，找出前 N 个质数；途中合数按所选模式分解。'
                                       '颜色：组头蓝 / 质数红 / 异常橙 / 辅助灰。快捷键：F9 暂停·继续，F10 跳过当前，F11 停止。\n'
                                       '输入支持：任意大整数（可用逗号/空格/下划线分隔）、10^100、2^64、1e50；'
                                       '右侧面板实时累积记录找到的每一个质数；进度区每一行行尾可单独跳过该组。')
            self.hint.pack(fill='x', padx=6)

            # 底部控件先以 side=bottom 反向 pack，保证始终可见；paned 占中间剩余空间
            rowb = ttk.Frame(root)
            rowb.pack(side='bottom', fill='x', padx=4, pady=(0, 4))
            self.pbar = ttk.Progressbar(rowb, maximum=100, length=260)
            self.pbar.pack(side='left')
            self.status_var = tk.StringVar(value='就绪——输入 N 后点「开始分解」')
            ttk.Label(rowb, textvariable=self.status_var).pack(side='left', padx=8)
            self.lbl_res2 = tk.Label(root, anchor='w')
            self.lbl_res2.pack(side='bottom', fill='x', padx=6)
            self.lbl_res1 = tk.Label(root, anchor='w')
            self.lbl_res1.pack(side='bottom', fill='x', padx=6)
            self.prog_frame = tk.Frame(root, height=300)
            self.prog_frame.pack(side='bottom', fill='x', padx=4, pady=2)
            self.prog_frame.pack_propagate(False)

            self.paned = ttk.Panedwindow(root, orient='horizontal')
            self.paned.pack(fill='both', expand=True, padx=4, pady=2)
            lf = ttk.Frame(self.paned)
            self.paned.add(lf, weight=3)
            rf = ttk.Frame(self.paned)
            self.paned.add(rf, weight=0)
            tk.Label(lf, text='结果区').grid(row=0, column=0, columnspan=2, sticky='w')
            self.res_text = tk.Text(lf, wrap='none', font=('Consolas', 10))
            rsy = ttk.Scrollbar(lf, orient='vertical', command=self.res_text.yview)
            rsx = ttk.Scrollbar(lf, orient='horizontal', command=self.res_text.xview)
            self.res_text.configure(yscrollcommand=rsy.set, xscrollcommand=rsx.set)
            self.res_text.grid(row=1, column=0, sticky='nsew')
            rsy.grid(row=1, column=1, sticky='ns')
            rsx.grid(row=2, column=0, sticky='ew')
            lf.rowconfigure(1, weight=1)
            lf.columnconfigure(0, weight=1)
            tk.Label(rf, text='找到质数记录（实时累积）').grid(row=0, column=0, columnspan=2, sticky='w')
            self.rec_text = tk.Text(rf, wrap='none', width=72, font=('Consolas', 10))
            rsy2 = ttk.Scrollbar(rf, orient='vertical', command=self.rec_text.yview)
            rsx2 = ttk.Scrollbar(rf, orient='horizontal', command=self.rec_text.xview)
            self.rec_text.configure(yscrollcommand=rsy2.set, xscrollcommand=rsx2.set)
            self.rec_text.grid(row=1, column=0, sticky='nsew')
            rsy2.grid(row=1, column=1, sticky='ns')
            rsx2.grid(row=2, column=0, sticky='ew')
            rf.rowconfigure(1, weight=1)
            rf.columnconfigure(0, weight=1)
            self._sash_restored = False
            root.update_idletasks()
            saved_sash = self.set.get('sash')
            try:
                if isinstance(saved_sash, int) and saved_sash > 200:
                    self.paned.sash_pos(0, saved_sash)
                else:
                    self.paned.sash_pos(0, max(300, root.winfo_width() - 560))
                self._sash_restored = True
            except Exception:
                pass

        # ---------- 主题（FR-19）
        def apply_theme(self, name):
            t = THEMES.get(name, THEMES['经典简洁'])
            self.theme = t
            self.combo_theme.set(name)
            self.set['theme'] = name
            st = self.style
            st.configure('.', background=t['bg'], foreground=t['fg'], font=('Microsoft YaHei UI', 9))
            st.configure('TFrame', background=t['bg'])
            st.configure('TLabel', background=t['bg'], foreground=t['fg'])
            st.configure('TButton', background=t['btn_bg'], foreground=t['fg'], padding=4)
            st.map('TButton', background=[('active', t['active']), ('disabled', t['bg'])])
            st.configure('TRadiobutton', background=t['bg'], foreground=t['fg'])
            st.configure('TCheckbutton', background=t['bg'], foreground=t['fg'])
            st.configure('TCombobox', fieldbackground=t['entry_bg'], background=t['btn_bg'],
                         foreground=t['fg'], arrowcolor=t['fg'])
            st.configure('TSpinbox', fieldbackground=t['entry_bg'], background=t['btn_bg'],
                         foreground=t['fg'], arrowcolor=t['fg'])
            st.configure('Horizontal.TProgressbar', background=t['accent'], troughcolor=t['bg'])
            self.root.configure(bg=t['bg'])
            for txt in (self.res_text, self.rec_text):
                txt.configure(bg=t['result_bg'], fg=t['result_fg'], insertbackground=t['result_fg'])
            for tag, col, bold, sz in (('header', t['header'], True, 10), ('prime', t['prime'], True, 10),
                                       ('warn', t['warn'], False, 10), ('info', t['info'], False, 9),
                                       ('unit', t['info'], False, 10)):
                for txt in (self.res_text,):
                    txt.tag_configure(tag, foreground=col, font=('Consolas', sz, 'bold' if bold else ''))
            self.rec_text.tag_configure('rec', foreground=t['prime'], font=('Consolas', 10))
            self.prog_frame.configure(bg=t['prog_bg'])
            self.hint.configure(bg=t['bg'], fg=t['hint'])
            self.lbl_res1.configure(bg=t['bg'], fg=t['info'])
            self.lbl_res2.configure(bg=t['bg'], fg=t['info'])
            for w in self.prog_frame.winfo_children():
                try:
                    w.destroy()
                except Exception:
                    pass
            self._prog_widgets = {}
            save_settings(self.set)

        # ---------- 状态
        def _reset_state(self):
            self.procs = []
            self.task_q = self.result_q = None
            self.stop_ev = self.pause_ev = None
            self.skip_evs = []
            self.pid2widx = {}
            self.widx_cpu = {}
            self.active = {}
            self.buffer = {}
            self.next_render = 0
            self.next_submit = 0
            self.pending_ks = []
            self.running_count = 0
            self.completed = 0
            self.total_groups = 0
            self.total_checked = 0
            self.total_primes = 0
            self.short_reasons = {}
            self.running = False
            self.paused = False
            self.stop_req = False
            self.skip_hold = False
            self.t_start_wall = None
            self.t_start_adj = None
            self._pacc = 0.0
            self._pstart = None
            self.ui_q = queue.Queue()

        def adj_now(self):
            t = time.time() - self._pacc
            if self._pstart is not None:
                t = self._pstart - self._pacc
            return t

        # ---------- 开始/控制（FR-9）
        def read_cfg(self):
            from tkinter import messagebox
            N = parse_input(self.combo_input.get())
            per = max(1, min(20, int(float(self.spin_per.get()))))
            procs = max(1, min(2 * self.cores, int(float(self.spin_procs.get()))))
            lim_txt = self.combo_limit.get()
            limit = None if lim_txt == '不限时' else float(lim_txt.split()[0])
            mode = self.mode_var.get()
            return {'N': N, 'per_group': per, 'procs': procs, 'limit': limit,
                    'mode': mode, 'mode_name': '完整质因数分解' if mode == 'full' else '两数相乘（证合数）',
                    'algo': self.combo_algo.get(), 'detail': bool(self.detail_var.get())}

        def on_start(self):
            from tkinter import messagebox
            if self.running:
                return
            try:
                cfg = self.read_cfg()
            except (ValueError, Exception) as e:
                messagebox.showerror('输入错误', str(e))
                return
            K = digit_len(cfg['N'])
            if K > 400 and not messagebox.askokcancel('组数较多', '将检查 %d 个数量级（>400），确定继续？' % K):
                return
            self.cfg = cfg
            self._reset_state_keep_ui()
            self.res_text.configure(state='normal')
            self.res_text.delete('1.0', 'end')
            self.res_text.configure(state='disabled')
            self.rec_text.configure(state='normal')
            self.rec_text.delete('1.0', 'end')
            self.rec_text.configure(state='disabled')
            self.total_groups = K
            self.pending_ks = list(range(K))
            self.task_q = mp.Queue()
            self.result_q = mp.Queue()
            self.stop_ev = mp.Event()
            self.pause_ev = mp.Event()
            self.skip_evs = [mp.Event() for _ in range(cfg['procs'])]
            for widx in range(cfg['procs']):
                p = mp.Process(target=_worker, args=(widx, self.task_q, self.result_q,
                                                     self.stop_ev, self.pause_ev, self.skip_evs[widx]),
                               daemon=True)
                p.start()
                self.procs.append(p)
            self.running = True
            self.t_start_wall = time.time()
            self.t_start_adj = self.adj_now()
            self.btn_start.configure(state='disabled')
            for b in (self.btn_pause, self.btn_skip, self.btn_stop):
                b.configure(state='normal')
            self.status_var.set('正在计算…')
            self.submit_next()

        def _reset_state_keep_ui(self):
            self.procs = []
            self.pid2widx = {}
            self.widx_cpu = {}
            self.active = {}
            self.buffer = {}
            self.next_render = 0
            self.next_submit = 0
            self.running_count = 0
            self.completed = 0
            self.total_checked = 0
            self.total_primes = 0
            self.short_reasons = {}
            self.paused = False
            self.stop_req = False
            self.skip_hold = False
            self._pacc = 0.0
            self._pstart = None
            self.btn_pause.configure(text='暂停')

        def submit_next(self):
            if not self.running:
                return
            while (self.running_count < self.cfg['procs'] and self.pending_ks
                   and not self.stop_req and not self.skip_hold):
                k = self.pending_ks.pop(0)
                seq = self.next_submit
                self.next_submit += 1
                self.active[seq] = {'k': k, 'widx': None, 'curnum': '', 'stage': '排队中',
                                    't0': self.adj_now(), 'glabel': abbrev(10 ** k, k)}
                self.task_q.put((seq, k, self.cfg))
                self.running_count += 1

        def on_pause(self):
            if not self.running:
                return
            if not self.paused:
                self.pause_ev.set()
                self.paused = True
                self._pstart = time.time()
                self.btn_pause.configure(text='继续')
                self.status_var.set('已暂停（F9 继续）')
            else:
                self.pause_ev.clear()
                self.paused = False
                if self._pstart is not None:
                    self._pacc += time.time() - self._pstart
                    self._pstart = None
                self.btn_pause.configure(text='暂停')
                self.status_var.set('正在计算…')

        def on_skip_all(self):
            if not self.running:
                return
            if not any(a['widx'] is not None for a in self.active.values()):
                return
            self.skip_hold = True                       # 暂停派发新任务，防级联（FR-9）
            for ev in self.skip_evs:
                ev.set()

        def skip_one(self, widx):
            if self.running and 0 <= widx < len(self.skip_evs):
                self.skip_evs[widx].set()

        def on_stop(self):
            if not self.running:
                return
            self.stop_req = True
            self.skip_hold = True
            self.stop_ev.set()

        # ---------- 消息轮询
        def poll(self):
            if self.result_q is not None:
                try:
                    while True:
                        msg = self.result_q.get_nowait()
                        self.handle_msg(msg)
                except Exception:
                    pass
            try:
                while True:
                    kind, data = self.ui_q.get_nowait()
                    if kind == 'res':
                        l1, l2, wcpu = data
                        self.lbl_res1.configure(text=l1)
                        self.lbl_res2.configure(text=l2)
                        self.widx_cpu = wcpu
            except Exception:
                pass
            self.root.after(80, self.poll)

        def handle_msg(self, msg):
            kind = msg[0]
            if kind == 'hello':
                _, widx, pid = msg[0], msg[1], msg[2]
                self.pid2widx[pid] = widx
                return
            seq, widx = msg[1], msg[2]
            if kind == 'curnum':
                a = self.active.get(seq)
                if a is not None:
                    a['widx'] = widx
                    a['curnum'] = msg[3]
                    a['stage'] = '逐个检查中'
            elif kind in ('trial', 'prog'):
                a = self.active.get(seq)
                if a is not None:
                    a['widx'] = widx
                    a['stage'] = msg[3]
            elif kind == 'milestone':
                self.rec_text.configure(state='normal')
                self.rec_text.insert('end', msg[3] + '\n', 'rec')
                self.rec_text.see('end')
                self.rec_text.configure(state='disabled')
            elif kind == 'result':
                self.running_count -= 1
                self.active.pop(seq, None)
                self.buffer[seq] = msg
                self.flush_buffer()
                if self.skip_hold and not self.stop_req and self.running_count == 0:
                    self.skip_hold = False              # 跳过完成，恢复派发
                self.submit_next()
                if self.completed >= self.total_groups or (self.stop_req and self.running_count == 0):
                    self.finish()

        def flush_buffer(self):
            while self.next_render in self.buffer:
                msg = self.buffer.pop(self.next_render)
                self.next_render += 1
                _, seq, widx, kind, payload, _, _ = msg
                if kind == 'gerror':
                    self._append('warn', '【出错】数量级任务异常：%s' % str(payload.get('error', ''))[:400])
                    self.completed += 1
                    continue
                pl = payload
                self.completed += 1
                self.total_checked += pl['checked']
                self.total_primes += pl['primes']
                if pl['primes'] < self.cfg['per_group']:
                    self.short_reasons[pl['reason']] = self.short_reasons.get(pl['reason'], 0) + 1
                for style, text in pl['rows']:
                    tag = style if style in ('header', 'prime', 'warn', 'info', 'unit') else 'plain'
                    self._append(tag, text)
                self.pbar.configure(value=100.0 * self.completed / max(1, self.total_groups))
                self.status_var.set('已完成 %d/%d 个数量级 · 已检查 %d 个数 · 找到质数 %d 个%s'
                                    % (self.completed, self.total_groups, self.total_checked,
                                       self.total_primes, ' · 已暂停' if self.paused else ''))

        def _append(self, tag, text):
            self.res_text.configure(state='normal')
            self.res_text.insert('end', text + '\n', tag)
            self.res_text.see('end')
            self.res_text.configure(state='disabled')

        # ---------- 进度面板（FR-10/FR-18）
        def tick_progress(self):
            t = self.theme
            for w in self.prog_frame.winfo_children():
                w.destroy()
            self._prog_widgets = {}
            if not self.running:
                tk.Label(self.prog_frame, bg=t['prog_bg'], fg=t['prog_fg'], anchor='w',
                         font=('Consolas', 10),
                         text='（当前没有任务——输入 N 后点「开始分解」；快捷键 F9 暂停/继续、F10 跳过当前、F11 停止）').pack(fill='x')
                self.root.after(1000, self.tick_progress)
                return
            busy = sum(1 for a in self.active.values() if a['widx'] is not None)
            tk.Label(self.prog_frame, bg=t['prog_bg'], fg=t['prog_fg'], anchor='w', font=('Consolas', 10),
                     text='[正在计算] 共 %d 组 · 并行 %d 进程（每组占 1 个）· 忙碌 %d / 空闲 %d%s'
                          % (self.total_groups, self.cfg['procs'], busy, self.cfg['procs'] - busy,
                             ' · 已暂停' if self.paused else '')).pack(fill='x')
            items = sorted(self.active.items(), key=lambda kv: kv[1]['k'])
            shown = items[:12]
            now = self.adj_now()
            for seq, a in shown:
                row = tk.Frame(self.prog_frame, bg=t['prog_bg'])
                row.pack(fill='x')
                widx = a['widx']
                cpu = ('（CPU %.0f%%）' % self.widx_cpu[widx]) if (widx is not None and widx in self.widx_cpu) else ''
                ge = max(0.0, now - a['t0'])
                txt = '数量级 %s（本组已 %.0f 秒）正在算 %s（%s）· 进程#%s%s' % (
                    a['glabel'], ge, a['curnum'] or '…', a['stage'],
                    (widx + 1) if widx is not None else '?', cpu)
                tk.Label(row, bg=t['prog_bg'], fg=t['prog_fg'], anchor='w', font=('Consolas', 10),
                         text=txt).pack(side='left', fill='x', expand=True)
                if widx is not None:
                    b = tk.Button(row, text='跳过', font=('Microsoft YaHei UI', 8),
                                  command=lambda w=widx: self.skip_one(w))
                    b.pack(side='right')
                self._prog_widgets[seq] = row
            if len(items) > 12:
                tk.Label(self.prog_frame, bg=t['prog_bg'], fg=t['prog_fg'], anchor='w', font=('Consolas', 10),
                         text='……其余 %d 组进行中' % (len(items) - 12)).pack(fill='x')
            self.root.after(1000, self.tick_progress)

        # ---------- 资源监控线程
        def _res_loop(self):
            while True:
                try:
                    pids = [p.pid for p in self.procs if p.is_alive()]
                    data = self.res_mon.tick(pids, self.pid2widx)
                    self.ui_q.put(('res', data))
                except Exception:
                    pass
                time.sleep(2)

        # ---------- 收尾
        def finish(self):
            if not self.running:
                return
            self.running = False
            mode = '完整质因数分解' if self.cfg['mode'] == 'full' else '两数相乘（证合数）'
            lim = '不限时' if self.cfg['limit'] is None else ('%g 秒' % self.cfg['limit'])
            self._append('header', '━━━━━━━━━━ 任务汇总 ━━━━━━━━━━')
            if self.stop_req:
                self._append('warn', '【任务已停止】')
            self._append('info', '模式：%s · 算法：%s · 进程数：%d · 每组限时：%s · 每组找质数：%d 个'
                         % (mode, self.cfg['algo'], self.cfg['procs'], lim, self.cfg['per_group']))
            self._append('info', '共检查 %d 个数量级（共 %d 个）、%d 个数，找到质数 %d 个'
                         % (self.completed, self.total_groups, self.total_checked, self.total_primes))
            if self.short_reasons:
                names = {'timeout': '限时已到', 'skipped': '被跳过', 'stopped': '任务停止', 'endN': '到达输入的 N'}
                detail = '、'.join('%s %d 组' % (names.get(r, r), c) for r, c in self.short_reasons.items())
                self._append('warn', '未找满 %d 个质数的数量级：%s（%s）——可调大限时/取消跳过重跑'
                             % (self.cfg['per_group'], sum(self.short_reasons.values()), detail))
            calc = self.adj_now() - self.t_start_adj
            wall = time.time() - self.t_start_wall
            self._append('info', '分解计算总用时 %.2f 秒 · 全程耗时 %.2f 秒' % (calc, wall))
            self.status_var.set('就绪——已完成 %d/%d 个数量级' % (self.completed, self.total_groups))
            self._shutdown_workers()
            self.btn_start.configure(state='normal')
            for b in (self.btn_pause, self.btn_skip, self.btn_stop):
                b.configure(state='disabled')

        def _shutdown_workers(self):
            try:
                if self.task_q is not None:
                    for _ in self.procs:
                        try:
                            self.task_q.put_nowait(None)
                        except Exception:
                            pass
                t0 = time.time()
                for p in self.procs:
                    p.join(timeout=max(0.1, 3 - (time.time() - t0)))
                for p in self.procs:
                    if p.is_alive():
                        p.terminate()
                for p in self.procs:
                    p.join(timeout=1)
            except Exception:
                pass
            self.procs = []
            self.pid2widx = {}

        # ---------- 关闭
        def on_close(self):
            try:
                self.set['sash'] = self.paned.sash_pos(0)
            except Exception:
                pass
            self.set.update({'input': self.combo_input.get(), 'limit': self.combo_limit.get(),
                             'procs': self.spin_procs.get(), 'per_group': self.spin_per.get(),
                             'algo': self.combo_algo.get(), 'mode': self.mode_var.get(),
                             'detail': bool(self.detail_var.get()), 'theme': self.combo_theme.get()})
            save_settings(self.set)
            if self.running:
                try:
                    self.stop_ev.set()
                except Exception:
                    pass
                self._shutdown_workers()
            self.root.destroy()

    root = __import__('tkinter').Tk()
    App(root)
    root.mainloop()


# ---------------------------------------------------------------- 命令行模式

def run_cli(expr, detail=False):
    N = parse_input(expr)
    K = digit_len(N)
    cfg = {'N': N, 'per_group': DEFAULT_PER_GROUP, 'limit': None, 'mode': 'full',
           'mode_name': '完整质因数分解', 'algo': ALGO_AUTO, 'detail': detail}
    print('%s：N=%s，共 %d 个数量级，每组找前 %d 个质数' % (APP_TITLE, abbrev(N), K, cfg['per_group']))
    t0 = time.time()
    tot_checked = tot_primes = 0
    for k in range(K):
        ctx = LocalCtx(verbose=True)
        pl = run_group(k, cfg, ctx)
        for style, text in pl['rows']:
            print(text)
        tot_checked += pl['checked']
        tot_primes += pl['primes']
    print('—— 汇总：共 %d 组，检查 %d 个数，找到质数 %d 个，总用时 %.2f 秒'
          % (K, tot_checked, tot_primes, time.time() - t0))


# ---------------------------------------------------------------- 自检（验收清单第 1 条）

def selftest():
    import multiprocessing as _mp
    cases = []

    def case(name):
        def deco(fn):
            cases.append((name, fn))
            return fn
        return deco

    @case('缩写：10 的幂')
    def t1():
        assert abbrev(10 ** 21) == '10^21'
        assert abbrev(1) == '1'
        assert abbrev(10 ** 9) == '10^9'

    @case('缩写：10^k+小偏移')
    def t2():
        assert abbrev(10 ** 21 + 1) == '10^21+1'
        assert abbrev(10 ** 44 + 257, 44) == '10^44+257'

    @case('缩写：科学计数 7 位有效去尾零')
    def t3():
        assert abbrev(5 * 10 ** 103) == '5×10^103'
        assert abbrev(10 ** 102 - 10 ** 95) == '9.999999×10^101'
        assert abbrev(int('9090909' + '0' * 95)) == '9.090909×10^101'

    @case('缩写：≤9 位原样')
    def t4():
        assert abbrev(123456789) == '123456789'
        assert abbrev(100000000) == '100000000'

    @case('显示宽度与对齐')
    def t5():
        assert dw('中文a') == 5
        assert dw('abc') == 3
        assert pad('中文', 6) == '中文  '

    @case('输入解析')
    def t6():
        assert parse_input('10^6') == 10 ** 6
        assert parse_input('2^64') == 2 ** 64
        assert parse_input('1e30') == 10 ** 30
        assert parse_input('1,234 567_89') == 123456789
        assert parse_input('1.5e3') == 1500
        for bad in ('', 'abc', '-5', '1.5e0', '10^'):
            try:
                parse_input(bad)
                raise AssertionError('应报错: %r' % bad)
            except ValueError:
                pass

    @case('素性判定（含卡迈克尔数与强伪素数）')
    def t7():
        for p in (2, 3, 17, 101, 7919, 2305843009213693951):
            assert is_prime(p), p
        for c in (1, 561, 1105, 3215031751, 10 ** 50, 2305843009213693951 * 3):
            assert not is_prime(c), c

    @case('完整分解：123456789 = 3^2 × 3607 × 3803')
    def t8():
        cfg = {'mode': 'full', 'algo': ALGO_AUTO}
        ctx = LocalCtx()
        r = analyze_number(123456789, 8, cfg, ctx)
        assert r['style'] == 'plain'
        assert r['right'] == '3^2 × 3607 × 3803', r['right']

    @case('两数相乘模式：10403 = 101 × 103')
    def t9():
        cfg = {'mode': 'two', 'algo': ALGO_AUTO}
        r = analyze_number(10403, 4, cfg, LocalCtx())
        assert r['right'] == '101 × 103', r['right']

    @case('质数标红：100003 为质数')
    def t10():
        cfg = {'mode': 'full', 'algo': ALGO_AUTO}
        r = analyze_number(100003, 5, cfg, LocalCtx())
        assert r['style'] == 'prime' and r['prime']

    @case('单位数 1')
    def t11():
        r = analyze_number(1, 0, {'mode': 'full', 'algo': ALGO_AUTO}, LocalCtx())
        assert r['style'] == 'unit' and '既不是质数也不是合数' in r['right']

    @case('扫描：10^2 组找出 101/103/107')
    def t12():
        cfg = {'N': 10 ** 3, 'per_group': 3, 'limit': None, 'mode': 'full',
               'mode_name': '完整', 'algo': ALGO_AUTO, 'detail': True}
        pl = run_group(2, cfg, LocalCtx())
        assert pl['reason'] == 'done'
        txt = ' '.join(t for s, t in pl['rows'])
        assert '101' in txt and '103' in txt and '107' in txt
        assert pl['checked'] == 8 and pl['primes'] == 3

    @case('限时：0 秒立即超时')
    def t13():
        cfg = {'N': 10 ** 30, 'per_group': 3, 'limit': 0, 'mode': 'full',
               'mode_name': '完整', 'algo': ALGO_AUTO, 'detail': False}
        ctx = LocalCtx()
        pl = run_group(5, cfg, ctx)
        assert pl['reason'] == 'timeout', pl['reason']
        assert '本组限时已到' in ' '.join(t for s, t in pl['rows'])

    @case('跳过：跳过事件生效且不级联')
    def t14():
        cfg = {'N': 10 ** 10, 'per_group': 3, 'limit': None, 'mode': 'full',
               'mode_name': '完整', 'algo': ALGO_AUTO, 'detail': False}
        ctx = LocalCtx()
        ctx.forced = 'skip'
        pl = run_group(3, cfg, ctx)
        assert pl['reason'] == 'skipped'
        ctx.forced = None
        pl2 = run_group(4, cfg, ctx)          # 下一组不受影响
        assert pl2['reason'] == 'done'

    @case('停止：停止事件生效')
    def t15():
        cfg = {'N': 10 ** 10, 'per_group': 3, 'limit': None, 'mode': 'full',
               'mode_name': '完整', 'algo': ALGO_AUTO, 'detail': False}
        ctx = LocalCtx()
        ctx.forced = 'stop'
        pl = run_group(3, cfg, ctx)
        assert pl['reason'] == 'stopped'

    @case('对齐：组内 = 号与用时列对齐')
    def t16():
        cfg = {'N': 10 ** 4, 'per_group': 3, 'limit': None, 'mode': 'full',
               'mode_name': '完整', 'algo': ALGO_AUTO, 'detail': True}
        pl = run_group(3, cfg, LocalCtx())
        widths = set()
        tw = set()
        for style, text in pl['rows']:
            if ' = ' in text:
                widths.add(dw(text.split(' = ')[0]))
                if '（用时' in text:
                    tw.add(dw(text.split('（用时')[0]))
        assert len(widths) == 1, widths
        assert len(tw) == 1, tw

    @case('rho 分解半素数')
    def t17():
        n = 1000003 * 1000033
        cfg = {'mode': 'full', 'algo': ALGO_RHO}
        r = analyze_number(n, 12, cfg, LocalCtx())
        assert r['style'] == 'plain', r
        assert '1000003' in r['right'] and '1000033' in r['right']

    @case('流水线：多进程顺序上屏（10^4，2 进程）')
    def t18():
        ctx = _mp.get_context('spawn' if os.name == 'nt' else None)
        N = 10 ** 4
        cfg = {'N': N, 'per_group': 3, 'limit': 30, 'mode': 'full',
               'mode_name': '完整', 'algo': ALGO_AUTO, 'detail': False}
        tq, rq = ctx.Queue(), ctx.Queue()
        stop, pause = ctx.Event(), ctx.Event()
        skips = [ctx.Event(), ctx.Event()]
        procs = [ctx.Process(target=_worker, args=(w, tq, rq, stop, pause, skips[w])) for w in range(2)]
        for p in procs:
            p.start()
        for seq in range(5):
            tq.put((seq, seq, cfg))
        got = {}
        t_end = time.time() + 60
        while len(got) < 5 and time.time() < t_end:
            msg = rq.get(timeout=60)
            if msg[0] == 'result':
                got[msg[1]] = msg[4]
        order_ok = list(sorted(got)) == [0, 1, 2, 3, 4]
        primes_ok = all(got[k]['primes'] == 3 for k in range(4))
        last_ok = got[4]['primes'] == 0 and got[4]['reason'] == 'endN'
        for _ in procs:
            tq.put(None)
        stop.set()
        for p in procs:
            p.join(timeout=5)
            if p.is_alive():
                p.terminate()
        assert order_ok and primes_ok and last_ok, (list(got.keys()),
                                                    {k: (v['primes'], v['reason']) for k, v in got.items()})

    passed = 0
    for name, fn in cases:
        try:
            fn()
            passed += 1
            print('  [通过] %s' % name)
        except Exception as e:
            print('  [失败] %s：%r' % (name, e))
    print('selftest: %d/%d 通过' % (passed, len(cases)))
    return 0 if passed == len(cases) else 1


# ---------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser(description=APP_TITLE)
    ap.add_argument('--selftest', action='store_true', help='运行自检后退出')
    ap.add_argument('--demo', metavar='N', help='打开界面并自动分解指定 N')
    ap.add_argument('--cli', metavar='N', help='无界面命令行模式')
    ap.add_argument('--detail', action='store_true', help='启动即勾选「列出合数明细」')
    args = ap.parse_args()
    if args.selftest:
        sys.exit(selftest())
    if args.cli:
        run_cli(args.cli, detail=args.detail)
        return
    run_gui(args)


if __name__ == '__main__':
    mp.freeze_support()
    main()
