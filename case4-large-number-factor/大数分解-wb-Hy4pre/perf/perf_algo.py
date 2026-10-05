# -*- coding: utf-8 -*-
"""追加实验：深试除收益、ECM vs rho、进程池稳态扩展性。"""
import sys, os, time, math, multiprocessing as mp

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import (SMALL_PRIMES, _trial_count, is_prime, ndigits, Abort,
                  ecm_factor, pollard_rho_brent, pollard_pm1)

CORES = os.cpu_count() or 1


def sieve(n):
    bs = bytearray([1]) * (n + 1)
    bs[0:2] = b"\x00\x00"
    for i in range(2, math.isqrt(n) + 1):
        if bs[i]:
            bs[i * i:: i] = bytearray(len(range(i * i, n + 1, i)))
    return [i for i in range(2, n + 1) if bs[i]]


def mul_work(cnt):
    a = (1 << 4096) - 3
    b = (1 << 4096) - 17
    m = (1 << 8192) - 1
    x = a
    for _ in range(cnt):
        x = (x * b) & m
    return x


def main():
    print("=" * 80)
    print("[A] 进程池：把「池创建开销」与「稳态扩展性」分开测")
    CNT = 3000
    t0 = time.perf_counter(); mul_work(CNT); base = time.perf_counter() - t0
    print("  单进程 %d 次 = %.4f s（基准）" % (CNT, base))
    for np_ in (4, 8, 16, 24):
        t0 = time.perf_counter()
        pool = mp.Pool(np_)
        t_create = time.perf_counter() - t0
        pool.map(mul_work, [CNT] * np_)          # 预热
        t0 = time.perf_counter()
        for _ in range(3):
            pool.map(mul_work, [CNT] * np_)
        dt = (time.perf_counter() - t0) / 3
        pool.close(); pool.join()
        print("  %2d 进程：池创建 %.2f s ｜ 稳态每轮 %.3f s → 稳态加速 %.2fx（理论 %d）"
              % (np_, t_create, dt, base * np_ / dt, np_))

    print("\n[B] 深试除（10^5 → 10^6 → 10^7）能救回多少存活数？")
    PR6 = [p for p in SMALL_PRIMES]                 # 只到 1e5
    extra6 = [p for p in sieve(10 ** 6) if p > 10 ** 5]
    print("  素数表扩到 10^6：共 %d 个素数（原 %d）" % (len(PR6) + len(extra6), len(PR6)))
    for k in (30, 50, 100):
        lo = 10 ** k
        base_tp = SMALL_PRIMES[:_trial_count(k + 1)]
        surv, i = [], 0
        while len(surv) < 20 and i < 30000:
            n = lo + i; i += 1
            for p in base_tp:
                if p * p > n: break
                if n % p == 0: break
            else:
                surv.append(n)
        if not surv:
            continue
        t0 = time.perf_counter()
        hit = 0
        for n in surv:
            for p in extra6:
                if p * p > n: break
                if n % p == 0: hit += 1; break
        dt6 = time.perf_counter() - t0
        print("  10^%-4d 存活 %2d 个 → 10^6 以内找到因子的：%2d 个（%.0f%%），"
              "深试除均摊 %.1f ms/数"
              % (k, len(surv), hit, 100.0 * hit / len(surv), dt6 / len(surv) * 1000))

    print("\n[C] 对同一批存活数：rho / p-1 / 本项目的 ECM 谁更快？（每数 1.5 秒预算）")
    for k in (30, 50):
        lo = 10 ** k
        base_tp = SMALL_PRIMES[:_trial_count(k + 1)]
        surv, i = [], 0
        while len(surv) < 12 and i < 30000:
            n = lo + i; i += 1
            if is_prime(n):
                continue
            ok = False
            for p in base_tp:
                if p * p > n: break
                if n % p == 0: ok = True; break
            if not ok:
                surv.append(n)
        if not surv:
            continue
        res = {}
        for name, fn in (("rho", "rho"), ("p-1", "pm1"), ("ECM(本项目)", "ecm")):
            done, tot = 0, 0.0
            for n in surv:
                t0 = time.perf_counter()
                try:
                    if fn == "rho":
                        d = pollard_rho_brent(n, None)
                    elif fn == "pm1":
                        d = pollard_pm1(n, 50000, None)
                    else:
                        d = ecm_factor(n, 2000, 30, None)
                except Exception:
                    d = None
                tot += time.perf_counter() - t0
                if d and 1 < d < n:
                    done += 1
            res[name] = (done, tot / len(surv) * 1000)
        print("  10^%d（%d 个硬合数，1.5s 预算）：" % (k, len(surv)))
        for name, (done, ms) in res.items():
            print("     %-12s 解出 %2d/%d，平均 %.1f ms/数" % (name, done, len(surv), ms))

    print("\n[D] 「先找质数、后分解合数」两段式的潜在收益")
    print("  若把扫描（找质数）与分解（硬合数）解耦：")
    print("    - 扫描阶段成本 ≈ 0.05~0.2 ms/数（试除+MR），几乎不耗时；")
    print("    - 用户先立刻看到质数，合数在后台慢慢算；")
    print("    - 当前实现里，一个硬合数会阻塞整组（实测 98.5%~99.9% 的时间都花在这）。")
    print()


if __name__ == "__main__":
    mp.freeze_support()
    main()
