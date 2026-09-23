# Agnes Video 2.5 Flash — 接口速查

来源：
- https://www.agnes-ai.cn/zh-Hans/docs/agnes-video-25-flash
- https://www.agnes-ai.cn/zh-Hans/docs/agnes-video-25 （Flash 沿用其公共参数、响应字段与查询方式）

## 基本信息

- 模型 ID：`agnes-video-2.5-flash`
- 创建任务：`POST https://api.agnes-ai.cn/v1/videos`
- 查询任务：`GET https://api.agnes-ai.cn/agnesapi?video_id=<VIDEO_ID>&model_name=agnes-video-2.5-flash`
- 请求头：`Authorization: Bearer <API_KEY>`、`Content-Type: application/json`
- **异步任务**：先创建拿 `video_id`，再轮询到 `completed` / `failed`
- 价格：720P 原价 ¥0.15/秒，**当前现价 ¥0/秒**

## 创建任务：公共参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `model` | string | 是 | `agnes-video-2.5-flash` |
| `prompt` | string | 是 | 视频内容描述；reference 模式可用 `<Picture N>` `<Audio N>` 指代素材 |
| `mode` | string | 是 | `text` / `keyframe` / `reference` |
| `seconds` | string | 否 | 字符串 `"4"`–`"12"`，默认 `"5"` |
| `size` | string | 否 | Flash **固定 `"720P"`**，其他值返回 HTTP 400 `size must be 720P` |
| `aspect_ratio` | string | 否 | 默认 `16:9` |
| `seed` | integer | 否 | 随机种子，同种子提高可复现性 |
| `n` | integer | 否 | 当前仅支持 `1`，默认 `1` |

## 模式专用参数

| 参数 | 类型 | 适用模式 | 说明 |
| --- | --- | --- | --- |
| `first_frame` | string | `keyframe` | 首帧图 URL；与 `last_frame` 至少提供一个 |
| `last_frame` | string | `keyframe` | 尾帧图 URL；与 `first_frame` 至少提供一个 |
| `images` | string[] | `reference` | 参考图 URL 列表，Flash 最多 5 张 |
| `audios` | string[] | `reference` | 参考音频 URL 列表，Flash 最多 3 段 |
| `videos` | object[] | `reference` | **Flash 不支持**，传有效内容返回 HTTP 400 `videos is not supported` |

参考视频对象字段（仅非 Flash 模型）：`url`（必填）、`start_seconds`（默认 0）、`require_audio`（默认 false）。

## 模式规则

| mode | 用途 | 必需媒体 | 不允许的字段 |
| --- | --- | --- | --- |
| `text` | 纯文本生成 | 无 | `first_frame` `last_frame` `images` `audios` `videos` |
| `keyframe` | 首帧/尾帧/首尾帧控制 | `first_frame` 与 `last_frame` 至少一个 | `images` `audios` `videos` |
| `reference` | 图片或音频参考 | `images` 或 `audios` 至少一类非空 | `first_frame` `last_frame` `videos` |

参考媒体限制（Agnes Video 2.5 通用）：
图片最多 8 张、单张 <15 MB、请求总大小 <50 MB、宽高各 256–5760 px；
音频最多 3 段、总时长 2–12 秒、单个 <15 MB；
视频最多 1 个、总时长 2–12 秒、单个 <50 MB、帧率 24–60 FPS。
单次请求参考媒体总数 ≤ 12。
**所有媒体 URL 必须可由 Agnes 服务公开访问，并在任务完成前保持有效。**

## Flash 专属校验

在任务创建、排队、计费和推理**之前**执行；校验失败不创建任务、不产生费用。
多个错误同时存在时，按 `size` → `images` → `audios` → `videos` 的顺序返回首个。

| 校验项 | Flash 规则 | 失败响应（HTTP 400） |
| --- | --- | --- |
| `size` | 仅支持字符串 `"720P"` | `{"detail": "size must be 720P"}` |
| `reference` 图片数 | 最多 5 张 | `{"detail": "images length must not exceed 5"}` |
| `reference` 音频数 | 最多 3 段 | `{"detail": "audios length must not exceed 3"}` |
| 参考视频 | 不支持 | `{"detail": "videos is not supported"}` |

## 创建任务响应

```json
{
  "id": "task_YOUR_TASK_ID",
  "task_id": "task_YOUR_TASK_ID",
  "video_id": "video_YOUR_VIDEO_ID",
  "object": "video",
  "model": "agnes-video-2.5",
  "status": "queued",
  "progress": 0,
  "created_at": 1786900000,
  "seconds": "5",
  "size": "720P"
}
```

`id` 与 `task_id` 是任务 ID；**查询要用 `video_id`**。

## 查询任务响应（完成）

官方文档示例（`agnes-video-2.5`，Flash 沿用同一响应格式）：

```json
{
  "completed_at": 1790062857,
  "created_at": 1790062812,
  "error": null,
  "expires_at": null,
  "id": "task_YOUR_TASK_ID",
  "internal_progress": 0,
  "internal_status": "pending",
  "object": "video",
  "progress": 100,
  "quality": "standard",
  "remixed_from_video_id": null,
  "seconds": "4",
  "size": "720P",
  "started_at": 1790062812,
  "status": "completed",
  "url": "https://example.com/generated/video.mp4"
}
```

