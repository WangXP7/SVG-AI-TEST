# -*- coding: utf-8 -*-
"""
大数分解可视化工具 —— 程序入口（factor_gui.py）

用法：
    python factor_gui.py                 启动图形界面
    python factor_gui.py --demo=10^12    启动界面并自动开始分解 10^12
    python factor_gui.py --detail        默认勾选「列出合数明细」
    python factor_gui.py --selftest      运行自检用例（命令行，无需界面）
    python factor_gui.py --headless=N    无界面跑完整个流水线并打印结果

按《产品需求设计-ZCode-GLM5.3Flash-v1.8.md》实现；纯标准库，Python >= 3.8。
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import queue
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (  # noqa: E402
    Pipeline, analyze, build_tasks, dw, ecm_factor, fmt_sec, group_count,
    is_prime, parse_number, pollard_pm1, pollard_rho_brent, render_group,
    render_summary, run_group, short, InputError, Abort,
)


# ------------------------------------------------------------------ 轻量事件（供自检用）

class _Ev:
    """与 multiprocessing.Event 接口兼容的轻量事件，用于单进程自检。"""

    def __init__(self, v=False):
        self.v = v

    def is_set(self):
        return self.v

    def set(self):
        self.v = True

    def clear(self):
        self.v = False


class _Q:
    def __init__(self):
        self.items = []

    def put_nowait(self, m):
        self.items.append(m)


def _mk_task(k, lo, hi, target=3, mode="full", tlimit=0, algo="auto", detail=False):
    return {"seq": k, "k": k, "lo": lo, "hi": hi, "target": target, "mode": mode,
            "tlimit": tlimit, "algo": algo, "detail": detail}


# ------------------------------------------------------------------ 自检（FR 验收清单 1）

def selftest(verbose=True) -> int:
    results = []

    def check(name, cond, info=""):
        results.append((name, bool(cond), info))
        if verbose:
            print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                                   (" —— " + info) if (info and not cond) else ""))

    print("=" * 70)
    print("自检开始（缩写 / 素性 / 分解 / 扫描正确性 / 对齐 / 限时 / 跳过 / 流水线）")
    print("=" * 70)

    # ---- 1. 输入解析与大数缩写（FR-1 / FR-6）
    print("\n[1] 输入解析与大数缩写")
    cases = [("10^100", 10 ** 100), ("2^64", 2 ** 64), ("1e50", 10 ** 50),
             ("1,234,567", 1234567), ("123456789", 123456789),
             ("1_000", 1000), (" 10^12 ", 10 ** 12)]
    ok = True
    for text, want in cases:
        try:
            got = parse_number(text)
        except Exception as e:
            got = None
        if got != want:
            ok = False
            check("解析 %r" % text, False, "得到 %r" % (got,))
    check("输入解析（7 组写法）", ok)
    try:
        parse_number("abc")
        check("非法输入应报错", False)
    except InputError:
        check("非法输入应报错", True)

    abbr = [(10 ** 21, "10^21"), (10 ** 21 + 1, "10^21+1"),
            (5 * 10 ** 103, "5×10^103"), (10 ** 22 - 1, "10^22-1"),
            (123456789, "123456789"),
            (int("9090909" + "0" * 95), "9.090909×10^101")]
    for val, want in abbr:
        got = short(val)
        check("缩写 %s" % want, got == want, "得到 %s" % got)

    # ---- 2. 素性检测（FR 第 7 节）
    print("\n[2] 素性检测（Miller-Rabin）")
    for p in (2, 3, 97, 1000003, 32416190071, 2 ** 61 - 1, 10 ** 12 + 39):
        check("质数 %s 判定为质数" % short(p), is_prime(p) is True)
    for c in (1, 4, 561, 1000001, 2 ** 64, (10 ** 15 + 1)):
        check("合数 %s 判定为合数" % short(c), is_prime(c) is False)

    # ---- 3. 因子寻找与分解（FR-3 / FR-17）
    print("\n[3] 因子寻找与分解")
    f = pollard_pm1(1000003 * 1000033, 50000, None)
    r = pollard_rho_brent(1000003 * 1000033, None)
    check("p-1 / rho 至少有一个能分解 13 位半质数",
          (f not in (None, 1)) or (r not in (None, 1)),
          "p-1=%s rho=%s" % (f, r))
    semiprime = 1000003 * 1000033
    got = pollard_rho_brent(semiprime, None)
    check("rho 找到的因子确实是因子",
          got not in (None, 1) and semiprime % got == 0 and 1 < got < semiprime)
    ef = ecm_factor(1000003 * 1000033, 2000, 30, None)
    check("ECM 能在 30 条曲线内找到小因子（或返回 None 不崩溃）",
          ef is None or (semiprime % ef == 0 and 1 < ef < semiprime),
          "ECM=%s" % ef)

    def _eval_expr(expr):
        """把 '2^3 × 5' 这样的显示串还原为整数。"""
        total = 1
        for part in expr.split(" × "):
            part = part.strip()
            if "^" in part:
                a, b = part.split("^")
                total *= int(a) ** int(b)
            else:
                total *= int(part)
        return total

    ctx = None
    q = _Q()

    class _C:
        stop_ev, skip_ev, pause_ev = _Ev(False), _Ev(False), _Ev(False)
        seq, widx, q_ = 0, 0, q
        deadline = None
        paused = 0.0
        num_t0 = time.time()
        stage = ""
        _last_trial = 0.0
        _last_prog = 0.0

        def tick(self):
            pass

        def progress(self, s):
            pass

        def trial_tick(self, i, p, t):
            pass

    ctx = _C()

    full_cases = [2 ** 64, 1000000007, 2 ** 61 - 1, 100, 123456789,
                  semiprime, 10 ** 12 + 39, 97 * 89 * 83, 999999999989]
    ok = True
    for n in full_cases:
        tag, expr = analyze(n, "full", "auto", ctx)
        if tag == "prime":
            if not is_prime(n):
                ok = False
                check("分解 %s" % short(n), False, "标为质数但不是质数")
            continue
        if tag != "composite":
            ok = False
            check("分解 %s" % short(n), False, "标签=%s 表达式=%s" % (tag, expr))
            continue
        try:
            v = _eval_expr(expr)
        except Exception as e:
            ok = False
            check("分解 %s" % short(n), False, "无法还原表达式 %s（%s）" % (expr, e))
            continue
        if v != n:
            ok = False
            check("分解 %s" % short(n), False, "%s != %s" % (v, n))
    check("完整质因数分解（9 个数：乘积与结果一致）", ok)

    tag, expr = analyze(semiprime, "two", "auto", ctx)
    ok = (tag == "composite" and _eval_expr(expr) == semiprime
          and expr.count(" × ") == 1)
    check("两数相乘（证合数）模式：%s" % expr, ok)
    tag, expr = analyze(2 ** 61 - 1, "two", "auto", ctx)
    check("质数在两种模式下都标为「质数」", tag == "prime" and expr == "质数")
    tag, expr = analyze(1, "full", "auto", ctx)
    check("单位数 1 正确显示为「单位」", tag == "unit" and "单位" in expr)

    # ---- 4. 扫描正确性（FR-2）
    print("\n[4] 数量级扫描正确性")
    want = {0: ["2", "3", "5"], 1: ["11", "13", "17"],
            2: ["101", "103", "107"], 3: ["1009", "1013", "1019"],
            4: ["10007", "10009", "10037"]}
    ok = True
    for k, primes in want.items():
        lo = 1 if k == 0 else 10 ** k
        hi = 10 ** (k + 1) - 1
        res = run_group(_mk_task(k, lo, hi), 0, _Ev(False), _Ev(False),
                        _Ev(False), _Q())
        got = [e[1] for e in res["entries"] if e[0] == "prime"]
        if got != primes:
            ok = False
            check("数量级 10^%d 的前 3 个质数" % k, False,
                  "期望 %s 得到 %s" % (primes, got))
    check("数量级 10^0~10^4 的前 3 个质数（共 5 组）", ok)

    res = run_group(_mk_task(12, 10 ** 12, 10 ** 12), 0, _Ev(False), _Ev(False),
                    _Ev(False), _Q())
    check("只含 10^12 的最后一组：0 个质数、状态 range_end",
          res["nprime"] == 0 and res["status"] == "range_end",
          "得到 nprime=%s status=%s" % (res["nprime"], res["status"]))

    # 组内合数也按所选模式分解、并保留计数
    res = run_group(_mk_task(1, 10, 99, target=3), 0, _Ev(False), _Ev(False),
                    _Ev(False), _Q())
    # 10~17 共 8 个数：质数 11/13/17，合数 10/12/14/15/16 共 5 个
    check("组内合数计数正确（前 3 个质数=11,13,17，合数 5 个，检查 8 个数）",
          res["ncomp"] == 5 and res["checked"] == 8,
          "ncomp=%s checked=%s" % (res["ncomp"], res["checked"]))

    # ---- 5. 组内对齐（FR-5）
    print("\n[5] 组内 = 号与用时列对齐")
    fake = {
        "seq": 3, "k": 3, "lo_s": "10^3", "hi_s": "10^4-1",
        "entries": [("prime", "1009", "质数", 0.01),
                    ("composite", "1000000000000000000000000000001",
                     "3 × 107 × 1000003", 1.25),
                    ("unit", "1", "单位：既不是质数也不是合数", 0.0),
                    ("composite", "1010", "2 × 5 × 101", 0.02)],
        "ncomp": 2, "durs": [("1009", 0.01), ("1011", 2.5), ("1010", 0.02)],
        "nprime": 1, "checked": 4, "target": 3, "status": "done",
        "elapsed": 3.8, "widx": 0,
    }
    lines = render_group(fake, True)
    body = [t for t, _ in lines if " = " in t]
    # 显示列 = 前缀的显示宽度（中文按 2 倍计）
    eq_idx = {dw(t[:t.index(" = ")]) for t in body}
    y_idx = {dw(t[:t.index("用时")]) for t in body}
    check("组内所有 = 号显示列对齐", len(eq_idx) == 1, "列位置集合 %s" % eq_idx)
    check("组内所有「用时」列对齐", len(y_idx) == 1, "列位置集合 %s" % y_idx)

    # ---- 6. 每组限时（FR-8）
    print("\n[6] 每组限时")
    t0 = time.time()
    res = run_group(_mk_task(30, 10 ** 30, 10 ** 31 - 1, tlimit=0.05), 0,
                    _Ev(False), _Ev(False), _Ev(False), _Q())
    dt = time.time() - t0
    check("限时 0.05 秒后立刻返回并标注 timeout",
          res["status"] == "timeout" and dt < 5.0,
          "status=%s 用时=%.2fs" % (res["status"], dt))
    txt = "\n".join(t for t, _ in render_group(res, False))
    check("限时结果给出「限时已到」橙色提示", "限时已到" in txt)

    # ---- 7. 跳过（FR-9）
    print("\n[7] 跳过当前（不级联）")

    class _AutoSkip(_Ev):
        """第 n 次查询后自动置位，模拟界面按下「跳过」。"""

        def __init__(self, after):
            _Ev.__init__(self, False)
            self.after = after
            self.n = 0

        def is_set(self):
            self.n += 1
            if self.n >= self.after:
                self.v = True
            return self.v

    res = run_group(_mk_task(2, 100, 999, target=3), 0, _Ev(False), _Ev(False),
                    _AutoSkip(5), _Q())
    check("跳过事件生效后组状态为 skipped",
          res["status"] == "skipped", "status=%s" % res["status"])
    txt = "\n".join(t for t, _ in render_group(res, False))
    check("跳过结果给出橙色标注", "已跳过本组" in txt)

    # 停止
    res = run_group(_mk_task(2, 100, 999, target=3), 0, _AutoSkip(3), _Ev(False),
                    _Ev(False), _Q())
    check("停止事件生效后组状态为 stopped",
          res["status"] == "stopped", "status=%s" % res["status"])

    # ---- 8. 多进程流水线（FR-7）
    print("\n[8] 多进程流水线（顺序保证 / 汇总）")

    def run_pipe(tasks, nproc, target=3, mode="full", tlimit=0, algo="auto",
                 detail=False, budget=180.0, skip_after=None):
        pipe = Pipeline(tasks, nproc, target, mode, tlimit, algo, detail)
        pipe.start()
        t0 = time.time()
        order = []
        out = []
        skipped = False
        while time.time() - t0 < budget:
            pipe.pump()
            for r in pipe.ordered_results():
                order.append(r["seq"])
                out.append(r)
            if skip_after is not None and not skipped and pipe.active:
                time.sleep(skip_after)
                pipe.skip_all()
                skipped = True
            if pipe.done_count >= pipe.total and pipe.next_seq >= pipe.total:
                break
            time.sleep(0.02)
        for r in pipe.ordered_results():
            order.append(r["seq"])
            out.append(r)
        pipe.terminate_all(timeout=3.0)
        return out, order

    n = 10 ** 12
    out, order = run_pipe(build_tasks(n), 4)
    check("流水线跑完 10^12 的全部 13 个数量级",
          len(out) == 13, "完成 %d 组" % len(out))
    check("结果按数量级顺序输出", order == list(range(13)), "顺序=%s" % order)
    total_primes = sum(r["nprime"] for r in out)
    check("共找到 36 个质数（12 个完整组 × 3）", total_primes == 36,
          "得到 %d" % total_primes)
    check("最后一组（只含 10^12）未找满 3 个质数",
          out[-1]["nprime"] == 0 and out[-1]["status"] == "range_end",
          "nprime=%s status=%s" % (out[-1]["nprime"], out[-1]["status"]))

    # 跳过当前：构造一个超长的组，跑起来后跳过
    hard = [{"seq": 0, "k": 200, "lo": 10 ** 200, "hi": 10 ** 201 - 1}]
    out2, _o2 = run_pipe(hard, 1, target=3, tlimit=0, budget=60.0, skip_after=1.5)
    check("流水线中「跳过当前」能让进行中的组立即退出",
          len(out2) == 1 and out2[0]["status"] == "skipped",
          "状态=%s" % ([r["status"] for r in out2],))

    # 汇总文本
    summary = {"mode_s": "完整质因数分解", "algo_s": "自动（p-1→rho）", "nproc": 4,
               "tlimit_s": "10 秒", "target": 3, "ngroups": 13, "checked": 100,
               "nprimes": 36, "incomplete": 1, "calc": 12.3, "wall": 20.0,
               "stopped": False}
    stxt = "\n".join(t for t, _ in render_summary(summary))
    check("任务汇总包含模式/进程数/限时/质数数/未找满组数/用时",
          all(x in stxt for x in ("完整质因数分解", "4", "10 秒", "36", "1", "秒")))

    # ---- 结果
    print("\n" + "=" * 70)
    passed = sum(1 for _, okk, _ in results if okk)
    total = len(results)
    for name, okk, info in results:
        if not okk:
            print("  FAIL: %s %s" % (name, info))
    print("自检结束：%d/%d 项通过" % (passed, total))
    print("=" * 70)
    return 0 if passed == total else 1


# ------------------------------------------------------------------ 无界面运行

def headless(text_n, procs, target, mode, tlimit, algo, detail, budget=3600.0):
    n = parse_number(text_n)
    tasks = build_tasks(n)
    print("N = %s（%d 位，共 %d 个数量级）" % (short(n), group_count(n), len(tasks)))
    print("模式=%s 算法=%s 每组限时=%s 每组找质数=%d 进程=%d 明细=%s"
          % (mode, algo, (tlimit if tlimit else "不限时"), target, procs, detail))
    print("-" * 70)
    pipe = Pipeline(tasks, procs, target, mode, tlimit, algo, detail)
    pipe.start()
    t0 = time.time()
    calc = 0.0
    checked = 0
    nprime = 0
    incomplete = 0
    next_seq = 0
    while time.time() - t0 < budget:
        for m in pipe.pump():
            if m[0] == "milestone":
                print("  ★ %s" % m[3])
        for r in pipe.ordered_results():
            calc += r["elapsed"]
            checked += r["checked"]
            nprime += r["nprime"]
            if r["nprime"] < r["target"]:
                incomplete += 1
            for text, _tag in render_group(r, detail):
                print(text)
            next_seq = r["seq"] + 1
        if pipe.done_count >= pipe.total and pipe.next_seq >= pipe.total:
            break
        time.sleep(0.03)
    for r in pipe.ordered_results():
        calc += r["elapsed"]
        checked += r["checked"]
        nprime += r["nprime"]
        for text, _tag in render_group(r, detail):
            print(text)
    pipe.terminate_all(timeout=3.0)
    print("-" * 70)
    for text, _tag in render_summary({
        "mode_s": mode, "algo_s": algo, "nproc": procs,
        "tlimit_s": (tlimit if tlimit else "不限时"), "target": target,
        "ngroups": len(tasks), "checked": checked, "nprimes": nprime,
        "incomplete": incomplete, "calc": calc, "wall": time.time() - t0,
        "stopped": False}):
        print(text)
    return 0


# ------------------------------------------------------------------ main

def _fix_stdout():
    """windowed（无控制台）打包时 sys.stdout 为 None：把输出重定向到 exe 旁的日志文件（行缓冲）。"""
    if getattr(sys, "frozen", False) and sys.stdout is None:
        import io
        try:
            path = os.path.join(os.path.dirname(sys.executable),
                                "大数分解工具_输出.txt")
            f = io.open(path, "w", encoding="utf-8", buffering=1)
            sys.stdout = sys.stderr = f
            print("（本程序以无控制台方式运行，以下输出被写入该文件）\n", flush=True)
        except Exception:
            sys.stdout = io.StringIO()
            sys.stderr = sys.stdout
        return True
    return False


def main(argv=None):
    redirected = _fix_stdout()
    ap = argparse.ArgumentParser(description="大数分解可视化工具")
    ap.add_argument("--selftest", action="store_true", help="运行自检用例后退出")
    ap.add_argument("--demo", default="", help="启动界面并自动开始分解，例如 --demo=10^12")
    ap.add_argument("--headless", default="", help="无界面跑完整个流水线，例如 --headless=10^12")
    ap.add_argument("--detail", action="store_true", help="默认勾选「列出合数明细」")
    ap.add_argument("--procs", type=int, default=0, help="进程数（默认 CPU 逻辑核心数）")
    ap.add_argument("--target", type=int, default=3, help="每组找几个质数（默认 3）")
    ap.add_argument("--mode", default="full", choices=["full", "two"], help="分解模式")
    ap.add_argument("--algo", default="auto",
                    choices=["auto", "rho", "pm1", "ecm", "deep"], help="找因子算法")
    ap.add_argument("--tlimit", type=float, default=0, help="每组限时秒数，0 表示不限时")
    args = ap.parse_args(argv)

    if args.selftest:
        rc = selftest()
        if redirected:
            sys.stdout.flush()
        return rc

    if args.headless:
        procs = args.procs or (os.cpu_count() or 4)
        rc = headless(args.headless, procs, args.target, args.mode,
                      args.tlimit, args.algo, args.detail)
        if redirected:
            sys.stdout.flush()
        return rc

    import tkinter as tk
    from gui import App
    root = tk.Tk()
    app = App(root, auto_start=args.demo, detail_default=args.detail)
    if args.procs:
        app.proc_var.set(args.procs)
    root.mainloop()
    return 0


if __name__ == "__main__":
    mp.freeze_support()
    sys.exit(main())
