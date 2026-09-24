#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agnes AI 多模态公共模块。

设计目标：同一份脚本能在 Windows(WorkBuddy) / macOS / Linux / WSL2 / NAS /
Hermes Agent（local / Docker / Modal 后端）上直接跑，不装任何第三方包。

因此这里只用 Python 标准库（urllib），刻意不用 requests / openai SDK。

密钥解析顺序（从高到低）：
  1. 命令行 --api-key
  2. 环境变量 AGNES_API_KEY      <- Hermes 的 required_environment_variables 会注入这个
  3. 环境变量 AGNES_AI_API_KEY
  4. 配置文件 ~/.agnes/config.json 或 ~/.config/agnes/config.json 的 api_key
  5. WorkBuddy 模型配置 ~/.workbuddy/models.json 中 id 含 "agnes" 的条目 apiKey
     （所以在本机 WorkBuddy 里是零配置的）

Base URL 解析顺序：--base-url > AGNES_BASE_URL > config.json 的 base_url > 官方中国站默认值
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "https://api.agnes-ai.cn/v1"
DEFAULT_OUT_DIR = "~/agnes-output"

IMAGE_MODEL_DEFAULT = "agnes-image-2.5-flash"
VIDEO_MODEL_DEFAULT = "agnes-video-2.5-flash"

# 调用凭证日志：每次真实调用追加一行，供事后审计「到底打给了谁、用了哪个模型」。
LOG_PATH = "~/.agnes/invocations.log"
LOG_HEADER = "time\tkind\tmodel\tendpoint\tstatus\tartifact\tnote"

# 密钥写入位置的环境变量覆盖；同时参与「读」的候选顺序，保证写入点一定可读回。
CONFIG_PATH_ENV = "AGNES_CONFIG_PATH"
KEY_ENV_NAMES = ("AGNES_API_KEY", "AGNES_AI_API_KEY")
# Hermes Docker 部署下的挂载卷内配置路径（容器内 ~ 不是持久化卷）
DOCKER_CONFIG_PATH = "/opt/data/agnes/config.json"

# 让中文输出在 Windows 控制台（cp936）下也不炸
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# --------------------------------------------------------------------------
# 输出与错误
# --------------------------------------------------------------------------

def eprint(*args) -> None:
    print(*args, file=sys.stderr, flush=True)


def fail(message: str, code: int = 2) -> None:
    """打印错误到 stderr 并退出。错误信息带可检索前缀，方便 agent 判断。"""
    eprint(f"[agnes-error] {message}")
    raise SystemExit(code)


GUARD_EXIT_CODE = 3


def guard_fail(message: str) -> None:
    """守卫在「请求发出前」拦下调用时专用。

    单独一个退出码（3）是为了把「被守卫拦住」和「请求真的失败」区分开 ——
    fail() 默认也是 2，两者混在一起时，自测脚本无法证明守卫真的生效了。
    """
    eprint(f"[agnes-guard] {message}")
    raise SystemExit(GUARD_EXIT_CODE)


def emit_result(path_or_url: str, label: str = "") -> None:
    """把最终产物作为「裸绝对路径」打到 stdout。

    这一行是跨平台集成点：
      - WorkBuddy 直接读这一行拿文件；
      - Hermes 的 gateway 会从回复里抽取媒体路径，**默认渲染成内联图**
        （会被有损压缩）。高分辨率图 / 视频要在回复末尾加 `[[as_document]]`，
        gateway 才会改成可下载的文件附件。
    所以：路径必须单独占一行、必须是绝对路径、不要再包引号或 markdown 链接。
    """
    p = Path(path_or_url).expanduser()
    print(str(p.resolve()) if p.exists() else str(p), flush=True)


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

def read_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _config_candidates():
    """读取候选顺序。写入点必须全部在列表内，否则会出现「写进去了却读不到」。"""
    out = []
    env_path = os.environ.get(CONFIG_PATH_ENV, "").strip()
    if env_path:
        out.append(Path(env_path).expanduser())
    out.extend([
        Path.home() / ".agnes" / "config.json",
        Path.home() / ".config" / "agnes" / "config.json",
        Path(__file__).resolve().parent.parent / "config.json",
        Path(DOCKER_CONFIG_PATH),
    ])
    return out


def load_config() -> dict:
    for path in _config_candidates():
        data = read_json(path)
        if isinstance(data, dict):
            return data
    return {}


def _key_from_workbuddy_models() -> str:
    """从 WorkBuddy 的模型配置里捞 agnes 的 apiKey。

    只在「用户已经在本机接入了 agnes 文本模型」时命中，
    这样在 WorkBuddy 上是零配置；在 Hermes / NAS 上这条自然落空。
    """
    for name in ("models.json",):
        data = read_json(Path.home() / ".workbuddy" / name)
        if not isinstance(data, list):
            continue
        for entry in data:
            if not isinstance(entry, dict):
                continue
            ident = str(entry.get("id", "")) + str(entry.get("name", ""))
            if "agnes" in ident.lower():
                key = entry.get("apiKey") or entry.get("api_key")
                if key:
                    return str(key).strip()
    return ""


