#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证「从 Hermes 现场配置复用同一把 Key」：形态覆盖 + 误用防护。

用临时目录当 HERMES_HOME，受控 env 跑子进程，绝不碰本机真实 Hermes 配置。
重点不是「能不能读到」，而是「**别家的 Key 绝不能被误用**」。
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PY = sys.executable
COMMON = Path(__file__).resolve().parent.parent / "scripts" / "agnes_common.py"

results = []


def run(env=None):
    base = {k: v for k, v in os.environ.items()
            if k not in ("AGNES_API_KEY", "AGNES_AI_API_KEY", "AGNES_CONFIG_PATH", "HERMES_HOME")}
    if env:
        base.update(env)
    p = subprocess.run([PY, str(COMMON), "--check-key"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=base, timeout=90)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("[PASS] " if cond else "[FAIL] ") + name + (f"  <- {detail}" if detail and not cond else ""))


def probe(name, cfg_yaml, dotenv, expect_key, expect_src=""):
    """在临时 HERMES_HOME 里放好 config.yaml / .env，看能否命中预期 Key。"""
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        if cfg_yaml is not None:
            (home / "config.yaml").write_text(cfg_yaml, encoding="utf-8")
        if dotenv is not None:
            (home / ".env").write_text(dotenv, encoding="utf-8")
        code, out = run({"HERMES_HOME": str(home),
                         "HOME": str(home), "USERPROFILE": str(home)})
        if expect_key is None:
            check(name + " → 不应命中", code == 1 and "[agnes-need-key]" in out, out[:250])
        else:
            ok = code == 0 and expect_key[:6] in out and expect_key[-4:] in out
            extra = (expect_src in out) if ok and expect_src else True
            check(name, ok and extra, out[:250])


# --- 形态甲：providers.<name>.api_key 明文 ---
probe("甲1 provider 名含 agnes，明文 api_key",
      "providers:\n"
      "  agnes:\n"
      '    name: "Agnes"\n'
      '    api: "https://api.agnes-ai.cn/v1"\n'
      "    api_key: sk-plainAGNESkey0001\n"
      "    models:\n"
      "      agnes-image-2.5-flash: {}\n",
      None, "sk-plainAGNESkey0001", "providers")

# --- 形态甲：key_env 指向 .env ---
probe("甲2 key_env 指向 .env 里的变量",
      "providers:\n"
      "  agnes:\n"
      '    api: "https://api.agnes-ai.cn/v1"\n'
      "    key_env: MY_AGNES_TOKEN\n",
      "MY_AGNES_TOKEN=sk-fromEnvFile0002\n", "sk-fromEnvFile0002")

# --- 形态甲：provider 名不含 agnes，靠 models 列表自证（不写 base_url 时） ---
probe("甲3 provider 名无关，models 里含 agnes-*（无 base_url）",
      "providers:\n"
      "  my-gateway:\n"
      "    api_key: sk-byModels0003\n"
      "    models:\n"
      "      agnes-video-2.5-flash: {}\n",
      None, "sk-byModels0003")

# --- 形态乙：顶层 model 单 provider ---
probe("乙1 model.base_url 含 agnes",
      "model:\n"
      "  provider: custom\n"
      "  default: agnes-2.5-flash\n"
      '  base_url: "https://api.agnes-ai.cn/v1"\n'
      "  api_key: sk-topModel0004\n",
      None, "sk-topModel0004", "model")

# --- 形态丙：只有 key_env，且变量名自带 agnes ---
probe("丙1 key_env 名含 agnes，值在 .env",
      "providers:\n"
      "  some-provider:\n"
      '    api: "https://api.agnes-ai.cn/v1"\n'
      "    key_env: AGNES_TOKEN_X\n",
      "AGNES_TOKEN_X=sk-envName0005\n", "sk-envName0005")

# --- 形态丁：.env 里就是 AGNES_API_KEY ---
probe("丁1 .env 里是 AGNES_API_KEY",
      "providers:\n  other:\n    api: \"https://x.example.com/v1\"\n",
      "AGNES_API_KEY=sk-dotenv0006\n", "sk-dotenv0006")

# --- 防护：别家 provider 的 Key 绝不能被误用 ---
probe("防护1 只有 DeepSeek provider，不应命中",
      "providers:\n"
      "  deepseek:\n"
      '    api: "https://api.deepseek.com/v1"\n'
      "    api_key: sk-deepseekSHOULDNOTUSE\n"
      "    models:\n"
      "      deepseek-chat: {}\n",
      "OPENAI_API_KEY=sk-openaiSHOULDNOTUSE\n", None)

probe("防护2 别家 provider + .env 只有 OPENAI_API_KEY，不应命中",
      "providers:\n"
      "  moonshot:\n"
      '    api: "https://api.moonshot.cn/v1"\n'
      "    key_env: OPENAI_API_KEY\n",
      "OPENAI_API_KEY=sk-moonshotSHOULDNOTUSE\n", None)

probe("防护3 完全没有 Hermes 配置，不应命中", None, None, None)

probe("防护4 agnes provider 存在但没有任何 key 来源，不应命中",
      "providers:\n"
      "  agnes:\n"
      '    api: "https://api.agnes-ai.cn/v1"\n',
      None, None)

probe("防护5 base_url 明确指向别家（Key 配串行），不应命中",
      "providers:\n"
      "  agnes:\n"
      '    api: "https://api.deepseek.com/v1"\n'
      "    api_key: sk-wrongHostSHOULDNOTUSE\n"
      "    models:\n"
      "      agnes-image-2.5-flash: {}\n",
      None, None)

probe("防护6 只有名字带 agnes，base_url 指向别家，不应命中",
      "providers:\n"
      "  my-agnes-proxy:\n"
      '    base_url: "https://openrouter.ai/api/v1"\n'
      "    api_key: sk-otherVendorSHOULDNOTUSE\n",
      None, None)

# --- 优先级：显式 config.json 应压过 Hermes 现场 ---
with tempfile.TemporaryDirectory() as td:
    home = Path(td)
    (home / "config.yaml").write_text(
        "providers:\n  agnes:\n    api: \"https://api.agnes-ai.cn/v1\"\n"
        "    api_key: sk-fromHermesZZZ\n", encoding="utf-8")
    own = home / "mine.json"
    own.write_text('{"api_key": "sk-fromMyConfigYYY"}', encoding="utf-8")
    code, out = run({"HERMES_HOME": str(home), "HOME": str(home), "USERPROFILE": str(home),
                     "AGNES_CONFIG_PATH": str(own)})
    # 只看来源：输出是脱敏指纹（sk-fro...gYYY），拿完整串比对必然误报
    check("优先级 显式配置文件压过 Hermes 现场",
          code == 0 and "来源=配置文件" in out and "sk-fro" in out and "gYYY" in out, out[:250])

print("-" * 60)
passed = sum(1 for _, ok in results if ok)
print(f"合计 {passed}/{len(results)} 项通过")
if passed != len(results):
    sys.exit(1)
