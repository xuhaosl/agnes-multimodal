#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读铁律验证：技能不许写宿主的任何配置文件。

设计约束（2026-09-23）：读取宿主应用的配置文件（Hermes 的 config.yaml / .env）
时只允许只读；所有写入通道都必须被硬拦，以免破坏宿主配置。

本文件不只验「拒绝成功」，更验**拒绝之后目标文件字节零变化** ——
因为原子写入会在同目录建 .tmp，光拦「覆盖」不够，得确认连临时文件都没落下。

仍用临时 HOME + 受控 env，绝不碰本机真实 Hermes。
"""
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PY = sys.executable
COMMON = Path(__file__).resolve().parent.parent / "scripts" / "agnes_common.py"

REAL_KEY = "sk-shouldNEVERbewritten0001"
READONLY_EXIT = 4

results = []

# 一份「像真配置」的 config.yaml：带注释、带缩进、序列写法，一字不改地留着
CONFIG_YAML = """\
# Hermes Agent 配置
custom_providers:
- name: Agnes
  base_url: https://api.agnes-ai.cn/v1
  key_env: HERMES_CUSTOM_API_AGNES_AI_CN_API_KEY
  model: agnes-2.5-flash
  api_mode: chat_completions
  models:
    agnes-image-2.5-flash: {}
    agnes-video-2.5-flash: {}
  models_discovered: true
