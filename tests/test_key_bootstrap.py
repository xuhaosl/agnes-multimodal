#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证「密钥自举」链路：检查 / 写入 / 覆盖 / 环境变量优先级告警 / 缺失提示。

用受控 env + 临时 HOME 跑子进程，避免污染本机真实配置。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PY = sys.executable
SKILL = Path(__file__).resolve().parent.parent
COMMON = SKILL / "scripts" / "agnes_common.py"

results = []
skipped = []


def run(args, env=None, cwd=None):
    base = {k: v for k, v in os.environ.items() if k not in ("AGNES_API_KEY", "AGNES_AI_API_KEY", "AGNES_CONFIG_PATH")}
    if env:
        base.update(env)
    p = subprocess.run(
        [PY, str(COMMON)] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=base, cwd=cwd or str(SKILL), timeout=90,
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("[PASS] " if cond else "[FAIL] ") + name + (f"  <- {detail}" if detail and not cond else ""))


with tempfile.TemporaryDirectory() as td:
    home = Path(td) / "home"
    home.mkdir()
    cfg = Path(td) / "cfg" / "config.json"
    clean = {"HOME": str(home), "USERPROFILE": str(home)}

    # 1) 无密钥场景：应报 [agnes-need-key] 且退出码 1
    code, out = run(["--check-key"], env=clean)
    check("1 无密钥时 check-key 退出码=1", code == 1, f"code={code}")
    check("1 无密钥时输出 [agnes-need-key]", "[agnes-need-key]" in out, out[:200])

    # 2) 自检（不带参数）在无密钥时应给出 --set-key 引导
    code, out = run([], env=clean)
    check("2 无密钥自检退出码=1", code == 1, f"code={code}")
    check("2 无密钥自检提示 --set-key", "--set-key" in out, out[-300:])

    # 3) --set-key 写入指定位置
    fake = "sk-testFAKEkey0123456789ABCDEF"
    code, out = run(["--set-key", fake, "--config-path", str(cfg)], env=clean)
    check("3 set-key 退出码=0", code == 0, f"code={code}\n{out}")
    check("3 配置文件已生成", cfg.exists(), str(cfg))
    if cfg.exists():
        data = json.loads(cfg.read_text(encoding="utf-8"))
        check("3 api_key 写入正确", data.get("api_key") == fake, str(data)[:200])
        check("3 只写入最小字段", set(data.keys()) == {"api_key"}, str(list(data.keys())))

    # 4) 写入后 check-key 应通过（用 AGNES_CONFIG_PATH 指过去）
    code, out = run(["--check-key"], env={**clean, "AGNES_CONFIG_PATH": str(cfg)})
    check("4 写入后 check-key 退出码=0", code == 0, f"code={code}\n{out}")
    check("4 来源识别为配置文件", "配置文件" in out, out[:200])

    # 5) 覆盖写：换 Key，且不能破坏已有其它字段
    cfg.write_text(json.dumps({"api_key": fake, "out_dir": "/tmp/keep-me", "image": {"size": "4K"}}, ensure_ascii=False), encoding="utf-8")
    newkey = "sk-testNEWkey9876543210ZYXWVU"
    code, out = run(["--set-key", newkey, "--config-path", str(cfg)], env=clean)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    check("5 覆盖后 api_key 更新", data.get("api_key") == newkey, str(data)[:200])
    check("5 其它字段被保留 out_dir", data.get("out_dir") == "/tmp/keep-me", str(data)[:200])
    check("5 其它字段被保留 image", data.get("image") == {"size": "4K"}, str(data)[:200])

    # 6) 环境变量优先级告警
    code, out = run(["--set-key", "sk-envtest0000111122223333", "--config-path", str(cfg)],
                    env={**clean, "AGNES_API_KEY": "sk-from-environment-xxxx"})
    check("6 已存在环境变量时给出告警", "环境变量" in out and "不会生效" in out, out[:400])

    # 7) 环境变量存在时 check-key 来源应识别为环境变量而非配置文件
    code, out = run(["--check-key"], env={**clean, "AGNES_CONFIG_PATH": str(cfg), "AGNES_API_KEY": "sk-from-environment-xxxx"})
    check("7 优先级：环境变量高于配置文件", "环境变量 AGNES_API_KEY" in out, out[:200])

    # 8) 空 Key 必须拒绝
    code, out = run(["--set-key", "   ", "--config-path", str(Path(td) / "empty.json")], env=clean)
    check("8 空 Key 被拒绝（退出码!=0）", code != 0, f"code={code}")
    check("8 空 Key 未生成文件", not (Path(td) / "empty.json").exists(), "")

    # 9) 密钥不回显：输出里不应出现完整 Key
    code, out = run(["--set-key", "sk-SECRETcheck1234567890abcd", "--config-path", str(cfg)], env=clean)
    check("9 输出不回显完整 Key", "sk-SECRETcheck1234567890abcd" not in out, out[:300])
    check("9 输出含脱敏指纹", "..." in out, out[:300])

# 10) 本机真实环境 --check-key —— 仅在已配置密钥的环境有意义，未配置则跳过
code, out = run(["--check-key"])
if code == 0:
    check("10 本机真实环境 check-key=0", True, "")
else:
    skipped.append("10 本机真实环境 check-key（当前环境未配置密钥，跳过）")

print("-" * 60)
passed = sum(1 for _, ok, _ in results if ok)
line = f"合计 {passed}/{len(results)} 项通过"
if skipped:
    line += f"，跳过 {len(skipped)} 项"
print(line)
for s in skipped:
    print(f"[SKIP] {s}")
if passed != len(results):
    sys.exit(1)