# --------------------------------------------------------------------------
# 复用 Hermes 已经配好的 Agnes 凭据
#
# Hermes 把模型 provider 配置放在 ~/.hermes/config.yaml（Docker 部署下容器里的
# ~/.hermes 就等于挂载卷 /opt/data）。密钥两种放法：直接写在 provider 的 api_key，
# 或只写变量名（key_env）由 ~/.hermes/.env 提供。
#
# 既然用户已经给 Hermes 配好了 Agnes，多模态就该复用同一把 Key ——
# 让他为 skill 再配第二遍是没有道理的。
#
# 只做缩进感知的行级解析，不引第三方 YAML 库（本 skill 只用标准库），
# 因此刻意只支持「块式映射 + 标量」这一种写法，遇到 flow style 就放弃该段。
# --------------------------------------------------------------------------

HERMES_HOME_ENV = "HERMES_HOME"
ENV_KEY_NAME_HINT = ("AGNES_API_KEY", "AGNES_AI_API_KEY")
# 归属证据：不写死 section 名（Hermes 升级可能改叫 providers / custom_providers / 别的），
# 改看内容像不像 Agnes，这样名字变了也还能命中。
AGNES_HOST_HINT = "agnes-ai.cn"
AGNES_MODEL_HINT = re.compile(r"agnes-(?:image|video|\d)", re.I)


def _hermes_homes() -> list:
    out = []
    raw = os.environ.get(HERMES_HOME_ENV, "").strip()
    if raw:
        out.append(Path(raw).expanduser())
    out.append(Path.home() / ".hermes")
    if os.name != "nt":
        out.append(Path("/opt/data"))
    seen, uniq = set(), []
    for item in out:
        token = str(item)
        if token not in seen:
            seen.add(token)
            uniq.append(item)
    return uniq


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _strip_scalar(raw: str) -> str:
    """去掉 YAML 标量的引号与行尾注释。"""
    value = raw.strip()
    if not value:
        return ""
    if value[0] in "\"'":
        quote = value[0]
        end = value.find(quote, 1)
        return value[1:end] if end > 0 else value[1:]
    # 只有「空白 + #」才算注释，避免把 sk-abc#def 这种值截断
    m = re.match(r"^(.*?)\s+#", value)
    if m:
        value = m.group(1)
    return value.strip().rstrip(",")


def _eff_indent(line: str) -> int:
    """有效缩进：YAML 序列项 `- ` 的内容等价于多缩进 2，这里折算进去。

    没有这层折算，`- name: Agnes`（`-` 与父键同缩进）会被当成同级行，
    整个序列就切不出来了。
    """
    ind = _indent_of(line)
    return ind + 2 if re.match(r"^[ \t]*-[ \t]+", line) else ind


def _yaml_section(text: str, name: str) -> list:
    """取顶层 `name:` 之下、缩进更深的所有行（含序列项）。flow style 直接放弃。"""
    out, inside, base = [], False, 0
    for line in text.splitlines():
        if inside:
            if not line.strip():
                continue
            if _eff_indent(line) <= base:
                break
            out.append(line)
            continue
        m = re.match(rf"^(\s*){re.escape(name)}\s*:\s*(.*)$", line)
        if m and not m.group(1):
            if m.group(2).strip():
                return []
            inside, base = True, len(m.group(1))
    return out


def _top_sections(text: str) -> list:
    """列出所有顶层块式 section → [(名字, 行列表)]。"""
    names = []
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*:\s*$", line)
        if m:
            names.append(m.group(1))
    return [(n, _yaml_section(text, n)) for n in names]


def _split_entries(lines: list) -> list:
    """把一段行切成若干条目原文，映射（`k:`）与序列（`- k:`）两种写法都吃。

    关键细节：YAML 序列里 `- name: x` 的 `- ` 占两格，后面的 `  base_url: y`
    缩进也是两格 —— 二者**有效缩进相同**。所以必须先判定模式：
    序列模式只认 `- ` 开头的行开新条目，否则每个字段都会被切成独立条目，
    「有没有 base_url」「有没有 key_env」就散落在不同块里，判定必然错位。
    """
    body = [ln for ln in lines if ln.strip()]
    if not body:
        return []
    base = min(_eff_indent(ln) for ln in body)
    is_seq = any(_eff_indent(ln) == base and re.match(r"^[ \t]*-[ \t]+", ln) for ln in body)

    blocks, buf = [], None
    for line in body:
        is_item = bool(re.match(r"^[ \t]*-[ \t]+", line))
        start = False
        if _eff_indent(line) == base:
            if is_seq:
                start = is_item
            else:
                start = bool(re.match(r"^[ \t]*[A-Za-z0-9_.\-]+\s*:", line))
        if not start:
            if buf is not None:
                buf.append(line)
            continue
        if buf is not None:
            blocks.append("\n".join(buf))
        if is_item:
            head = re.sub(r"^[ \t]*-[ \t]+", "", line)
            buf = [" " * (_indent_of(line) + 2) + head]
        else:
            buf = [line]
    if buf is not None:
        blocks.append("\n".join(buf))
    return blocks


