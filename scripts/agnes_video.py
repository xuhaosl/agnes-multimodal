#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agnes Video 2.5 Flash：文生视频 / 首尾帧控制 / 图片·音频参考生成。

两步式异步接口：
  1) POST https://api.agnes-ai.cn/v1/videos           创建任务 -> 拿 video_id
  2) GET  https://api.agnes-ai.cn/agnesapi?video_id=<ID>&model_name=agnes-video-2.5-flash
     轮询直到 status 为 completed / failed。默认间隔 5 秒：官方建议 1-2 秒，
     但实测该频率会被大量 429 限流（见 SKILL.md「Pitfalls」）
  完成后取响应**顶层 url**（官方文档与实测一致），metadata.url 仅作兼容兜底

用法示例
--------
# 文生视频
python agnes_video.py "夜晚森林中三只猫组成微型铜管乐队向前行进，镜头平稳后退" --seconds 5

# 首尾帧控制（模式自动识别为 keyframe）
python agnes_video.py "人物从首帧姿态自然转身走向窗边，镜头缓慢推进" \\
    --first-frame first.png --last-frame last.png

# 图片参考（模式自动识别为 reference，Flash 最多 5 张）
python agnes_video.py "以 <Picture 1> 的角色和画风为参考，角色在花田中奔跑" --image char.png

# 查询已有任务（video_id 形如 task_xxxxx）
python agnes_video.py --resume task_xxxxx
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agnes_common import (  # noqa: E402
    VIDEO_MODEL_DEFAULT,
    SoftHTTPError,
    build_filename,
    derive_poll_url,
    download,
    emit_result,
    eprint,
    fail,
    guard_target,
    guess_ext,
    http_json,
    is_remote,
    load_config,
    log_invocation,
    print_receipt,
    resolve_api_key,
    resolve_base_url,
    resolve_out_dir,
    to_input_media,
    verify_artifact_host,
    verify_response_model,
)

ASPECT_RATIOS = ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"]

# Flash 硬限制（官方文档：校验在任务创建前执行，失败不创建任务、不计费）
FLASH_MAX_IMAGES = 5
FLASH_MAX_AUDIOS = 3
FLASH_SIZE = "720P"

# 创建任务的重试策略：实测 503（视频队列已满）与 429 会连着来，
# http_json 默认的 2s/4s 退避远远不够（见 SKILL.md Pitfalls：要等到 45s 量级才建得起来）。
# 旧版把这件事写在文档里、让调用方自己退避，属于「文档提醒了但代码没做」——
# 现在内置：45s / 90s / 180s。调用方只需要保证串行。
CREATE_RETRIES = 3
CREATE_BACKOFF = 45.0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agnes_video.py",
        description="Agnes Video 2.5 Flash 视频生成（异步任务 + 自动轮询）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("prompt", nargs="?", default="", help="视频内容描述；--resume 时可不填")
    p.add_argument("--mode", default=None, choices=["text", "keyframe", "reference"],
                   help="生成模式；不填则按传入的媒体自动判断")
    p.add_argument("--seconds", default=None, help='时长字符串 "4"-"12"，默认 "5"')
    p.add_argument("--size", default=None, help="分辨率档位；Flash 固定 720P")
    p.add_argument("--aspect-ratio", default=None, choices=ASPECT_RATIOS, help="画幅，默认 16:9")
    p.add_argument("--first-frame", default=None, metavar="PATH_OR_URL", help="首帧图（keyframe 模式）")
    p.add_argument("--last-frame", default=None, metavar="PATH_OR_URL", help="尾帧图（keyframe 模式）")
    p.add_argument("--image", action="append", default=None, metavar="PATH_OR_URL",
                   help="参考图（reference 模式），可重复")
    p.add_argument("--audio", action="append", default=None, metavar="PATH_OR_URL",
                   help="参考音频（reference 模式），可重复")
    p.add_argument("--seed", type=int, default=None, help="随机种子，同种子提高可复现性")
    p.add_argument("--model", default=None, help=f"模型 ID，默认 {VIDEO_MODEL_DEFAULT}")
    p.add_argument("--poll-interval", type=float, default=None,
                   help="轮询间隔秒数，默认 5（官方建议 1-2 秒，但实测该频率会被大量 429 限流）")
    p.add_argument("--max-wait", type=int, default=None, help="最长等待秒数，默认 900")
    p.add_argument("--resume", default=None, metavar="VIDEO_ID", help="查询已存在的任务")
    p.add_argument("--out-dir", default=None)
    p.add_argument("--name", default=None)
    p.add_argument("--url-only", action="store_true", help="只输出视频 URL，不下载")
    p.add_argument("--json", action="store_true", help="打印接口原始响应 JSON")
    p.add_argument("--api-key", default=None)
    p.add_argument("--base-url", default=None)
    p.add_argument("--poll-url", default=None, help="覆盖轮询端点，默认由 base-url 推导")
    p.add_argument("--timeout", type=int, default=None, help="单次请求超时秒数")
    return p