"""
DOTENV = "HERMES_CUSTOM_API_AGNES_AI_CN_API_KEY=sk-theRealKeyInEnv0002\n"


def sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def run(args, env_extra=None):
    base = {k: v for k, v in os.environ.items()
            if k not in ("AGNES_API_KEY", "AGNES_AI_API_KEY", "AGNES_CONFIG_PATH", "HERMES_HOME")}
    if env_extra:
        base.update(env_extra)
    p = subprocess.run([PY, str(COMMON)] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=base, timeout=90)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("[PASS] " if cond else "[FAIL] ") + name + (f"\n         <- {detail}" if detail and not cond else ""))


def fresh_home():
    """建一个装着 Hermes 配置的临时 HOME，返回 (临时目录, home) —— 调用方负责清理。"""
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    hh = root / ".hermes"
    hh.mkdir(parents=True)
    (hh / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    (hh / ".env").write_text(DOTENV, encoding="utf-8")
    return td, root, hh


def protected_case(name, args, env_extra_builder=None, target_rel="config.yaml"):
    """跑一次，断言退出码==4 且目标文件与原目录快照完全一致。"""
    td, root, hh = fresh_home()
    try:
        target = hh / target_rel
        before_sha, before_names = sha(target), sorted(p.name for p in hh.rglob("*"))
        env = {"HERMES_HOME": str(hh), "HOME": str(root), "USERPROFILE": str(root)}
        if env_extra_builder:
            env.update(env_extra_builder(hh))
        code, out = run(args(hh) if callable(args) else args, env)
        after_sha, after_names = sha(target), sorted(p.name for p in hh.rglob("*"))
        ok = (code == READONLY_EXIT
              and "只读权限" in out
              and before_sha == after_sha
              and before_names == after_names)
        check(name, ok,
              f"code={code}（期望 {READONLY_EXIT}） sha_same={before_sha == after_sha} "
              f"目录新增={set(after_names) - set(before_names)} out={out[:200]}")
    finally:
        td.cleanup()


print("=" * 64)
print("A. 必须拒绝：目标是 Hermes 的文件")
print("=" * 64)

protected_case("A1 --config-path 直指 Hermes 的 config.yaml",
               lambda hh: ["--set-key", REAL_KEY, "--config-path", str(hh / "config.yaml")])

protected_case("A2 --config-path 直指 Hermes 的 .env",
               lambda hh: ["--set-key", REAL_KEY, "--config-path", str(hh / ".env")],
               target_rel=".env")

protected_case("A3 环境变量 AGNES_CONFIG_PATH 指向 config.yaml",
               ["--set-key", REAL_KEY],
               env_extra_builder=lambda hh: {"AGNES_CONFIG_PATH": str(hh / "config.yaml")})

protected_case("A4 大小写变体 CONFIG.YAML 同样要拦",
               lambda hh: ["--set-key", REAL_KEY, "--config-path", str(hh / "CONFIG.YAML")])

protected_case("A5 深层嵌套 .hermes/sub/config.yaml 同样要拦",
               lambda hh: ["--set-key", REAL_KEY, "--config-path", str(hh / "sub" / "config.yaml")])

protected_case("A6 指向 Hermes 的 settings.yaml 也要拦",
               lambda hh: ["--set-key", REAL_KEY, "--config-path", str(hh / "settings.yaml")])

# 关键：确认没被覆盖成 JSON，注释与序列结构还在
td, root, hh = fresh_home()
try:
    env = {"HERMES_HOME": str(hh), "HOME": str(root), "USERPROFILE": str(root)}
    run(["--set-key", REAL_KEY, "--config-path", str(hh / "config.yaml")], env)
    after = (hh / "config.yaml").read_text(encoding="utf-8")
    check("A7 拒绝后 config.yaml 仍是原文（注释/序列结构未损）",
          after == CONFIG_YAML, repr(after[:160]))
    check("A8 拒绝后 Hermes 目录没有多出 .tmp 等残留",
          not any(p.name.endswith(".tmp") for p in hh.rglob("*")),
          str([p.name for p in hh.rglob("*")]))
finally:
    td.cleanup()

print()
print("=" * 64)
print("B. 必须放行：正常写入点不受影响")
print("=" * 64)

td, root, hh = fresh_home()
try:
    env = {"HERMES_HOME": str(hh), "HOME": str(root), "USERPROFILE": str(root)}
    sub = hh / "agnes" / "config.json"
    code, out = run(["--set-key", REAL_KEY, "--config-path", str(sub)], env)
    check("B1 Hermes 内的自有子目录 .hermes/agnes/config.json 可写",
          code == 0 and sub.is_file() and REAL_KEY in sub.read_text(encoding="utf-8"), out[:200])
    code, out = run(["--check-key"],
                    {**env, "AGNES_CONFIG_PATH": str(sub)})
    check("B2 写进去的能读回来（写入点/读取点闭环）",
          code == 0 and "sk-sho" in out and "0001" in out, out[:250])
finally:
    td.cleanup()

td, root, hh = fresh_home()
try:
    env = {"HERMES_HOME": str(hh), "HOME": str(root), "USERPROFILE": str(root)}
    other = root / "myown" / "agnes.json"
    code, out = run(["--set-key", REAL_KEY, "--config-path", str(other)], env)
    check("B3 Hermes 目录之外的任意路径可写",
          code == 0 and other.is_file(), out[:200])

    plain = root / "elsewhere" / "config.yaml"
    code, out = run(["--set-key", REAL_KEY, "--config-path", str(plain)], env)
    check("B4 不在 Hermes 目录内的 config.yaml 不误拦（只是同名）",
          code == 0 and plain.is_file(), out[:200])
finally:
    td.cleanup()

print()
print("=" * 64)
print("C. 读取路径未被越权扩大")
print("=" * 64)

src = COMMON.read_text(encoding="utf-8", errors="replace")
check("C1 对 Hermes 的读取只出现 config.yaml 与 .env",
      src.count('home / "config.yaml"') == 1 and src.count('home / ".env"') == 1)
check("C2 没有 unlink / rmtree / rename 作用在 Hermes 路径上",
      "rmtree" not in src and 'home / ".hermes' not in src)

print()
print("=" * 64)
print("D. 日志路径（追加写，最阴的一个口子）")
print("=" * 64)

SCRIPTS = str(COMMON.parent)


def run_py(snippet, env_extra):
    base = {k: v for k, v in os.environ.items()
            if k not in ("AGNES_API_KEY", "AGNES_AI_API_KEY", "AGNES_CONFIG_PATH",
                         "HERMES_HOME", "AGNES_LOG_FILE")}
    base.update(env_extra)
    prev = base.get("PYTHONPATH") or ""
    base["PYTHONPATH"] = SCRIPTS + (os.pathsep + prev if prev else "")
    p = subprocess.run([PY, "-c", snippet],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=base, timeout=90)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


td, root, hh = fresh_home()
try:
    env = {"HERMES_HOME": str(hh), "HOME": str(root), "USERPROFILE": str(root),
           "AGNES_LOG_FILE": str(hh / "config.yaml")}
    before = sha(hh / "config.yaml")
    code, out = run_py(
        "import agnes_common as a\n"
        "print('LOG_PATH_IS_NONE', a.log_path() is None)\n"
        "a.log_invocation('image', 'agnes-image-2.5-flash', 'https://api.agnes-ai.cn/v1')\n"
        "print('AFTER_CALL_OK')\n", env)
    after = sha(hh / "config.yaml")
    check("D1 日志指到 config.yaml 时 log_path() 返回 None",
          "LOG_PATH_IS_NONE True" in out, out[:250])
    check("D2 追加日志后 config.yaml 字节完全未变",
          before is not None and before == after, out[:250])
    check("D3 给出明确告警且没崩",
          "AFTER_CALL_OK" in out and "已跳过写日志" in out, out[:250])

    code, out = run(["--history"], env)
    check("D4 --history 在该情况下优雅退化（退 1 + 提示），不报异常",
          code == 1 and "只读" in out, f"code={code} {out[:250]}")
finally:
    td.cleanup()

td, root, hh = fresh_home()
try:
    env = {"HERMES_HOME": str(hh), "HOME": str(root), "USERPROFILE": str(root),
           "AGNES_LOG_FILE": str(hh / "agnes.log")}
    code, out = run_py(
        "import agnes_common as a\n"
        "print('LOG_PATH_OK', a.log_path() is not None)\n"
        "a.log_invocation('image', 'agnes-image-2.5-flash', 'https://api.agnes-ai.cn/v1')\n", env)
    log_file = hh / "agnes.log"
    check("D5 日志放在 Hermes 目录内的非保留名（agnes.log）不误拦",
          log_file.is_file() and "agnes-image" in log_file.read_text(encoding="utf-8", errors="replace"),
          out[:250])
finally:
    td.cleanup()

print()
print("=" * 64)
print("E. 产物目录（不破坏文件，但别混进 Hermes 目录）")
print("=" * 64)

td, root, hh = fresh_home()
try:
    env = {"HERMES_HOME": str(hh), "HOME": str(root), "USERPROFILE": str(root),
           "AGNES_OUT_DIR": str(hh)}
    before = sha(hh / "config.yaml")
    code, out = run_py(
        "import agnes_common as a\n"
        "print('OUT', a.resolve_out_dir())\n", env)
    after = sha(hh / "config.yaml")
    check("E1 产物目录=Hermes 家目录时给出提醒（放行，不拦）",
          "OUT" in out and "Hermes 的家目录" in out, out[:250])
    check("E2 提醒归提醒，config.yaml 依然字节未变",
          before is not None and before == after, out[:250])

    env2 = {**env, "AGNES_OUT_DIR": str(hh / "agnes-output")}
    code, out = run_py(
        "import agnes_common as a\n"
        "print('OUT', a.resolve_out_dir())\n", env2)
    check("E3 子目录 agnes-output 不触发提醒（NAS 推荐写法）",
          "OUT" in out and "Hermes 的家目录" not in out, out[:250])
finally:
    td.cleanup()

print("-" * 64)
passed = sum(1 for _, ok in results if ok)
print(f"合计 {passed}/{len(results)} 项通过")
if passed != len(results):
    sys.exit(1)
