# SVG-AI-TEST

**AI Agent × 大模型 交叉验证测试存档** —— 同一组题目，不同 AI Agent / 大模型的成品对比画廊。

🌐 **在线浏览（GitHub Pages）**：<https://wangxp7.github.io/SVG-AI-TEST/>

## 目录结构

```
├── index.html                  # 主页：分门别类展示两个案例的全部成品
├── case1-complex-plane/        # 案例一「复杂多层 SVG」（原目录 vs复杂多层SVG）
│   ├── 复杂生图或SVG提示词.txt    # 完整提示词
│   ├── *.svg                    # 各家生成的 SVG 源文件
│   ├── *.png / *.jpg            # 渲染图 / 文生图对照
│   └── Pavo-AgensVideo-2.5.mp4  # 视频路线样本
└── case2-pelican-bike/         # 案例二「鹈鹕骑自行车」（原目录 vs鹈鹕骑自行车SVG）
    ├── pelican-cycling.svg/.png # 基准原创 SVG「海风骑行」
    ├── GLM / GPT 系列图片快照     # 不同大模型图片成绩
    └── *.html                   # 各 Agent 的独立交互页
```

## 案例

| 案例 | 题目 | 形式 | 覆盖模型 / Agent |
|---|---|---|---|
| 一 · 复杂多层 SVG | 超长提示词：四发客机 + 孙悟空 + 鹈鹕骑车 + 北极熊 + 秦始皇 + 递归玻璃球等 60 余项硬约束 | SVG / 文生图 / 视频 | ZCode·GLM-5.3-Flash、WorkBuddy·DS4.1-Flash、WorkBuddy·SpaceBunny、豆包 DB 2.1 Pro、Qwen 3.8 Max、GPT-6 Astra Mid、Qoder·Qwen3.8-Flash、DeepSeek 网页版、MiniMax、GPT 文生图、Pavo Agens Video 2.5 |
| 二 · 鹈鹕骑自行车 | 「海风骑行」：系围巾的鹈鹕骑珊瑚红自行车穿过海边风景 | SVG / 动图 / 图片 / 交互 HTML | 基准原创、GLM-5.1、GPT-4o、GPT-5、GPT-5 mini、GPT-6 Astra、WorkBuddy·DS v4.1 Flash（含 3D 双版）、Luna 5.6 xhigh |

所有文件按原目录原文件名存档，保证可溯源；主页按「案例 → Agent/模型」分类展示，SVG / 图片 / 视频均可在线预览与下载。
