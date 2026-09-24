#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""针对真实部署 config.yaml 结构的专项验证。

实测结构（2026-09-23 采样自真实部署，Key 已换成假值）：
    custom_providers:            <- 顶层键是**序列**，不是映射
    - name: Agnes                <- name 是用户自己起的
      base_url: https://api.agnes-ai.cn/v1
      key_env: HERMES_CUSTOM_API_AGNES_AI_CN_API_KEY
      model: agnes-2.5-flash
      api_mode: chat_completions
      models:
        agnes-2.0-flash: {}
        ... （11 个）
      models_discovered: true

两个易变点，本文件就围绕它们设计：
  ① Hermes 升级后这个键名可能变  → 用例 3
  ② name 是用户自己命名的        → 用例 2

仍然坚持：用临时 HERMES_HOME，绝不碰本机真实配置。
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PY = sys.executable
COMMON = Path(__file__).resolve().parent.parent / "scripts" / "agnes_common.py"

REAL_KEY = "sk-realAGNESkeyREAL0001"
VAR_NAME = "HERMES_CUSTOM_API_AGNES_AI_CN_API_KEY"

# 真实配置里的 11 个模型，一字不改
MODELS_BLOCK = "".join(f"    {m}: {{}}\n" for m in [
    "agnes-2.0-flash", "agnes-2.5-flash", "agnes-2.5-pro", "agnes-2.5-pro-alpha",
    "agnes-2.5-pro-beta", "agnes-3.0-flash", "agnes-image-2.1-flash",
    "agnes-image-2.5-flash", "agnes-video-2.5", "agnes-video-2.5-flash",
    "agnes-video-v2.0",
])


def entry(name="Agnes", section_key="custom_providers", dash=True,
          base="https://api.agnes-ai.cn/v1", key_env=VAR_NAME):
    """按真实写法拼一个 provider 条目。dash=True 用序列（真实写法）。"""
    head = f"- name: {name}\n  " if dash else f"  {name}:\n    name: {name}\n    "
    body = (f"base_url: {base}\n  key_env: {key_env}\n  model: agnes-2.5-flash\n"
            f"  api_mode: chat_completions\n  models:\n")
    if not dash:
        body = (f"base_url: {base}\n    key_env: {key_env}\n    model: agnes-2.5-flash\n"
                f"    api_mode: chat_completions\n    models:\n")
        models = "".join(f"      {m}: {{}}\n" for m in [
            "agnes-2.0-flash", "agnes-2.5-flash", "agnes-2.5-pro", "agnes-2.5-pro-alpha",
            "agnes-2.5-pro-beta", "agnes-3.0-flash", "agnes-image-2.1-flash",
            "agnes-image-2.5-flash", "agnes-video-2.5", "agnes-video-2.5-flash",
            "agnes-video-v2.0"])
        return f"{section_key}:\n{head}{body}{models}  models_discovered: true\n"
    return f"{section_key}:\n{head}{body}{MODELS_BLOCK}  models_discovered: true\n"


results = []


# 同 test_hermes_key.py：本机真实凭据与 Hermes 的 provider 变量族一律不进子进程，
# 否则「不该命中」的用例会被宿主导出的 key_env 变量喂出假结果。
SCRUB_KEYS = ("AGNES_API_KEY", "AGNES_AI_API_KEY", "AGNES_CONFIG_PATH", "HERMES_HOME")


def _clean_env():
    return {k: v for k, v in os.environ.items()
            if k not in SCRUB_KEYS and not k.startswith("HERMES_CUSTOM_API")}


def run(env=None):
    base = _clean_env()
    if env:
        base.update(env)
    p = subprocess.run([PY, str(COMMON), "--check-key"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=base, timeout=90)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("[PASS] " if cond else "[FAIL] ") + name + (f"\n         <- {detail}" if detail and not cond else ""))


def probe(name, cfg, dotenv, expect_key, expect_src="", extra_env=None):
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        if cfg is not None:
            (home / "config.yaml").write_text(cfg, encoding="utf-8")
        if dotenv is not None:
            (home / ".env").write_text(dotenv, encoding="utf-8")
        env = {"HERMES_HOME": str(home), "HOME": str(home), "USERPROFILE": str(home)}
        if extra_env:
            env.update(extra_env)
        code, out = run(env)
        if expect_key is None:
            check(name + " → 不应命中", code == 1 and "[agnes-need-key]" in out, out[:300])
        else:
            ok = code == 0 and expect_key[:6] in out and expect_key[-4:] in out
            extra = (expect_src in out) if ok and expect_src else True
            check(name, ok and extra, out[:300])


