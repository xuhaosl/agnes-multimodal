#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跑齐全部回归测试，汇总通过数，并核对每套的项数与声明一致。

每套测试在独立子进程里运行，互不干扰。
全部通过退出码 0；任一失败、或实测项数与声明不符，退出码 1。

只用标准库，不需要 pip 安装任何东西。
"""
import re
import subprocess
import sys
from pathlib import Path

PY = sys.executable
HERE = Path(__file__).resolve().parent

# (文件名, 声明项数, 说明) —— 声明项数与 README「已验证」段必须一致，
# 数字不准会直接判失败，免得文档里的数字靠印象写。
SUITES = [
    ("test_hermes_key.py", 13, "Hermes 现场配置复用同一把 Key：形态覆盖 + 误用防护"),
    ("test_hermes_real_config.py", 12, "真实部署结构（序列 + key_env）与升级改名"),
    ("test_readonly_hermes.py", 22, "只读铁律：写入被拦 + 目标文件字节零变化"),
    ("test_key_bootstrap.py", 20, "密钥自举：检查 / 写入 / 覆盖 / 缺失引导"),
    ("test_domain_guard.py", 29, "域名判定：后缀边界 + 三道守卫 + 白名单"),
    ("test_guard_bypass.py", 12, "守卫绕过路径：--resume / --poll-url / 环境变量 走真实命令行"),
    ("test_py39_syntax.py", 3, "Python 3.9 兼容：语法层 + 注解层 + 3.10+ API 黑名单"),
    ("test_zero_deps.py", 6, "零依赖：空集 + 判据输入 + scripts/tests 无第三方 + 顺序逻辑 + 真实探针"),
]

failed = []
total = 0

for name, declared, desc in SUITES:
    print("=" * 68, flush=True)
    print(f"▶ {name}   —— {desc}", flush=True)
    print("=" * 68, flush=True)
    p = subprocess.run([PY, str(HERE / name)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    out = (p.stdout or "") + (p.stderr or "")
    print(out, end="", flush=True)

    m = re.search(r"合计 (\d+)/(\d+) 项通过", out)
    skipped = re.search(r"跳过 (\d+) 项", out)
    skipped_n = int(skipped.group(1)) if skipped else 0

    if p.returncode != 0:
        failed.append(f"{name} —— 有断言失败")
    elif not m:
        failed.append(f"{name} —— 没打印汇总行（脚本异常退出？）")
    elif int(m.group(2)) + skipped_n != declared:
        failed.append(f"{name} —— 实测 {int(m.group(2)) + skipped_n} 项 ≠ 声明 {declared} 项")
    else:
        total += declared
    print(flush=True)

print("=" * 68)
if failed:
    print("未通过：")
    for f in failed:
        print("  ✗", f)
    sys.exit(1)
print(f"全部 {len(SUITES)} 套通过，合计 {total} 项断言")