def _agnes_evidence(block: str, section: str) -> list:
    """返回该配置块属于 Agnes 的证据；空列表 = 认不出 → 不取它的 Key。

    判据分两档，因为 base_url 是决定性的：

    - **写了 base_url**：必须指向 `agnes-ai.cn`，明确指向别家的一律不取。
      （用户把 Key 配串行的场景很常见，这时候取出来就是拿别家的凭据去打 Agnes。）
    - **没写 base_url**：才允许靠模型列表 / 命名 / 密钥变量名自证。

    注意 provider 的 `name` 是用户自己起的，只算弱证据，从不单独成立。
    """
    label = _yaml_scalar(block, "name")
    host = (_yaml_scalar(block, "base_url") or _yaml_scalar(block, "api")
            or _yaml_scalar(block, "url"))
    var = _yaml_scalar(block, "key_env") or _yaml_scalar(block, "api_key_env")

    if host:
        if AGNES_HOST_HINT not in host.lower():
            return []
        hits = ["base_url 指向 agnes-ai.cn"]
        if AGNES_MODEL_HINT.search(block):
            hits.append("模型列表含 agnes-* 条目")
        return hits

    hits = []
    if AGNES_MODEL_HINT.search(block):
        hits.append("模型列表含 agnes-* 条目")
    if "agnes" in (var + label + section).lower():
        hits.append("provider 名或密钥变量名含 agnes")
    return hits


def _yaml_scalar(block: str, field: str) -> str:
    m = re.search(rf"^[ \t]*{re.escape(field)}[ \t]*:[ \t]*(.+)$", block, re.M)
    return _strip_scalar(m.group(1)) if m else ""


def _read_env_file(path: Path) -> dict:
    data = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return data
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if key:
            data[key] = _strip_scalar(value)
    return data


def _hermes_key_lookup() -> tuple:
    """返回 (key, 出处说明)；找不到返回 ("", "")。

    判定「这个条目是不是 Agnes」靠内容自证：base_url / 模型列表 / 密钥变量名。
    刻意**不**依赖 section 名，也**不**把 provider 的 name 当必要条件 ——
    实测 Hermes 用的键是 `custom_providers`（序列），且 name 是用户自己起的，
    升级后键名与命名都可能变；只认结构变化不改「内容特征」这件事。

    只读本地文件、只用于调 Agnes，不会外传；宁可不命中，也不猜着用 ——
    拿别家的 Key 去调 Agnes 会 401，还平白把无关密钥送出网。
    """
    for home in _hermes_homes():
        env_file = _read_env_file(home / ".env")
        cfg = home / "config.yaml"
        text = ""
        if cfg.is_file():
            try:
                text = cfg.read_text(encoding="utf-8", errors="replace")
            except Exception:
                text = ""

        def pick(block: str) -> str:
            direct = _yaml_scalar(block, "api_key")
            if direct:
                return direct
            var = _yaml_scalar(block, "key_env") or _yaml_scalar(block, "api_key_env")
            if var:
                return env_file.get(var, "") or os.environ.get(var, "").strip()
            return ""

        if text:
            # 形态甲：顶层任意 section（providers / custom_providers / 升级后改的名）
            # 下的 provider 条目，映射与序列两种写法都吃。
            for section, lines in _top_sections(text):
                for block in _split_entries(lines):
                    hits = _agnes_evidence(block, section)
                    if not hits:
                        continue
                    key = pick(block)
                    if key:
                        label = _yaml_scalar(block, "name")
                        where = f"{section}" + (f"「{label}」" if label else "")
                        return key, (f"Hermes 配置 {_short_path(cfg)} 的 {where}"
                                     f"（依据：{'、'.join(hits)}）")

            # 形态乙：顶层 model: 就是唯一 provider
            model_block = "\n".join(_yaml_section(text, "model"))
            if model_block:
                base = _yaml_scalar(model_block, "base_url") or _yaml_scalar(model_block, "api")
                default_model = _yaml_scalar(model_block, "default")
                if "agnes" in (base + default_model).lower():
                    key = pick(model_block)
                    if key:
                        return key, f"Hermes 配置 {_short_path(cfg)} 的 model（base_url={base or '未写'}）"

        # 形态丁：变量名自证 —— 这本来就是给 Agnes 用的，无需再证明归属
        for var in ENV_KEY_NAME_HINT:
            if env_file.get(var):
                return env_file[var], f"Hermes 环境文件 {_short_path(home / '.env')} 的 {var}"
    return "", ""


def resolve_api_key(cli_value: str | None = None) -> str:
    if cli_value and cli_value.strip():
        return cli_value.strip()
    for env_name in ("AGNES_API_KEY", "AGNES_AI_API_KEY"):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    cfg = load_config()
    for field in ("api_key", "apiKey", "agnes_api_key"):
        value = str(cfg.get(field, "")).strip()
        if value:
            return value
    value = _hermes_key_lookup()[0]
    if value:
        return value
    value = _key_from_workbuddy_models()
    if value:
        return value
    fail(
        "[agnes-need-key] 未检测到 Agnes API Key。\n"
        "交给 agent 处理，不要让用户手动去编辑文件：\n"
        "  1) 问用户要 Key（在 https://www.agnes-ai.cn 控制台获取，形如 sk-xxxx）\n"
        f"  2) 用户给出后执行： python {Path(__file__).name} --set-key <用户的KEY>\n"
        "  3) 然后重跑原任务（脚本会自动读取刚写入的配置）\n"
        "  Key 只写进本地配置文件，不会被上传到任何地方。"
    )
    raise AssertionError("unreachable")


# --------------------------------------------------------------------------
# 密钥自动配置（由 agent 调用，用户不需要手动碰文件）
# --------------------------------------------------------------------------

def env_key_hits() -> list:
    """返回当前已设置密钥的环境变量名，供「写了配置也不生效」的场景告警。"""
    return [name for name in KEY_ENV_NAMES if os.environ.get(name, "").strip()]


