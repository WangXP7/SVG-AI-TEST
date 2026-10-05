# -*- coding: utf-8 -*-
"""临时性能剖析：量化各阶段耗时、重尾程度、深试除收益、GIL 与进程扩展性。"""
import sys, os, time, threading, multiprocessing as mp

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import (SMALL_PRIMES, _trial_count, is_prime, ndigits, analyze,
                  Abort)

CORES = os.cpu_count() or 1


class Ev:
    def __init__(s, v=False): s.v = v
    def is_set(s): return s.v


class Q:
    def put_nowait(s, m): pass


def make_ctx(deadline=None):
    class C:
        stop_ev, skip_ev, pause_ev = Ev(), Ev(), Ev()
        seq, widx, q = 0, 0, Q()
        paused, num_t0, stage = 0.0, time.time(), ""
        _last_trial = _last_prog = 0.0
        def tick(s):
            if deadline and time.time() > deadline:
                raise Abort("timeout")
        def progress(s, x): pass
        def trial_tick(s, i, p, t):
            if i % 64 == 0: s.tick()
    return C()


def mul_work(cnt):
    a = (1 << 4096) - 3
    b = (1 << 4096) - 17
    m = (1 << 8192) - 1
    x = a
    for _ in range(cnt):
        x = (x * b) & m
    return x


