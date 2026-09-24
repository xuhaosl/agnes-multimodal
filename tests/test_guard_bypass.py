#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""守卫的「绕过路径」回归测试 —— README「守卫」那一条的可复现证据。

README 声称：守卫覆盖 `--resume` / `--poll-url` 等绕过路径。

test_domain_guard.py 证明的是 guard_target() **函数本身**判得对。
本文件证明的是那些**命令行口子真的接到了这个函数上** ——
参数解析漏一个、守卫顺序摆错一处，函数级测试照样全绿，
而 Key 已经能被推到别人的服务器上。

三个设计，少一个这套测试就没意义：

① 走真实命令行（子进程），不直接调函数。
   「--poll-url 会不会成为绕过口」这件事只在 main() 的参数流转里成立。

② 断言退出码 **恰好等于 3**，不是「不等于 0」。
   脚本因别的原因崩掉（参数名写错、缺 Key、prompt 为空）同样返回非 0，
   那种情况下守卫压根没生效 —— 只断言「非 0」就是假通过。
   3 是守卫专属退出码（agnes_common.GUARD_EXIT_CODE），2 才是一般失败。

③ 有两个负向对照（⑩⑪）。
   否则排除不掉「这脚本对任何输入都返回 3」这种可能。

不联网、不消耗额度：
   被拦下的用例根本到不了网络层；两个对照用例用 `--seconds 99`
   故意让它在守卫**之后**、请求**之前**的本地校验处失败。
   域名一律用 `.invalid` 保留顶级域（RFC 2606 保证不解析）。
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEO = ROOT / "scripts" / "agnes_video.py"

GUARD_EXIT = 3
GUARD_TAG = "[agnes-guard]"

REAL_BASE = "https://api.agnes-ai.cn/v1"
BAD_POLL = "http://evil.invalid/agnesapi"
BAD_BASE = "http://evil.invalid/v1"

_ISOLATED_HOME = tempfile.mkdtemp(prefix="agnes-guard-test-")

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("[PASS] " if cond else "[FAIL] ") + name + (f"\n         <- {detail}" if detail and not cond else ""))


def run(*args, env_extra=None):
    """在隔离环境里跑 agnes_video.py，返回 (退出码, stdout+stderr)。

    隔离掉宿主环境里所有 AGNES_* 变量，并把 ~ 指到临时目录 ——
    否则本机真实配置会掺进来，测试结果就不可复现了。
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGNES_")}
    env.update({
        "HOME": _ISOLATED_HOME,
        "USERPROFILE": _ISOLATED_HOME,   # Windows 上 ~ 走这个
        "AGNES_API_KEY": "sk-guard-test-not-a-real-key",
        "AGNES_BASE_URL": REAL_BASE,
        "PYTHONIOENCODING": "utf-8",
    })
    if env_extra:
        env.update(env_extra)
    p = subprocess.run([sys.executable, str(VIDEO), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=120)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def must_block(name, *args, env_extra=None):
    """期望被守卫拦下：退出码恰好 3，且带守卫标记。"""
    code, out = run(*args, env_extra=env_extra)
    check(name, code == GUARD_EXIT and GUARD_TAG in out,
          f"退出码={code}（期望 {GUARD_EXIT}），守卫标记={'有' if GUARD_TAG in out else '无'}，"
          f"输出={out.strip()[:160]!r}")


def must_not_block(name, *args, env_extra=None, expect_code=2):
    """期望守卫放行：落在**预期的那一个**退出码上，且不带守卫标记。

    只断言「不是 3」是不够的 —— 脚本因别的原因崩掉（返回 1）同样满足，
    而那说明它压根没走到我们想验的那一步。放行后应该停在**本地校验**失败处，
    即一般失败码 2（`fail()` 的默认值）。
    """
    code, out = run(*args, env_extra=env_extra)
    check(name, code == expect_code and GUARD_TAG not in out,
          f"退出码={code}（期望 {expect_code}），守卫标记={'有' if GUARD_TAG in out else '无'}，"
          f"输出={out.strip()[:160]!r}")


# ------------------------------------------------------------------ 三个口子
must_block("① --poll-url 给恶意域名 → 拦（--resume 也不豁免）",
           "--resume", "task_probe", "--poll-url", BAD_POLL)

must_block("② AGNES_POLL_URL 环境变量 → 拦",
           "--resume", "task_probe", env_extra={"AGNES_POLL_URL": BAD_POLL})

must_block("③ --base-url 恶意域名（轮询端点由它推导）→ 拦",
           "--resume", "task_probe", "--base-url", BAD_BASE)

must_block("④ --poll-url 优先于 --base-url（base 合法也拦得住）",
           "--resume", "task_probe", "--base-url", REAL_BASE, "--poll-url", BAD_POLL)

# ------------------------------------------------------------------ 伪装形态
must_block("⑤ userinfo 伪装 api.agnes-ai.cn@evil.invalid → 拦",
           "--resume", "task_probe", "--poll-url", "https://api.agnes-ai.cn@evil.invalid/agnesapi")

must_block("⑥ 子域蹭字样 agnes.evil.invalid → 拦",
           "--resume", "task_probe", "--poll-url", "https://agnes.evil.invalid/agnesapi")

must_block("⑦ 无点边界 notagnes-ai.cn → 拦",
           "--resume", "task_probe", "--poll-url", "https://notagnes-ai.cn/agnesapi")

# ------------------------------------------------------------------ 模型名
must_block("⑧ 非 agnes 模型（域名合法）→ 拦",
           "--resume", "task_probe", "--model", "gpt-4o")

# ------------------------------------------------------------------ 创建路径
must_block("⑨ 创建路径（不带 --resume）+ 恶意 base-url → 拦",
           "probe", "--seconds", "99", "--base-url", BAD_BASE)

# ------------------------------------------------------------------ 负向对照
must_not_block("⑩ 对照：ALLOW_ANY_HOST=1 时同一命令不再被判为守卫拦截",
               "probe", "--seconds", "99", "--base-url", BAD_BASE,
               env_extra={"AGNES_ALLOW_ANY_HOST": "1"})

must_not_block("⑪ 不误伤：合法域名不被守卫拦（后面本地校验失败退出）",
               "probe", "--seconds", "99")

# ------------------------------------------------------------------ 拦截时机
_code, _out = run("--resume", "task_probe", "--poll-url", BAD_POLL)
check("⑫ 守卫在请求发出前就拦下（没进入业务逻辑）",
      "[agnes-video]" not in _out,
      f"输出里出现了业务日志，说明守卫位置太靠后：{_out.strip()[:160]!r}")

# ------------------------------------------------------------------ 汇总
passed = sum(1 for _, ok in results if ok)
print(f"\n合计 {passed}/{len(results)} 项通过")
shutil.rmtree(_ISOLATED_HOME, ignore_errors=True)
sys.exit(0 if passed == len(results) else 1)