# --------------------------------------------------------------------------
# 只读铁律：Hermes 自己的配置文件，本 skill **只能读**。
# 任何写入 / 修改 / 删除都要在「动手之前」拦下 —— 连临时文件都不许在那边出现
# （原子写入会在同目录建 .tmp，所以只靠「别覆盖」是不够的）。
# --------------------------------------------------------------------------

READONLY_EXIT_CODE = 4          # 与守卫（3）、一般失败（2）分开，便于自测区分

HERMES_RESERVED_NAMES = {
    "config.yaml", "config.yml", "config.toml",
    "settings.yaml", "settings.yml",
    ".env", ".env.local", ".env.production", ".env.development",
}


def is_hermes_protected_file(path: Path) -> bool:
    """这个路径是 Hermes 自己的文件吗？是的话本 skill 只能读。

    判定 = 文件名是 Hermes 的保留名 **且** 落在某个 Hermes home 之内。
    （别处同名文件不拦 —— 那只是名字撞了，不是 Hermes 的。）
    """
    if path.name.lower() not in HERMES_RESERVED_NAMES:
        return False
    try:
        target = path.expanduser().resolve(strict=False)
    except Exception:
        return False
    for home in _hermes_homes():
        try:
            root = home.expanduser().resolve(strict=False)
        except Exception:
            continue
        if target == root or root in target.parents:
            return True
    return False


def assert_write_allowed(path: Path) -> Path:
    """写入前的闸门：目标若是 Hermes 的文件，直接拒绝并给出替代方案。

    Hermes 的配置写坏了会导致它起不来，而且这类损坏往往在重启后才暴露。
    所以宁可不写 —— 换一个路径一样能配好 Key。
    """
    if is_hermes_protected_file(path):
        fail(
            f"拒绝写入 {_short_path(path.expanduser())}\n"
            "  这是 Hermes 自己的配置文件，本 skill 对它只有只读权限，不能写入。\n"
            "  要改 Hermes 的模型 / 密钥配置，请用 `hermes setup` 或手动编辑 ——\n"
            "  写坏了 Hermes 会起不来，而且往往重启后才暴露。\n"
            "  如果只是想给本 skill 单独配一把 Key，换个路径就行，例如：\n"
            "    python agnes_common.py --set-key sk-xxxx --config-path ~/.agnes/config.json",
            READONLY_EXIT_CODE,
        )
    return path


def writable_config_path(explicit: str | None = None) -> Path:
    """决定密钥写进哪个配置文件。

    顺序：
      1. 显式传入的 --config-path
      2. 环境变量 AGNES_CONFIG_PATH
      3. 已存在的候选配置文件 —— 写回原处，避免同一台机器出现两份配置互相打架
      4. 默认位置：Hermes Docker 容器里写挂载卷 /opt/data/agnes/config.json
         （容器内 ~ 不是持久化卷，写到那儿一重建就丢）；其余情况写 ~/.agnes/config.json

    注意：返回的路径必须也在 _config_candidates() 里，否则会「写进去了却读不回来」。
    另外，无论走哪条分支，目标都不能是 Hermes 自己的文件（见 assert_write_allowed）。
    """
    raw = (explicit or "").strip() or os.environ.get(CONFIG_PATH_ENV, "").strip()
    if raw:
        return assert_write_allowed(Path(raw).expanduser())
    for path in _config_candidates():
        if path.exists():
            return assert_write_allowed(path)
    if os.name != "nt" and os.path.isdir("/opt/data") and os.access("/opt/data", os.W_OK):
        return assert_write_allowed(Path(DOCKER_CONFIG_PATH))
    return assert_write_allowed(Path.home() / ".agnes" / "config.json")


def save_api_key(key: str, config_path: str | None = None) -> Path:
    """把 Key 写进配置文件，保留文件里已有的其它字段（out_dir、image 等）。

    先写临时文件再原子替换，避免中途失败留下半截 JSON 把已有配置毁掉。
    """
    key = (key or "").strip()
    if not key:
        fail("要写入的 Key 是空的，没有写入任何内容。")
    path = assert_write_allowed(writable_config_path(config_path))
    data = read_json(path)
    if not isinstance(data, dict):
        data = {}
    data["api_key"] = key
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)  # Windows 上无实际作用，Linux/NAS 上收紧权限
    except Exception:
        pass
    return path


def cli_set_key(argv: list) -> int:
    """写入密钥：python agnes_common.py --set-key <KEY> [--config-path P]"""
    key = ""
    config_path = None
    rest = list(argv[1:])
    for idx, token in enumerate(rest):
        if token == "--set-key" and idx + 1 < len(rest):
            key = rest[idx + 1]
        elif token == "--config-path" and idx + 1 < len(rest):
            config_path = rest[idx + 1]
        elif not token.startswith("-") and not key:
            key = token
    if not key:
        fail("--set-key 后面要跟 Key，例如：--set-key sk-xxxx")
    path = save_api_key(key, config_path)
    print(f"[agnes-key] 已写入 -> {_short_path(path)}")
    print(f"[agnes-key] 密钥指纹（脱敏）：{_mask(key)}")
    hits = env_key_hits()
    if hits:
        print(f"[agnes-key] ⚠ 环境变量 {' / '.join(hits)} 已存在，且它的优先级高于配置文件，")
        print("            新写入的 Key 不会生效。要真正换 Key，请改该环境变量后重启会话。")
    print("[agnes-key] 下一步：重跑原本的图像 / 视频任务即可。")
    return 0