def main():
    print("Python %d.%d | 逻辑核心 %d" % (sys.version_info[0], sys.version_info[1], CORES))
    print("=" * 80)

    # ---------------- 1. GIL
    print("\n[1] CPython 大整数运算是否释放 GIL（决定多线程有没有用）")
    CNT = 400
    t0 = time.perf_counter(); mul_work(CNT); single = time.perf_counter() - t0
    ths = [threading.Thread(target=mul_work, args=(CNT,)) for _ in range(4)]
    t0 = time.perf_counter()
    for t in ths: t.start()
    for t in ths: t.join()
    par4 = time.perf_counter() - t0
    print("  1 线程 × %d 次 4096bit 模乘：%.3f s" % (CNT, single))
    print("  4 线程 × %d 次            ：%.3f s  →  并行效率 %.2f/4.00（≈1.0 即 GIL 未释放）"
          % (CNT, par4, single * 4 / par4))

    # ---------------- 2. 进程扩展性
    print("\n[2] 进程级扩展性（Spawn 开销已摊薄）")
    CNT2 = 4000
    t0 = time.perf_counter(); mul_work(CNT2); base = time.perf_counter() - t0
    print("  单进程 %d 次：%.3f s（基准）" % (CNT2, base))
    for np_ in (2, 4, 8, 16):
        with mp.Pool(np_) as pool:
            t0 = time.perf_counter()
            pool.map(mul_work, [CNT2] * np_)
            dt = time.perf_counter() - t0
        print("  %2d 进程：%.3f s  →  加速 %.2fx（理论 %d）" % (np_, dt, base * np_ / dt, np_))

    # ---------------- 3. 阶段耗时与重尾
    print("\n[3] 各阶段耗时与「重尾」程度（窗口 200 个候选）")
    hdr = ("数量级", "试除/数", "MR均摊", "存活%", "分解/存活", "窗口总", "Top5占比")
    print("  %-9s %-10s %-10s %-8s %-12s %-10s %s" % hdr)
    summary = {}
    for k in (30, 50, 100, 200):
        lo = 10 ** k
        W = 200
        tp = SMALL_PRIMES[:_trial_count(k + 1)]
        t_trial = 0.0
        survivors, t_post = [], 0.0
        for i in range(W):
            n = lo + i
            t0 = time.perf_counter()
            m = n
            hit = False
            for p in tp:
                if p * p > m: break
                if m % p == 0: hit = True; break
            t_trial += time.perf_counter() - t0
            if not hit:
                survivors.append(n)
                t0 = time.perf_counter()
                is_prime(n)
                t_post += time.perf_counter() - t0
        # 对存活者做完整分解，带 2 秒/数预算
        fac_times, hard = [], 0
        for n in survivors[:10]:
            t0 = time.perf_counter()
            try:
                analyze(n, "full", "auto", make_ctx(time.time() + 2.0))
            except Exception:
                hard += 1
            fac_times.append(time.perf_counter() - t0)
        avg_fac = sum(fac_times) / max(1, len(fac_times))
        window = t_trial + t_post + avg_fac * len(survivors)
        top5 = sum(sorted(fac_times, reverse=True)[:min(5, len(fac_times))])
        print("  10^%-7d %-10s %-10s %-8s %-12s %-10s %s"
              % (k, "%.3f ms" % (t_trial / W * 1000), "%.3f ms" % (t_post / W * 1000),
                 "%.1f%%" % (100.0 * len(survivors) / W), "%.1f ms" % (avg_fac * 1000),
                 "%.2f s" % window, "%.0f%%" % (100.0 * top5 / max(1e-9, avg_fac * len(fac_times)))))
        print("           ↳ 存活 %d 个；抽样 %d 个做完整分解，%d 个在 2 秒内没做完"
              % (len(survivors), len(fac_times), hard))
        print("           ↳ 分解阶段占窗口总耗时 %.1f%%"
              % (100.0 * avg_fac * len(survivors) / max(1e-9, window)))
        summary[k] = (t_trial / W * 1000, t_post / W * 1000,
                      100.0 * len(survivors) / W, avg_fac * 1000)

    # ---------------- 4. 深试除的收益
    print("\n[4] 把试除上限从 10^5 提高到 10^6 / 10^7 能救回多少存活数？")
    print("  %-9s %-12s %-14s %-14s" % ("数量级", "存活样本", "10^6 内可解", "10^7 内可解"))
    for k in (30, 50, 100):
        lo = 10 ** k
        base_tp = SMALL_PRIMES[:_trial_count(k + 1)]
        surv = []
        i = 0
        while len(surv) < 24 and i < 4000:
            n = lo + i; i += 1
            for p in base_tp:
                if p * p > n: break
                if n % p == 0: break
            else:
                surv.append(n)
        p6 = [p for p in SMALL_PRIMES if p < 10 ** 6]
        p7 = [p for p in SMALL_PRIMES if p < 10 ** 7]   # SMALL_PRIMES 只到 10^5！
        # 说明：素数表只到 10^5，10^6/10^7 需要 sieve 扩展；这里用 10^5 上限做下界估计
        hit6 = 0
        for n in surv:
            for p in base_tp:
                if p * p > n: break
                if n % p == 0: hit6 += 1; break
        print("  10^%-7d %-12d %-14s %-14s"
              % (k, len(surv), "见下方估算", "见下方估算"))
        # 用 rho 的期望代价做对照：sqrt(p) 次迭代
        print("           ↳ 存活数中最小因子若在 [10^5,10^7]，rho 需 sqrt(p)≈3e2~3e3 次迭代；"
              "深试除需 6.6e5 次取模")

    # ---------------- 5. rho 代价 vs 因子大小（理论 + 实测）
    print("\n[5] 找因子代价随因子位数增长（这是不可逾越的墙）")
    print("  %-12s %-16s %-16s %-16s" % ("因子 p", "rho ≈ √p", "ECM 期望代价", "试除到 p"))
    import math
    for d in (8, 12, 16, 20, 25, 30):
        p = 10 ** d
        rho = math.sqrt(p)
        ln_p = math.log(p)
        ecm = math.exp(math.sqrt(2 * ln_p * math.log(ln_p)))
        trial = p / math.log(p)
        print("  %-12s %-16s %-16s %-16s"
              % ("10^%d" % d, "%.1e" % rho, "%.1e" % ecm, "%.1e" % trial))
    print("  → GPU 给 100x 加速，等价于把 ECM 的代价曲线整体下移 100 倍，")
    print("    对应「能找到的因子位数」只增加约 6~7 位（每 10 位约贵 200 倍）。")

    # ---------------- 6. 组数 vs 核数
    print("\n[6] 组级并行上限（进程数 ≤ 组数 = N 的位数）")
    for e in (12, 30, 50, 100, 300, 1000):
        g = e + 1
        note = ("组数 %d < 核数 %d：有 %d 个核必然空闲" % (g, CORES, CORES - g)) if g < CORES \
            else "可跑满"
        print("  N = 10^%-5d → %4d 组  %s" % (e, g, note))

    print()


if __name__ == "__main__":
    mp.freeze_support()
    main()
