#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""域名判定与守卫验证：大小写、结尾点、子域混淆、userinfo 伪装、白名单。

README 说「域名判定用的是域后缀边界，不是字符串里含 agnes」，本文件就是这句话的
可复现证据。判定错一次的后果不是「调到别的服务」，而是**把 API Key 发到别人的
服务器上** —— 所以既要验「该拦的拦住」，也要验「该放的别误伤」。

三道守卫一次覆盖：
  guard_target()          请求发出前 —— 域名 + 模型名
  verify_response_model() 响应返回后 —— 服务端自报模型
  verify_artifact_host()  下载产物前 —— 产物域名

不需要网络，不发起任何请求，不消耗额度。
"""
import contextlib
import io
import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import agnes_common as a  # noqa: E402

MODEL = "agnes-image-2.5-flash"
GUARD_EXIT = a.GUARD_EXIT_CODE  # 3
FAIL_EXIT = 2

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("[PASS] " if cond else "[FAIL] ") + name + (f"\n         <- {detail}" if detail and not cond else ""))


@contextlib.contextmanager
def env(**kv):
    """临时改环境变量，退出时还原（None 表示删除）。"""
    old = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def guard(endpoint, model=MODEL):
    """跑 guard_target，返回 (是否放行, host, 退出码, stderr)。"""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        try:
            host = a.guard_target(endpoint, model)
            return True, host, 0, buf.getvalue()
        except SystemExit as e:
            return False, "", e.code, buf.getvalue()


def verify_model(model, response):
    """跑 verify_response_model，返回 (是否放行, 退出码)。"""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        try:
            a.verify_response_model(model, response, "图像")
            return True, 0
        except SystemExit as e:
            return False, e.code


def artifact(url):
    """跑 verify_artifact_host，返回 (host, stderr)。"""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        host = a.verify_artifact_host(url, "图像")
    return host, buf.getvalue()


print("=" * 64)
print("A. 域后缀边界（_is_agnes_host 纯函数）")
print("=" * 64)

for label, host, expect in [
    ("A1 子域 api.agnes-ai.cn 放行", "api.agnes-ai.cn", True),
    ("A2 裸域 agnes-ai.cn 放行", "agnes-ai.cn", True),
    ("A3 大小写 API.AGNES-AI.CN 放行", "API.AGNES-AI.CN", True),
    ("A4 结尾点 api.agnes-ai.cn. 放行", "api.agnes-ai.cn.", True),
    ("A5 子域混淆 agnes.evil.com 拦下", "agnes.evil.com", False),
    ("A6 无点边界 notagnes-ai.cn 拦下", "notagnes-ai.cn", False),
    ("A7 信任域出现在中间 agnes-ai.cn.evil.com 拦下", "agnes-ai.cn.evil.com", False),
    ("A8 空 host 拦下", "", False),
]:
    got = a._is_agnes_host(host)  # noqa: SLF001
    check(label, got is expect, f"host={host!r} got={got} expect={expect}")

print()
print("=" * 64)
print("B. guard_target —— 域名判定（请求发出前）")
print("=" * 64)

ok, host, code, err = guard("https://api.agnes-ai.cn/v1")
check("B1 官方域名放行且返回 host", ok and host == "api.agnes-ai.cn", f"host={host!r} err={err[:150]}")

for label, url in [
    ("B2 子域混淆 agnes.evil.com 拦下", "https://agnes.evil.com/v1"),
    ("B3 userinfo 伪装 api.agnes-ai.cn@evil.com 拦下", "https://api.agnes-ai.cn@evil.com/v1"),
    ("B4 无点边界 notagnes-ai.cn 拦下", "https://notagnes-ai.cn/v1"),
    ("B5 完全无关域名 evil.com 拦下", "https://evil.com/v1"),
]:
    ok, host, code, err = guard(url)
    check(label, (not ok) and code == GUARD_EXIT and "[agnes-guard]" in err,
          f"code={code}（期望 {GUARD_EXIT}） err={err[:200]}")

print()
print("=" * 64)
print("C. guard_target —— 模型名前缀")
print("=" * 64)

ok, host, code, err = guard("https://api.agnes-ai.cn/v1", "agnes-video-2.5-flash")
check("C1 agnes- 前缀模型放行（域名也对）", ok, err[:150])

for label, model in [
    ("C2 别家模型 dall-e-3 拦下（域名对也不行）", "dall-e-3"),
    ("C3 别家模型 GPT-Image-1 拦下（大小写无关）", "GPT-Image-1"),
    ("C4 空模型名拦下", ""),
]:
    ok, host, code, err = guard("https://api.agnes-ai.cn/v1", model)
    check(label, (not ok) and code == GUARD_EXIT, f"code={code} err={err[:150]}")

print()
print("=" * 64)
print("D. 白名单与放行开关（自建代理场景，别一刀切死）")
print("=" * 64)

with env(AGNES_TRUSTED_HOSTS="proxy.example.com"):
    ok1, h1, c1, e1 = guard("https://proxy.example.com/v1")
    ok2, h2, c2, e2 = guard("https://api.proxy.example.com/v1")
    ok3, h3, c3, e3 = guard("https://other.example.com/v1")
    check("D1 白名单域名本身放行", ok1, f"code={c1} {e1[:150]}")
    check("D2 白名单域名的子域放行", ok2, f"code={c2} {e2[:150]}")
    check("D3 白名单之外仍拦下", (not ok3) and c3 == GUARD_EXIT, f"code={c3}")

with env(AGNES_TRUSTED_HOSTS=".Proxy.Example.com."):
    ok, host, code, err = guard("https://proxy.example.com/v1")
    check("D4 白名单项的大小写与前后点被归一化", ok, f"code={code} {err[:150]}")

with env(AGNES_ALLOW_ANY_HOST="1"):
    ok, host, code, err = guard("https://evil.com/v1")
    check("D5 AGNES_ALLOW_ANY_HOST=1 整体放行", ok and host == "evil.com", f"code={code} {err[:150]}")
    check("D5 放行时仍打印警告（放行不等于静默）", "警告" in err, err[:200])

print()
print("=" * 64)
print("E. 第二道守卫：响应里的模型名")
print("=" * 64)

ok, code = verify_model(MODEL, {})
check("E1 响应没有 model 字段时放行（图像接口常见）", ok, f"code={code}")

ok, code = verify_model(MODEL, {"model": "agnes-image-2.5-flash"})
check("E2 响应模型含 agnes 放行", ok, f"code={code}")

ok, code = verify_model(MODEL, {"model": "dall-e-3"})
check("E3 响应模型不是 Agnes 时中止（退出码 2，非守卫码 3）",
      (not ok) and code == FAIL_EXIT, f"code={code}（期望 {FAIL_EXIT}）")

print()
print("=" * 64)
print("F. 第三道守卫：产物域名")
print("=" * 64)

host, err = artifact("https://cos-platform-outputs.agnes-ai.cn/a.png")
check("F1 产物域名含 agnes 不警告", host == "cos-platform-outputs.agnes-ai.cn" and "警告" not in err, err[:150])

host, err = artifact("https://storage.googleapis.com/agnes-aigc/a.png")
check("F2 host 不含 agnes 但 path 含 —— 不误报", "警告" not in err, err[:150])

host, err = artifact("https://example.com/a.png")
check("F3 都看不到 agnes 时警告但不中止", "警告" in err, err[:150])

print("-" * 64)
passed = sum(1 for _, ok in results if ok)
print(f"合计 {passed}/{len(results)} 项通过")
if passed != len(results):
    sys.exit(1)