def cli_check_key() -> int:
    """只检查有没有密钥，不改动任何东西：python agnes_common.py --check-key

    退出码 0 = 已有密钥，1 = 没有（agent 据此决定是否向用户索要）。
    """
    source = _identify_key_source()
    if source:
        print(f"[agnes-key] OK · 来源={source} · 指纹={_mask(resolve_api_key())}")
        return 0
    print("[agnes-need-key] 未检测到 Agnes API Key。")
    print("请向用户索要 Key，然后执行：")
    print(f"  python {Path(__file__).name} --set-key <用户的KEY>")
    return 1


def resolve_base_url(cli_value: str | None = None) -> str:
    raw = ""
    if cli_value and cli_value.strip():
        raw = cli_value.strip()
    elif os.environ.get("AGNES_BASE_URL", "").strip():
        raw = os.environ["AGNES_BASE_URL"].strip()
    else:
        raw = str(load_config().get("base_url", "")).strip() or DEFAULT_BASE_URL
    return normalize_base_url(raw)


def normalize_base_url(raw: str) -> str:
    url = raw.rstrip("/")
    if not re.search(r"/v\d+$", url):
        url = url + "/v1"
    return url


def derive_poll_url(base_url: str) -> str:
    """由 base URL 推导视频任务查询端点。

    https://api.agnes-ai.cn/v1  ->  https://api.agnes-ai.cn/agnesapi
    """
    parsed = urllib.parse.urlparse(base_url)
    path = re.sub(r"/v\d+$", "", parsed.path.rstrip("/"))
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, (path or "") + "/agnesapi", "", "", "")
    )


_out_dir_warned = False


def warn_if_out_dir_in_hermes(path: Path) -> None:
    """产物目录正好是 Hermes 的家目录时提醒一句。

    这里**不拦** —— 产物图片不会覆盖 Hermes 的任何文件，谈不上破坏；
    但混在一起容易让人以为 Hermes 目录被人动过，提醒一句更省事。
    （推荐写法是放在它的子目录里，例如 `/opt/data/agnes-output`，那边不触发。）
    """
    global _out_dir_warned
    if _out_dir_warned:
        return
    try:
        target = path.expanduser().resolve()
    except Exception:
        return
    for home in _hermes_homes():
        try:
            if target == home.expanduser().resolve():
                _out_dir_warned = True
                eprint(f"[agnes-warn] 产物目录就是 Hermes 的家目录：{_short_path(target)}")
                eprint("             产物不会覆盖 Hermes 的文件，但建议换到子目录，例如 "
                       f"{_short_path(target)}/agnes-output")
                return
        except Exception:
            continue


def resolve_out_dir(cli_value: str | None = None) -> Path:
    """解析产物目录。顺序与密钥一致：命令行 > 环境变量 > 配置文件 > 默认值。"""
    raw = (cli_value or "").strip()
    if not raw:
        raw = os.environ.get("AGNES_OUT_DIR", "").strip()
    if not raw:
        raw = str(load_config().get("out_dir", "")).strip()
    if not raw:
        raw = DEFAULT_OUT_DIR
    path = Path(raw).expanduser()
    warn_if_out_dir_in_hermes(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

RETRY_STATUS = {408, 429, 500, 502, 503, 504}


class SoftHTTPError(Exception):
    """把指定的 HTTP 状态码交回调用方自行处理，而不是直接退出进程。

    典型用途：轮询时先带 model_name 查询，若 404 再回退到纯 video_id 查询。
    """

    def __init__(self, code: int, detail: str):
        super().__init__(f"HTTP {code}: {detail}")
        self.code = code
        self.detail = detail


def _ssl_context():
    if os.environ.get("AGNES_INSECURE", "").strip() in ("1", "true", "True"):
        return ssl._create_unverified_context()  # noqa: SLF001  企业代理/自签证书场景
    return ssl.create_default_context()


def _extract_detail(body_text: str) -> str:
    try:
        parsed = json.loads(body_text)
    except Exception:
        return body_text.strip()[:400]
    if isinstance(parsed, dict):
        for field in ("detail", "message", "error"):
            value = parsed.get(field)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict) and value.get("message"):
                return str(value["message"])
    return json.dumps(parsed, ensure_ascii=False)[:400]


def http_json(
    method: str,
    url: str,
    api_key: str,
    payload: dict | None = None,
    timeout: int = 300,
    retries: int = 3,
    backoff: float = 2.0,
    soft_statuses: tuple = (),
):
    """发 JSON 请求并返回解析后的对象。

    429 / 5xx / 网络超时按指数退避重试；4xx 参数类错误立刻失败并带出 detail。
    soft_statuses 里列出的状态码不退出，改为抛出 SoftHTTPError 交给调用方。
    """
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "agnes-multimodal-skill/1.0",
    }

    last_error = ""
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            if not body.strip():
                return {}
            return json.loads(body)
        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            detail = _extract_detail(body_text) if body_text else exc.reason
            last_error = f"HTTP {exc.code}: {detail}"
            if exc.code in soft_statuses:
                raise SoftHTTPError(exc.code, str(detail))
            if exc.code in (400, 401, 403, 404, 422):
                fail(
                    f"{last_error}\n"
                    f"  (400=参数/模式不匹配, 401/403=Key 无效或无权限, 404=ID 不存在)"
                )
            if exc.code not in RETRY_STATUS or attempt == retries:
                break
        except SoftHTTPError:
            # 必须排在 except Exception 之前：SoftHTTPError 是 Exception 子类，
            # 少了这条就会被下面的兜底分支当成网络错误吞掉并重试。
            raise
        except Exception as exc:  # URLError / timeout / SSLError
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt == retries:
                break
        sleep_for = backoff * (2 ** (attempt - 1))
        eprint(f"[agnes] 第 {attempt} 次请求失败（{last_error}），{sleep_for:.1f}s 后重试…")
        time.sleep(sleep_for)

    fail(f"请求失败：{last_error}\n  URL: {url}")


