#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""README「兼容性：全部脚本通过 Python 3.9 语法校验」的可复现证据。

三层检查：

  ① 语法层 —— ast.parse(..., feature_version=(3, 9))
     拦 `match` 这类**语法级**新增。

  ② 注解层 —— PEP 604 联合类型（`str` 竖线 `None`）是 3.10 才有的**运行时**能力。
     语法层**拦不住它**：它在语法上只是普通的按位或运算。真正的雷在运行时 ——
     没有 `from __future__ import annotations` 时，Python 3.9 会在 `def` 执行那一刻
     求值注解并抛 TypeError，也就是 **import 阶段就崩**。
     这一层就是为它专设的。

  ③ API 黑名单 —— 3.10 / 3.11 才引入的标准库 API 字面扫描，只扫 scripts/。

为什么 ② 不能只靠 ①（实测教训）：
    在本机实测过，ast.parse 配 feature_version=(3,9) 解析
    「def f(x: str | None) -> str」**不报错**。只跑 ① 会给出「3.9 通过」的假绿灯。
    更早还栽过更蠢的一次：用 head -20 人工核对三个脚本有没有 future import，
    而 agnes_common.py 的那一行在第 21 行 —— 窗口截断，结论正好反了。
    所以这里用全文件 AST 扫描，不再靠「看一眼」。

局限（别把它当成完整的 3.9 验证器）：
    本机通常没有 3.9 解释器，无法真实执行；③ 只覆盖写进 API_DENYLIST 的少数 API，
    不做穷举。要 100% 确认，最终得在 3.9 环境里 import 一次脚本。
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = sorted((ROOT / "scripts").glob("*.py"))
TEST_FILES = sorted((ROOT / "tests").glob("*.py"))
SYNTAX_TARGETS = SCRIPTS + TEST_FILES

FUTURE_LINE = "from __future__ import annotations"

# 3.10 / 3.11 才引入的高风险标准库 API。
# 只扫 scripts/（README 承诺的对象是「脚本」），这同时也避免扫描器命中自身的规则表。
API_DENYLIST = [
    (re.compile(r"\bzip\s*\(\s*[^)]*\bstrict\s*="), "zip 的 strict 参数（3.10+）"),
    (re.compile(r"\bitertools\s*\.\s*pairwise\b"), "itertools 的 pairwise（3.10+）"),
    (re.compile(r"\basyncio\s*\.\s*TaskGroup\b"), "asyncio 的 TaskGroup（3.11+）"),
    (re.compile(r"\bException[A-Za-z]*Group\b"), "异常组类型（3.11+）"),
    (re.compile(r"\btoml" r"lib\b"), "内置 TOML 解析（3.11+）"),
]

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("[PASS] " if cond else "[FAIL] ") + name + (f"\n         <- {detail}" if detail and not cond else ""))


def annotations_of(tree):
    """收集所有注解表达式节点：函数参数、返回值、变量注解。"""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            args = list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)
            if a.vararg:
                args.append(a.vararg)
            if a.kwarg:
                args.append(a.kwarg)
            out += [x.annotation for x in args if x.annotation is not None]
            if node.returns is not None:
                out.append(node.returns)
        elif isinstance(node, ast.AnnAssign) and node.annotation is not None:
            out.append(node.annotation)
    return out


def pep604_lines(tree):
    """注解里出现顶层按位或（联合类型写法）的行号。"""
    lines = set()
    for ann in annotations_of(tree):
        for sub in ast.walk(ann):
            if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr):
                lines.add(sub.lineno)
    return sorted(lines)


# ---------------------------------------------------------------- ① 语法层
syntax_bad = []
for f in SYNTAX_TARGETS:
    rel = f.relative_to(ROOT).as_posix()
    try:
        ast.parse(f.read_text(encoding="utf-8"), filename=str(f), feature_version=(3, 9))
    except SyntaxError as e:
        syntax_bad.append(f"{rel}:{e.lineno} {e.msg}")
check(f"语法层：{len(SYNTAX_TARGETS)} 个文件都能被 3.9 语法解析",
      not syntax_bad, "; ".join(syntax_bad[:3]))

# ---------------------------------------------------------------- ② 注解层
missing_future = []
for f in SYNTAX_TARGETS:
    src = f.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(f))
    except SyntaxError:
        continue
    hits = pep604_lines(tree)
    if hits and FUTURE_LINE not in src:
        missing_future.append(f"{f.relative_to(ROOT).as_posix()} 行 {hits[:4]}")
check("注解层：所有联合类型写法都有 future import 兜底（3.9 下不会在 import 时崩）",
      not missing_future, "; ".join(missing_future[:3]))

# ---------------------------------------------------------------- ③ API 黑名单
api_bad = []
for f in SCRIPTS:
    src = f.read_text(encoding="utf-8")
    for pat, why in API_DENYLIST:
        m = pat.search(src)
        if m:
            api_bad.append(f"{f.relative_to(ROOT).as_posix()}:{src[:m.start()].count(chr(10)) + 1} {why}")
check(f"API 层：{len(SCRIPTS)} 个脚本未使用 3.10+ 专有 API",
      not api_bad, "; ".join(api_bad[:3]))

# ---------------------------------------------------------------- 汇总
passed = sum(1 for _, ok in results if ok)
print(f"\n覆盖：scripts {len(SCRIPTS)} 个 + tests {len(TEST_FILES)} 个")
print(f"合计 {passed}/{len(results)} 项通过")
sys.exit(0 if passed == len(results) else 1)
