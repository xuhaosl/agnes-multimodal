#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agnes AI 多模态公共模块。

设计目标：在 Hermes Agent（local / Docker / Modal 后端）以及任意
macOS / Linux / WSL2 / NAS / Windows 环境上直接跑，不装任何第三方包。

因此这里只用 Python 标准库（urllib），刻意不用 requests / openai SDK。

密钥解析顺序（从高到低）：
  1. 命令行 --api-key
  2. 环境变量 AGNES_API_KEY      <- Hermes 的 required_environment_variables 会注入这个
  3. 环境变量 AGNES_AI_API_KEY
  4. 配置文件 ~/.agnes/config.json 或 ~/.config/agnes/config.json 的 api_key

本文件不读取任何宿主应用的私有配置，只认上面这四条。

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

    这一行是 Hermes 的媒体交付集成点：
      - gateway 会从回复里抽取媒体路径，默认作为**内联图片气泡**发出（会被有损压缩）；
      - agent 若在回复末尾带上 `[[as_document]]` 指令，gateway 会改为
        把路径交付成**可下载的文件附件**（高分辨率图像 / 视频应该走这条）。
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
    return [
        Path.home() / ".agnes" / "config.json",
        Path.home() / ".config" / "agnes" / "config.json",
        Path(__file__).resolve().parent.parent / "config.json",
    ]


def load_config() -> dict:
    for path in _config_candidates():
        data = read_json(path)
        if isinstance(data, dict):
            return data
    return {}


def resolve_api_key(cli_value: str | None = None) -> str:
    """解析 Agnes API Key。

    只认四条来源：命令行参数 / AGNES_API_KEY / AGNES_AI_API_KEY / ~/.agnes/config.json。
    Hermes 场景下首选环境变量 —— frontmatter 声明后会由宿主引导填写并注入沙箱。
    """
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
    fail(
        "没有找到 Agnes API Key。按下面任一种方式提供：\n"
        "  1) 命令行加 --api-key sk-xxxx\n"
        "  2) 设置环境变量 AGNES_API_KEY=sk-xxxx\n"
        "     （Hermes：写进 ~/.hermes/.env，或首次加载本 skill 时按提示填写）\n"
        "  3) 写配置文件 ~/.agnes/config.json -> {\"api_key\": \"sk-xxxx\"}\n"
        "  Key 从 https://www.agnes-ai.cn 控制台获取。"
    )
    raise AssertionError("unreachable")


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
# 宿主环境里可能还有别的生图/生视频能力（那些会消耗配额）。
# 为了让「这次到底调的是不是 Agnes」可被事后核对，而不是一句口头保证，
# 这里做三件事：
#   1. guard_target()           —— 请求发出前，挡住非 Agnes 的 host / 模型名
#   2. verify_response_model()  —— 响应回来后，校验服务端自报的模型名
#   3. log_invocation()         —— 每次调用追加一行凭证日志，可随时审计
# --------------------------------------------------------------------------

def log_path() -> Path:
    raw = os.environ.get("AGNES_LOG_FILE", "").strip() or LOG_PATH
    return Path(raw).expanduser()


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
    eprint(f"[agnes] 凭证已追加到 {log_path()}（可随时审计）")


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
    print(f"凭证日志 : {log_path()}")
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
        print("未找到可用密钥，按 SKILL.md「密钥与配置」补上再试。")
        return 1
    print("密钥可用。可以跑 agnes_image.py / agnes_video.py 了。")
    return 0


def read_all_rows() -> list:
    path = log_path()
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    return [ln for ln in lines[1:] if ln.strip()]


def show_history(limit: int) -> int:
    rows = read_all_rows()
    if not rows:
        print(f"还没有调用记录（{log_path()} 为空或不存在）。")
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
    print(f"日志文件：{log_path()}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--history", "-H"):
        _limit = 10
        if len(sys.argv) > 2:
            try:
                _limit = max(1, int(sys.argv[2]))
            except ValueError:
                pass
        raise SystemExit(show_history(_limit))
    raise SystemExit(selfcheck())
