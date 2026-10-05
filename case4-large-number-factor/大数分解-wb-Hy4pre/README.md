# 大数分解可视化工具

按《产品需求设计-ZCode-GLM5.3Flash-v1.8.md》实现。纯标准库，可在 Windows 上独立运行。

## 快速开始

1. 直接双击运行：`release/大数分解可视化工具.exe`
2. 或从命令行运行：
   ```
   大数分解可视化工具.exe --selftest
   大数分解可视化工具.exe --demo=10^12
   大数分解可视化工具.exe --headless=10^30 --procs=4 --tlimit=10
   ```

## 命令行参数

| 参数 | 说明 |
|---|---|
| `--selftest` | 运行自检用例，命令行输出结果 |
| `--demo=N` | 启动界面并自动开始分解 N（如 `10^12`） |
| `--headless=N` | 无界面跑完整个流水线并打印结果 |
| `--detail` | 默认勾选「列出合数明细」 |
| `--procs=N` | 进程数（默认 CPU 逻辑核心数） |
| `--target=N` | 每组找几个质数（默认 3） |
| `--mode=full/two` | 完整质因数分解 / 两数相乘（证合数） |
| `--algo=auto/rho/pm1/ecm/deep` | 找因子算法 |
| `--tlimit=S` | 每组限时秒数，0 表示不限时 |

> 注：以无控制台方式（`--windowed`）运行时，`--selftest` / `--headless` 的输出会写入 exe 同目录的「大数分解工具_输出.txt」。

## 文件说明

| 文件 | 作用 |
|---|---|
| `factor_gui.py` | 程序入口、命令行、自检 |
| `core.py` | 输入解析、数论算法、多进程流水线、结果渲染 |
| `gui.py` | Tkinter 界面、主题、进度面板、资源监控 |
| `算法调研报告.md` | 试除/Miller-Rabin/rho/p-1/ECM/QS/GNFS 对比 |
| `变更日志.md` | 需求实现与变更记录 |
| `5轮自检报告.md` | 自检过程与结果 |

## 运行环境

- Windows 10/11 x64
- 无需 Python 环境（已打包为独立 exe）
- 如需从源码运行：Python >= 3.8，纯标准库，Windows 优先