def download(url: str, out_dir: Path, filename: str, timeout: int = 600, retries: int = 3) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / filename
    tmp = target.with_suffix(target.suffix + ".part")

    last_error = ""
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(
            url, headers={"User-Agent": "agnes-multimodal-skill/1.0"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as resp:
                with open(tmp, "wb") as fh:
                    while True:
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        fh.write(chunk)
            tmp.replace(target)
            return target
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
            if attempt < retries:
                time.sleep(2.0 * attempt)

    fail(f"下载产物失败：{last_error}\n  URL: {url}")


# --------------------------------------------------------------------------
# 媒体与文件名
# --------------------------------------------------------------------------

def is_remote(value: str) -> bool:
    return value.strip().lower().startswith(("http://", "https://", "data:"))


def to_input_media(value: str, max_mb: float = 20.0) -> str:
    """把本地文件路径转成 Data URI；http(s):// 与 data: 原样返回。"""
    value = value.strip()
    if is_remote(value):
        return value

    path = Path(value).expanduser()
    if not path.is_file():
        fail(f"输入媒体不存在：{value}")
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > max_mb:
        eprint(
            f"[agnes] 提醒：{path.name} 有 {size_mb:.1f} MB，"
            f"转 Data URI 后请求体会更大，必要时先压缩。"
        )
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def save_base64(b64_text: str, out_dir: Path, filename: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / filename
    target.write_bytes(base64.b64decode(b64_text))
    return target


def guess_ext(url: str, default: str = ".png") -> str:
    try:
        suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    except Exception:
        suffix = ""
    if suffix and len(suffix) <= 5:
        return suffix
    return default


def timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def slugify(text: str, max_len: int = 24) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "-", (text or "").strip())
    cleaned = cleaned.strip("-")
    return cleaned[:max_len].strip("-") or "output"


def build_filename(stem: str, prompt: str, ext: str) -> str:
    base = slugify(stem) if stem else f"{timestamp()}-{slugify(prompt)}"
    return f"{base}{ext}"


# --------------------------------------------------------------------------
# 调用审计与来源守卫
#
# 本机 WorkBuddy 除 Agnes 外还有别的生图/生视频能力（那些会消耗积分）。
# 为了让「这次到底调的是不是 Agnes」可被事后核对，而不是一句口头保证，
# 这里做三件事：
#   1. guard_target()           —— 请求发出前，挡住非 Agnes 的 host / 模型名
#   2. verify_response_model()  —— 响应回来后，校验服务端自报的模型名
#   3. log_invocation()         —— 每次调用追加一行凭证日志，可随时审计
# --------------------------------------------------------------------------

_log_path_warned = False


def log_path():
    """凭证日志的落地路径；若被指到 Hermes 的配置文件上，返回 None（放弃写日志）。

    日志是**追加**写，落在 Hermes 的 config.yaml / .env 上会直接把 YAML 毁掉 ——
    比覆盖还隐蔽（文件还是「有内容的」，只是结构烂了）。宁可这次不记日志。
    """
    global _log_path_warned
    raw = os.environ.get("AGNES_LOG_FILE", "").strip() or LOG_PATH
    path = Path(raw).expanduser()
    if is_hermes_protected_file(path):
        if not _log_path_warned:
            _log_path_warned = True
            eprint(f"[agnes-warn] 日志路径指向 Hermes 的配置文件，已跳过写日志：{_short_path(path)}")
        return None
    return path


def log_invocation(
    kind: str,
    model: str,
    endpoint: str,
    status: str = "ok",
    artifact: str = "",
    note: str = "",
) -> None:
    """追加一行调用凭证。日志写失败绝不影响主流程。"""
    try:
        path = log_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not path.exists()
        cells = [
            time.strftime("%Y-%m-%dT%H:%M:%S"),
            kind,
            model,
            endpoint,
            status,
            str(artifact),
            re.sub(r"\s+", " ", str(note)).strip(),
        ]
        with open(path, "a", encoding="utf-8") as fh:
            if is_new:
                fh.write(LOG_HEADER + "\n")
            fh.write("\t".join(cells) + "\n")
    except Exception:
        pass


# Agnes 信任域 / 模型前缀。判断用「域后缀边界」，不用「包含 agnes 子串」：
# agnes.evil.com 的注册域是 evil.com，只是子域蹭了 agnes 字样；
# https://api.agnes-ai.cn@evil.com 的 netloc 里同样含 agnes。
# 子串匹配会把这两种都放过去 —— 那不叫「调到别的服务」，
# 而是把 API Key 直接发到别人的服务器上。
AGNES_TRUSTED_SUFFIXES = ("agnes-ai.cn",)
AGNES_MODEL_PREFIX = "agnes-"


def _trusted_hosts():
    """AGNES_TRUSTED_HOSTS 里额外信任的 host，逗号分隔（自建代理/网关场景）。"""
    raw = os.environ.get("AGNES_TRUSTED_HOSTS", "")
    return tuple(
        h.strip().lower().lstrip(".").rstrip(".") for h in raw.split(",") if h.strip()
    )


def _is_agnes_host(host: str) -> bool:
    """host 是否落在 Agnes 信任域下（含子域）。"""
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    for suffix in AGNES_TRUSTED_SUFFIXES + _trusted_hosts():
        if host == suffix or host.endswith("." + suffix):
            return True
    return False


def guard_target(endpoint: str, model: str) -> str:
    """请求发出前挡住「不像 Agnes」的目标。

    默认严格：
      - 模型 ID 必须以 `agnes-` 开头
      - 域名必须落在 agnes-ai.cn 下（含子域）
    确实要走自建代理/网关时，用 AGNES_TRUSTED_HOSTS 加白名单，
    或显式设 AGNES_ALLOW_ANY_HOST=1 整体放行。

    用 urlparse().hostname 而不是 .netloc：netloc 会把 userinfo 一起带进来，
    https://api.agnes-ai.cn@evil.com 这种写法的 netloc 含 agnes，会骗过判断。
    """
    host = (urllib.parse.urlparse(endpoint).hostname or "").lower()
    relaxed = os.environ.get("AGNES_ALLOW_ANY_HOST", "").strip() in ("1", "true", "True")

    if not (model or "").lower().startswith(AGNES_MODEL_PREFIX):
        guard_fail(
            f'模型 ID 不是 Agnes 模型："{model}"\n'
            f"  本 skill 只调用 Agnes 的图像/视频模型"
            f"（agnes-image-2.5-flash / agnes-video-2.5-flash）。"
        )
    if not _is_agnes_host(host) and not relaxed:
        guard_fail(
            f"目标地址不是 Agnes 域名，已中止：{endpoint}\n"
            f"  当前 host = {host}\n"
            f"  判断依据是「域后缀」而非「含 agnes 字样」：agnes.evil.com 这类伪装域名会被拦下，\n"
            f"  否则 API Key 会被发到别人的服务器上。\n"
            f"  确实要走自建代理，请设 AGNES_TRUSTED_HOSTS=<你的域名>，"
            f"或显式设 AGNES_ALLOW_ANY_HOST=1 整体放行。"
        )
    if not _is_agnes_host(host):
        eprint(f"[agnes] 警告：目标 host 非 Agnes 官方域名（{host}），已按 AGNES_ALLOW_ANY_HOST 放行")
    return host


def extract_response_model(response) -> str:
    """从响应里尽量取出服务端自报的模型名，取不到返回空串。"""
    if not isinstance(response, dict):
        return ""
    for key in ("model", "model_name"):
        value = response.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    data = response.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        value = data[0].get("model")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def verify_response_model(expected: str, response, kind: str) -> str:
    """校验服务端返回的模型名。

    响应里没有 model 字段是常见情况（Agnes 图像接口就不一定返回），此时放行；
    但只要返回了、且不含 agnes，说明打到了非 Agnes 服务，立刻中止。
    """
    actual = extract_response_model(response)
    if actual and "agnes" not in actual.lower():
        fail(
            f"{kind}：服务端自报模型为 \"{actual}\"，不是 Agnes 模型，已中止。\n"
            f"  期望：{expected}\n"
            f"  说明请求打到了非 Agnes 服务，请检查 base_url / 代理配置。"
        )
    return actual


def verify_artifact_host(url: str, kind: str) -> str:
    """校验产物 URL 的域名。

    图像接口不返回 model 字段，所以「产物落在 Agnes 的存储路径下」是图像侧最硬的证据。
    注意官方文档示例里的 CDN 是 `storage.googleapis.com/agnes-aigc/...` —— host 里
    并不含 agnes，只有 path 含。因此这里对 host + path 一起判断，避免误报。
    都不含 agnes 时只警告不中止（官方将来换 CDN 域名也不至于误杀）。
    """
    parsed = urllib.parse.urlparse(url)
    # 同样用 hostname 而不是 netloc：排除 userinfo，避免伪装域名干扰
    host = (parsed.hostname or "").lower()
    haystack = (host + parsed.path).lower()
    if host and "agnes" not in haystack:
        eprint(
            f"[agnes] 警告：{kind}的产物地址 {host} 里看不到 Agnes 标识。"
            f"如果你没配置过自建代理，请留意这条。"
        )
    return host


def print_receipt(
    kind: str, model: str, endpoint: str, note: str = "", key_from_cli: bool = False
) -> None:
    """把「本次真实调用目标」打成一行凭证，给人核对。

    key_from_cli：用 --api-key 显式传入时置 True —— 否则 _identify_key_source()
    只认环境变量和配置文件，会把这行显示成「未知」，反而误导。
    """
    source = "命令行 --api-key" if key_from_cli else (_identify_key_source() or "未知")
    eprint(
        f"[agnes] 调用凭证 · 类型={kind} · 模型={model} · 端点={endpoint} "
        f"· 密钥来源={source}"
        + (f" · {note}" if note else "")
    )
    _log_target = log_path()
    if _log_target is not None:
        eprint(f"[agnes] 凭证已追加到 {_log_target}（可随时审计）")


# --------------------------------------------------------------------------
# 自检入口：python agnes_common.py
# --------------------------------------------------------------------------

def _mask(key: str) -> str:
    if len(key) <= 12:
        return key[:4] + "***"
    return f"{key[:6]}...{key[-4:]}"


def _short_path(path) -> str:
    """把家目录缩成 ~，避免把本机绝对路径（含用户名）写进凭证和输出。

    凭证日志有可能被贴出去求助，路径里带用户名属于不必要的信息暴露。
    """
    p = str(path)
    try:
        home = str(Path.home())
        if home and p.lower().startswith(home.lower()):
            return "~" + p[len(home):].replace("\\", "/")
    except Exception:
        pass
    return p.replace("\\", "/")


def _identify_key_source() -> str:
    if os.environ.get("AGNES_API_KEY", "").strip():
        return "环境变量 AGNES_API_KEY"
    if os.environ.get("AGNES_AI_API_KEY", "").strip():
        return "环境变量 AGNES_AI_API_KEY"
    cfg = load_config()
    for field in ("api_key", "apiKey", "agnes_api_key"):
        if str(cfg.get(field, "")).strip():
            for path in _config_candidates():
                data = read_json(path)
                if isinstance(data, dict) and str(data.get(field, "")).strip():
                    return f"配置文件 {_short_path(path)}"
    hint = _hermes_key_lookup()[1]
    if hint:
        return hint
    if _key_from_workbuddy_models():
        return "WorkBuddy 模型配置 " + _short_path(Path.home() / ".workbuddy" / "models.json")
    return ""


def effective_model_label(kind: str, default: str) -> str:
    """显示当前真正生效的模型，并标明来源。

    默认来自脚本常量；若被 config.json 覆盖则明示，避免自检结果误导。
    """
    cfg = load_config()
    section = cfg.get(kind, {}) if isinstance(cfg.get(kind), dict) else {}
    override = str(section.get("model", "")).strip()
    if override and override != default:
        return f"{override}（被配置文件覆盖，默认应为 {default}）"
    return f"{default}（脚本内写死）"


def selfcheck() -> int:
    print("Agnes 多模态 skill 自检")
    print("-" * 46)
    source = _identify_key_source()
    if source:
        key = resolve_api_key()
        print(f"密钥来源 : {source}")
        print(f"密钥     : {_mask(key)}")
    else:
        print("密钥来源 : 未找到")
    print(f"Base URL : {resolve_base_url()}")
    print(f"轮询端点 : {derive_poll_url(resolve_base_url())}")
    print(f"图像模型 : {effective_model_label('image', IMAGE_MODEL_DEFAULT)}")
    print(f"视频模型 : {effective_model_label('video', VIDEO_MODEL_DEFAULT)}")
    try:
        out_dir = resolve_out_dir()
        print(f"输出目录 : {out_dir}")
    except Exception as exc:
        print(f"输出目录 : 创建失败 {exc}")
    print(f"Python   : {sys.version.split()[0]} ({sys.executable})")
    _log_show = log_path()
    print("凭证日志 : " + (str(_log_show) if _log_show
                          else "已跳过 —— 路径指向 Hermes 的配置文件（本 skill 对 Hermes 只读）"))
    rows = read_all_rows()
    if rows:
        kinds: dict = {}
        for row in rows:
            parts = row.split("\t")
            if len(parts) > 1:
                kinds[parts[1]] = kinds.get(parts[1], 0) + 1
        summary = " / ".join(f"{k} {v} 次" for k, v in sorted(kinds.items()))
        print(f"累计调用 : 共 {len(rows)} 次（{summary}），最近 {rows[-1].split(chr(9))[0]}")
    else:
        print("累计调用 : 还没有记录，跑一次图像或视频脚本后这里会有凭证")
    print("-" * 46)
    if not source:
        print("未找到可用密钥。请让 agent 用 --set-key 写入，或手动执行：")
        print(f"  python {Path(__file__).name} --set-key <KEY>")
        return 1
    print("密钥可用。可以跑 agnes_image.py / agnes_video.py 了。")
    return 0


def read_all_rows() -> list:
    path = log_path()
    if path is None or not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    return [ln for ln in lines[1:] if ln.strip()]


def show_history(limit: int) -> int:
    path = log_path()
    if path is None:
        print("日志路径指向 Hermes 的配置文件，已放弃读写 —— 本 skill 对 Hermes 只读。")
        print("换个位置再试：AGNES_LOG_FILE=~/.agnes/invocations.log")
        return 1
    rows = read_all_rows()
    if not rows:
        print(f"还没有调用记录（{path} 为空或不存在）。")
        return 0
    print(f"最近 {min(limit, len(rows))} / {len(rows)} 条 Agnes 调用凭证")
    print("-" * 78)
    for row in rows[-limit:]:
        cells = row.split("\t")
        while len(cells) < 7:
            cells.append("")
        stamp, kind, model, endpoint, status, artifact, note = cells[:7]
        print(f"{stamp}  [{status}]  {kind:<6} {model}")
        print(f"    端点 : {endpoint}")
        if artifact:
            print(f"    产物 : {artifact}")
        if note:
            print(f"    备注 : {note}")
    print("-" * 78)
    print(f"日志文件：{path}")
    return 0


if __name__ == "__main__":
    _argv = sys.argv
    if len(_argv) > 1 and _argv[1] in ("--history", "-H"):
        _limit = 10
        if len(_argv) > 2:
            try:
                _limit = max(1, int(_argv[2]))
            except ValueError:
                pass
        raise SystemExit(show_history(_limit))
    if "--set-key" in _argv:
        raise SystemExit(cli_set_key(_argv))
    if "--check-key" in _argv or "--check" in _argv:
        raise SystemExit(cli_check_key())
    raise SystemExit(selfcheck())