DOTENV = f"{VAR_NAME}={REAL_KEY}\n"

print("=" * 62)
print("A. 真实结构（序列 + key_env）")
print("=" * 62)

probe("A1 原样复刻：custom_providers 序列 + key_env 指向 .env",
      entry(), DOTENV, REAL_KEY, "custom_providers")

probe("A2 同上但 key_env 的值直接来自环境变量（Hermes 注入场景）",
      entry(), None, REAL_KEY, "custom_providers", extra_env={VAR_NAME: REAL_KEY})

print()
print("=" * 62)
print("B. 易变点（升级改名 / 用户自定义 name）")
print("=" * 62)

probe("B1 name 换成纯自定义（name: 我的图像模型）仍应命中",
      entry(name="我的图像模型"), DOTENV, REAL_KEY, "custom_providers")

probe("B2 name 换成完全无关（name: primary-llm）仍应命中",
      entry(name="primary-llm"), DOTENV, REAL_KEY, "custom_providers")

probe("B3 顶层键改名 llm_providers（模拟升级后改名）仍应命中",
      entry(section_key="llm_providers"), DOTENV, REAL_KEY, "llm_providers")

probe("B4 顶层键改名 providers 且用映射写法仍应命中",
      entry(section_key="providers", dash=False), DOTENV, REAL_KEY, "providers")

probe("B5 api_key 明文代替 key_env 应命中",
      "custom_providers:\n"
      "- name: Agnes\n"
      "  base_url: https://api.agnes-ai.cn/v1\n"
      f"  api_key: {REAL_KEY}\n"
      "  model: agnes-2.5-flash\n"
      "  models:\n    agnes-image-2.5-flash: {}\n",
      None, REAL_KEY, "custom_providers")

probe("B6 去掉 base_url，靠 key_env 变量名 + models 自证应命中",
      "custom_providers:\n"
      "- name: Agnes\n"
      f"  key_env: {VAR_NAME}\n"
      "  models:\n    agnes-image-2.5-flash: {}\n",
      DOTENV, REAL_KEY, "custom_providers")

print()
print("=" * 62)
print("C. 序列里混着别家 provider —— 只准取 Agnes 那把")
print("=" * 62)

AGNES_ENTRY = ("- name: Agnes\n"
               "  base_url: https://api.agnes-ai.cn/v1\n"
               f"  key_env: {VAR_NAME}\n"
               "  model: agnes-2.5-flash\n"
               "  api_mode: chat_completions\n"
               "  models:\n" + MODELS_BLOCK)

MIXED = (
    "custom_providers:\n"
    "- name: DeepSeek\n"
    "  base_url: https://api.deepseek.com/v1\n"
    "  key_env: DEEPSEEK_KEY\n"
    "  models:\n    deepseek-chat: {}\n"
    + AGNES_ENTRY
)
probe("C1 第二个条目才是 Agnes，必须取 Agnes 的 Key",
      MIXED, DOTENV + "DEEPSEEK_KEY=sk-deepseekSHOULDNOTUSE\n", REAL_KEY, "custom_providers")

probe("C2 只有别家 provider，不应命中",
      "custom_providers:\n"
      "- name: DeepSeek\n"
      "  base_url: https://api.deepseek.com/v1\n"
      "  key_env: DEEPSEEK_KEY\n"
      "  models:\n    deepseek-chat: {}\n",
      "DEEPSEEK_KEY=sk-deepseekSHOULDNOTUSE\n", None)

probe("C3 base_url 改指向别家（配串行），即便 name/models 像也应拒绝",
      entry(base="https://api.deepseek.com/v1"), DOTENV, None)

probe("C4 key_env 指向的变量在 .env 里不存在，不应命中",
      entry(), f"OTHER_VAR={REAL_KEY}\n", None)

print()
print("=" * 62)
print("D. 源码信息（不改任何文件，仅读）")
print("=" * 62)
import re as _re
txt = COMMON.read_text(encoding="utf-8", errors="replace")
print(f"   脚本内是否残留真实 Key 形态：{'是' if _re.search(r'sk-[A-Za-z0-9]{20,}', txt) else '否（干净）'}")

print("-" * 62)
passed = sum(1 for _, ok in results if ok)
print(f"合计 {passed}/{len(results)} 项通过")
if passed != len(results):
    sys.exit(1)