- `status`：`queued` / `in_progress` / `completed` / `failed`
- `progress`：0–100
- **成品地址：顶层 `url`**。官方两篇文档都写「顶层 `url` 字段」，字段表还专门注明
  `url` 是「顶层字段；任务完成后用于播放或下载视频」，并强调「请以 `status` 和 `url` 为准」
- `metadata` 只在**失败**响应里出现（值为 `null`），不是成品地址的载体
- 失败时：`error.message` 带原因

### 实测确认（2026-09-15，agnes-video-2.5-flash 中国站）

实测完成响应与官方文档一致：成品地址在**顶层 `url`**，响应里**没有 `metadata` 字段**：

```json
{
  "completed_at": 1789452621,
  "created_at": 1789452500,
  "error": null,
  "expires_at": null,
  "id": "task_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "internal_progress": 0,
  "internal_status": "pending",
  "object": "video",
  "progress": 100,
  "quality": "standard",
  "remixed_from_video_id": null,
  "seconds": "4",
  "size": "720P",
  "started_at": 1789452588,
  "status": "completed",
  "url": "https://cos-platform-outputs.agnes-ai.cn/videos/agnes-video-2.5/task_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.mp4"
}
```

**取值策略：直接取顶层 `url`。** 脚本另留了 `metadata.url` 兜底分支（历史兼容），正常不会命中。
另外注意 `internal_status` / `internal_progress` 是**内部字段**（官方文档明示「不用于判断任务是否完成」，
示例里固定为 `pending` / `0`），**不要拿它判进度**，只看 `status` 和 `progress`。

> 上面这段实测响应里的**任务 ID 与产物地址已脱敏**（原值是实测真实产生的任务，
> 不宜随公开仓库散布）。字段名、字段结构、时间戳与取值逻辑均未改动。

失败示例：

```json
{
  "status": "failed",
  "progress": 100,
  "metadata": null,
  "error": { "message": "Invalid reference media" }
}
```

**只有 `status == "completed"` 时成品地址才是可交付的。**

## 轮询要求

- 官方建议每隔 **1–2 秒**查一次
- ⚠️ **实测 1–2 秒会被大量 429 限流**：
  - 2.5 秒间隔：`429 查询过于频繁，请稍后重试` 频繁出现，几乎每隔一次就被拦
  - **5 秒间隔：实测全程零限流**
  - 建议默认 5 秒，并对 429 做指数退避（上限 30 秒）
- 4 秒时长视频的实测耗时：
  - 创建 → `in_progress 10%`（约 88–109s）→ `completed 100%`（约 121–150s）
  - 即**约 2–2.5 分钟**。`max_wait` 默认给到 900 秒是合理的
- **推荐始终带 `model_name`**：`video_id + model_name` 适用于 `text` / `keyframe` / `reference` 全部模式
- 纯 `video_id` 查询**仅适用于创建时 `mode: "text"`** 的任务；`keyframe` / `reference` 不带 `model_name` 会查不到
- 生产环境要设最大轮询时长，并对网络超时和 `429` 做退避重试

## 视频画幅 → 输出像素

| aspect_ratio | 输出像素 |
| --- | --- |
| 21:9 | 1680x720 |
| 16:9 | 1280x704 |
| 4:3 | 960x720 |
| 1:1 | 720x720 |
| 3:4 | 720x960 |
| 9:16 | 720x1280 |

16:9 以实际生成文件为准。2026 年 9 月实测 Flash 的 720P 输出为 `1280x704`。

## 错误码

| HTTP | 常见原因 | 处理 |
| --- | --- | --- |
| 400 | 参数缺失、模式与媒体不匹配、时长或画幅非法 | 查请求字段与模式校验规则 |
| 401 / 403 | API Key 无效、过期或无权限 | 查请求头、密钥状态、模型权限 |
| 404 | 视频 ID 不存在 | 确认用创建响应里的 `video_id` |
| 429 | 请求频率超限 | 指数退避并降低轮询频率 |
| 500 | 服务端内部错误 | 稍后重试；持续失败联系技术支持 |

## 计费公式

```
视频总金额 = 输出秒数 × 输出分辨率单价
           + 输入视频秒数 × 输出分辨率单价
           + max(0, 图片数 - 免费图片张数) × 图片超额单价
```

按刊例价免费图片张数为 5 张。Flash 仅支持 720P、最多 5 张参考图 + 3 段参考音频。
当前限时免费期内输出秒数、输入视频秒数、参考图片均按 ¥0 计费。

## Python SDK 写法（官方示例，供参考）

官方示例用 `openai` SDK：`client = OpenAI(api_key=..., base_url="https://api.agnes-ai.cn/v1")`，
然后 `client.videos.create(model=..., prompt=..., seconds=..., size=..., extra_body={"mode": ..., "aspect_ratio": ..., "videos": [...]})`。
`mode`、`aspect_ratio` 和媒体字段通过 `extra_body` 合并到请求 JSON 顶层。

本 skill 刻意不依赖 SDK，直接用 urllib 发原始 JSON —— 参数名与 REST 层完全一致，跨平台无安装成本。
