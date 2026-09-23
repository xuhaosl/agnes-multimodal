#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agnes Image 2.5 Flash：文生图 / 图生图 / 多图合成。

接口：POST https://api.agnes-ai.cn/v1/images/generations

用法示例
--------
# 文生图（2K，16:9）
python agnes_image.py "日出时分薄雾峡谷上方的发光浮空城市，电影级写实，广角" --size 2K --ratio 16:9

# 图生图（本地图片自动转 Data URI）
python agnes_image.py "改成雨夜赛博朋克风格，保留原始构图" --image .\\input.png --size 2K

# 多图合成
python agnes_image.py "第一张作主角、第二张作产品参考，生成活动海报" \\
    --image a.png --image b.png --size 2K --ratio 3:4

# 只要 URL，不下载
python agnes_image.py "一只戴墨镜的章鱼" --url-only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agnes_common import (  # noqa: E402
    IMAGE_MODEL_DEFAULT,
    build_filename,
    download,
    emit_result,
    eprint,
    fail,
    guard_target,
    guess_ext,
    http_json,
    load_config,
    log_invocation,
    print_receipt,
    resolve_api_key,
    resolve_base_url,
    resolve_out_dir,
    save_base64,
    to_input_media,
    verify_artifact_host,
    verify_response_model,
)

RATIOS = ["1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agnes_image.py",
        description="Agnes Image 2.5 Flash 文生图 / 图生图 / 多图合成",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("prompt", help="图像生成或编辑的文本指令")
    p.add_argument("--image", action="append", default=None, metavar="PATH_OR_URL",
                   help="输入参考图；本地路径会自动转 Data URI。可重复传入实现多图合成")
    p.add_argument("--size", default=None, help="尺寸档位 1K/2K/3K/4K，或历史精确写法如 1024x768")
    p.add_argument("--ratio", default=None, choices=RATIOS, help="宽高比，默认 1:1")
    p.add_argument("--model", default=None, help=f"模型 ID，默认 {IMAGE_MODEL_DEFAULT}")
    p.add_argument("--base64", action="store_true", help="以 Base64 方式返回并落盘（不依赖外链有效期）")
    p.add_argument("--out-dir", default=None, help="产物目录，默认 ~/agnes-output")
    p.add_argument("--name", default=None, help="输出文件名主干（不含扩展名）")
    p.add_argument("--url-only", action="store_true", help="只输出图片 URL，不下载")
    p.add_argument("--json", action="store_true", help="打印接口原始响应 JSON")
    p.add_argument("--api-key", default=None)
    p.add_argument("--base-url", default=None)
    p.add_argument("--timeout", type=int, default=None, help="单次请求超时秒数，官方建议 60-360")
    return p


def main() -> int:
    args = build_parser().parse_args()

    cfg = load_config()
    cfg_image = cfg.get("image", {}) if isinstance(cfg.get("image"), dict) else {}

    model = args.model or cfg_image.get("model") or IMAGE_MODEL_DEFAULT
    size = args.size or cfg_image.get("size") or "2K"
    ratio = args.ratio or cfg_image.get("ratio") or "1:1"
    timeout = args.timeout or int(cfg.get("timeout", 300) or 300)

    api_key = resolve_api_key(args.api_key)
    base_url = resolve_base_url(args.base_url)
    endpoint = f"{base_url}/images/generations"
    out_dir = resolve_out_dir(args.out_dir)

    is_edit = bool(args.image)
    want_b64 = bool(args.base64) or str(cfg_image.get("response_format", "")).lower() == "b64_json"

    payload: dict = {
        "model": model,
        "prompt": args.prompt,
        "size": size,
        "ratio": ratio,
    }
    # 注意：response_format 绝不能放顶层，必须塞进 extra_body（官方明确的踩坑点）
    extra_body: dict = {"response_format": "b64_json" if want_b64 else "url"}

    if is_edit:
        # 图生图 / 多图合成：只需给 extra_body.image，不需要 tags: ["img2img"]
        extra_body["image"] = [to_input_media(item) for item in args.image]
        eprint(f"[agnes-image] 图生图模式，参考图 {len(extra_body['image'])} 张")
    elif want_b64:
        # 文生图取 Base64 用顶层 return_base64
        payload["return_base64"] = True

    payload["extra_body"] = extra_body

    eprint(f"[agnes-image] model={model} size={size} ratio={ratio} -> {endpoint}")
    # 请求前先确认目标确实在 Agnes 上（非 Agnes 域名/模型名直接拒绝）
    guard_target(endpoint, model)

    body = http_json("POST", endpoint, api_key, payload=payload, timeout=timeout)

    if args.json:
        print(__import__("json").dumps(body, ensure_ascii=False, indent=2))

    # 响应里若带服务端自报模型名，校验它确实是 Agnes；不带则放行
    actual_model = verify_response_model(model, body, "图像生成")

    def finish(artifact: str, remote_url: str = "") -> None:
        host = verify_artifact_host(remote_url, "图像") if remote_url else ""
        evidence = f"服务端自报模型={actual_model or '未返回'}"
        if host:
            evidence += f" · 产物域名={host}"
        log_invocation(
            "image", model, endpoint, "ok", artifact,
            f"server_model={actual_model or 'not-returned'} artifact_host={host or 'n/a'}",
        )
        print_receipt("image", model, endpoint, evidence, key_from_cli=bool(args.api_key))

    items = body.get("data") or []
    if not items:
        fail(f"接口未返回图像数据，原始响应：{__import__('json').dumps(body, ensure_ascii=False)[:500]}")

    item = items[0]
    url = item.get("url")
    b64 = item.get("b64_json")

    if not url and not b64:
        fail(f"响应里既没有 url 也没有 b64_json：{__import__('json').dumps(item, ensure_ascii=False)[:400]}")

    if b64:
        ext = ".png"
        filename = build_filename(args.name or "", args.prompt, ext)
        path = save_base64(b64, out_dir, filename)
        eprint(f"[agnes-image] 已保存 Base64 结果，{path.stat().st_size / 1024:.0f} KB")
        finish(str(path))
        emit_result(str(path))
        return 0

    if args.url_only:
        finish(url, url)
        print(url, flush=True)
        return 0

    ext = guess_ext(url, ".png")
    filename = build_filename(args.name or "", args.prompt, ext)
    path = download(url, out_dir, filename, timeout=timeout)
    eprint(f"[agnes-image] 已保存 {path.stat().st_size / 1024:.0f} KB")
    finish(str(path), url)
    emit_result(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