def poll_endpoint(base_url: str, override: str | None) -> str:
    if override:
        return override
    env = os.environ.get("AGNES_POLL_URL", "").strip()
    if env:
        return env
    return derive_poll_url(base_url)


def detect_mode(args) -> str:
    if args.mode:
        return args.mode
    if args.first_frame or args.last_frame:
        return "keyframe"
    if args.image or args.audio:
        return "reference"
    return "text"


def validate(mode: str, model: str, size: str, images, audios, first_frame, last_frame) -> None:
    """把官方校验规则前置到本地，避免白跑一次请求。

    官方文档明确：Flash 专属校验在任务创建、排队、计费和推理前执行，
    校验失败的请求不会创建任务、不产生费用——但本地先拦住更省事。
    """
    is_flash = "flash" in model.lower()
    if is_flash and size != FLASH_SIZE:
        fail(f'{model} 的 size 只支持 "{FLASH_SIZE}"，当前是 "{size}"')
    if mode == "text" and (first_frame or last_frame or images or audios):
        fail("text 模式不允许携带 first_frame / last_frame / images / audios")
    if mode == "keyframe":
        if not (first_frame or last_frame):
            fail("keyframe 模式至少要提供 first_frame 或 last_frame 之一")
        if images or audios:
            fail("keyframe 模式不允许携带 images / audios")
    if mode == "reference":
        max_img = FLASH_MAX_IMAGES if is_flash else 8
        max_aud = FLASH_MAX_AUDIOS if is_flash else 3
        if first_frame or last_frame:
            fail("reference 模式不允许携带 first_frame / last_frame")
        if not images and not audios:
            fail("reference 模式必须至少提供一张 --image 或一段 --audio")
        if len(images) > max_img:
            fail(f"reference 模式参考图最多 {max_img} 张，当前 {len(images)} 张")
        if len(audios) > max_aud:
            fail(f"reference 模式参考音频最多 {max_aud} 段，当前 {len(audios)} 段")
        if len(images) + len(audios) > 12:
            fail("单次请求参考媒体总数不得超过 12 个")


def resolve_media_input(value: str, kind: str) -> str:
    """远端 URL 原样使用；本地文件转 Data URI（尽力而为，见下方提醒）。"""
    if is_remote(value):
        return value
    eprint(
        f"[agnes-video] 提醒：官方要求 {kind} 是「可公开访问的 URL」。"
        f"已把本地文件转成 Data URI 尝试提交，若接口返回 400/Invalid reference media，"
        f"请先把文件放到公网可访问的地址再传 URL。"
    )
    return to_input_media(value)


def main() -> int:
    args = build_parser().parse_args()

    cfg = load_config()
    cfg_video = cfg.get("video", {}) if isinstance(cfg.get("video"), dict) else {}

    model = args.model or cfg_video.get("model") or VIDEO_MODEL_DEFAULT
    seconds = str(args.seconds or cfg_video.get("seconds") or "5")
    size = args.size or cfg_video.get("size") or FLASH_SIZE
    aspect_ratio = args.aspect_ratio or cfg_video.get("aspect_ratio") or "16:9"
    poll_interval = float(args.poll_interval or cfg_video.get("poll_interval", 5) or 5)
    max_wait = int(args.max_wait or cfg_video.get("max_wait", 900) or 900)
    timeout = args.timeout or int(cfg.get("timeout", 300) or 300)

    api_key = resolve_api_key(args.api_key)
    base_url = resolve_base_url(args.base_url)
    poll_url = poll_endpoint(base_url, args.poll_url)
    # 查询端点同样要过守卫：--resume / --poll-url / AGNES_POLL_URL 都不能成为绕过口
    guard_target(poll_url, model)
    out_dir = resolve_out_dir(args.out_dir)

    images = args.image or []
    audios = args.audio or []

    # 审计用：本次真实打到的端点（创建任务时是 /videos，--resume 时是查询端点）
    audit_endpoint = poll_url
    actual_model = ""

    # ---------------- 阶段 1：创建任务 ----------------
    if args.resume:
        video_id = args.resume.strip()
        mode = args.mode or "text"
        eprint(f"[agnes-video] 复用任务 video_id={video_id}")
    else:
        if not args.prompt.strip():
            fail("缺少 prompt（视频内容描述）")
        try:
            seconds_num = float(seconds)
        except ValueError:
            fail(f'seconds 需为数字字符串 "4"-"12"，当前 "{seconds}"')
        if not (4 <= seconds_num <= 12):
            fail(f'seconds 需为字符串 "4"-"12"，当前 "{seconds}"')

        mode = detect_mode(args)
        validate(mode, model, size, images, audios, args.first_frame, args.last_frame)

        payload: dict = {
            "model": model,
            "prompt": args.prompt,
            "mode": mode,
            "seconds": str(seconds),
            "size": size,
            "n": 1,
        }
        if aspect_ratio:
            payload["aspect_ratio"] = aspect_ratio
        if args.seed is not None:
            payload["seed"] = args.seed

        if mode == "keyframe":
            if args.first_frame:
                payload["first_frame"] = resolve_media_input(args.first_frame, "首帧图")
            if args.last_frame:
                payload["last_frame"] = resolve_media_input(args.last_frame, "尾帧图")
        elif mode == "reference":
            if images:
                payload["images"] = [resolve_media_input(v, "参考图") for v in images]
            if audios:
                payload["audios"] = [resolve_media_input(v, "参考音频") for v in audios]

        create_url = f"{base_url}/videos"
        audit_endpoint = create_url
        eprint(f"[agnes-video] 创建任务 mode={mode} model={model} seconds={seconds} size={size} -> {create_url}")
        # 请求前先确认目标确实在 Agnes 上（非 Agnes 域名/模型名直接拒绝）
        guard_target(create_url, model)

        created = http_json("POST", create_url, api_key, payload=payload, timeout=timeout,
                            retries=CREATE_RETRIES, backoff=CREATE_BACKOFF)
        if args.json:
            print(json.dumps(created, ensure_ascii=False, indent=2))

        actual_model = verify_response_model(model, created, "视频任务创建")

        video_id = created.get("video_id") or created.get("id") or created.get("task_id")
        if not video_id:
            fail(f"创建任务响应里没有 video_id：{json.dumps(created, ensure_ascii=False)[:400]}")
        eprint(f"[agnes-video] 任务已创建 video_id={video_id} status={created.get('status')}")

    # ---------------- 阶段 2：轮询 ----------------
    query = {"video_id": video_id, "model_name": model}
    last_progress = -1
    total_429 = 0
    consecutive_429 = 0
    started = time.time()
    result: dict = {}

    while True:
        elapsed = time.time() - started
        if elapsed > max_wait:
            fail(
                f"轮询超过上限 {max_wait}s 仍未完成（video_id={video_id}，进度 {last_progress}%）。\n"
                f"  任务仍在后台跑，可用 --resume {video_id} 稍后再查。"
            )

        try:
            # retries=1：重试策略由本循环自己控，避免和内部退避叠加刷屏
            result = http_json(
                "GET",
                f"{poll_url}?{urllib.parse.urlencode(query)}",
                api_key,
                payload=None,
                timeout=timeout,
                retries=1,
                soft_statuses=(404, 429),
            )
            consecutive_429 = 0
        except SoftHTTPError as exc:
            if exc.code == 404:
                # 404 且当前带了 model_name -> 回退到纯 video_id 查询（仅 mode=text 有效）
                if "model_name" in query:
                    eprint("[agnes-video] 带 model_name 查询返回 404，回退到纯 video_id 查询")
                    query.pop("model_name", None)
                    continue
                fail(
                    f"查询任务失败：video_id={video_id} 不存在或已过期（HTTP 404）。\n"
                    f"  请确认用的是创建任务响应里的 video_id。"
                )
            # 429：查询限流。官方建议 1-2 秒轮询，但实测这个频率会被大量限流，
            # 所以默认间隔放到 5 秒，真被限流时再指数退避（上限 30 秒）。
            total_429 += 1
            consecutive_429 += 1
            wait = min(poll_interval * (2 ** (consecutive_429 - 1)), 30.0)
            eprint(f"[agnes-video] 查询被限流(429)，{wait:.0f}s 后重试（累计第 {total_429} 次）")
            time.sleep(wait)
            continue

        status = str(result.get("status", "")).lower()
        progress = int(result.get("progress") or 0)
        if progress != last_progress:
            eprint(f"[agnes-video] {elapsed:5.0f}s  status={status or '?'}  progress={progress}%")
            last_progress = progress

        if status == "completed":
            # 响应里若带服务端自报模型名，校验它确实是 Agnes；不带则保留创建阶段的结论
            actual_model = verify_response_model(model, result, "视频任务查询") or actual_model
            break
        if status == "failed":
            message = ""
            err = result.get("error")
            if isinstance(err, dict):
                message = str(err.get("message", ""))
            elif isinstance(err, str):
                message = err
            fail(f"视频生成失败：{message or json.dumps(result, ensure_ascii=False)[:400]}")

        time.sleep(poll_interval)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    # 成品地址：官方文档（Flash 页与 Video 2.5 页）与实测一致，
    # 都在**顶层 url** 字段。metadata.url 只是历史兼容兜底，正常不会命中。
    metadata = result.get("metadata") or {}
    url = ""
    if isinstance(metadata, dict):
        url = metadata.get("url") or ""
    if not url:
        url = result.get("url") or ""
    if not url:
        fail(
            "status=completed 但响应里找不到 url："
            f"{json.dumps(result, ensure_ascii=False)[:400]}"
        )

    def finish(artifact: str, remote_url: str = "") -> None:
        host = verify_artifact_host(remote_url, "视频") if remote_url else ""
        log_invocation(
            "video", model, audit_endpoint, "ok", artifact,
            f"server_model={actual_model or 'not-returned'} artifact_host={host or 'n/a'} "
            f"video_id={video_id} seconds={seconds}",
        )
        evidence = f"服务端自报模型={actual_model or '未返回'}"
        if host:
            evidence += f" · 产物域名={host}"
        evidence += f" · video_id={video_id}"
        print_receipt("video", model, audit_endpoint, evidence, key_from_cli=bool(args.api_key))

    if args.url_only:
        finish(url, url)
        print(url, flush=True)
        return 0

    ext = guess_ext(url, ".mp4")
    stem = args.name or ""
    filename = build_filename(stem, args.prompt, ext)
    path = download(url, out_dir, filename, timeout=max(timeout, 600))
    eprint(f"[agnes-video] 已保存 {path.stat().st_size / (1024 * 1024):.1f} MB")
    finish(str(path), url)
    emit_result(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
