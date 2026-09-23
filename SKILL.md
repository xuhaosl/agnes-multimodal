---
name: agnes-multimodal
description: Agnes 图像与视频生成：文生图、图生图、多图合成、文生视频、首尾帧、图片音频参考。触发词：生成图片、画一张、文生图、图生图、生成视频、文生视频、Agnes 图像、Agnes 视频。
version: 1.0.0
author: xuhaosl
license: MIT
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [Media, Image Generation, Video Generation, Agnes, API]
required_environment_variables:
  - name: AGNES_API_KEY
    prompt: "Agnes AI API Key"
    help: "在 https://www.agnes-ai.cn 控制台获取，形如 sk-xxxx"
    required_for: "调用 Agnes 图像 / 视频生成接口"
---

# Agnes 多模态生成（图像 + 视频）

用两个纯 Python 标准库脚本调用 Agnes AI 的多模态模型，**不装任何第三方包**，
在 Hermes Agent 的 local / Docker / Modal 后端上都能直接跑。

- 图像 `agnes-image-2.5-flash` — 文生图 / 图生图 / 多图合成
- 视频 `agnes-video-2.5-flash` — 文生视频 / 首尾帧 / 图片·音频参考（异步任务 + 自动轮询）

接口参数以官方文档为准：
图像 https://www.agnes-ai.cn/zh-Hans/docs/agnes-image-25-flash
视频 https://www.agnes-ai.cn/zh-Hans/docs/agnes-video-25-flash

## When to Use

用户要「画一张图 / 生成图片 / 文生图 / 改这张图 / 把两张图合成 / 生成视频 / 文生视频 / 做一段动画」，
就走本 skill —— **用户不需要额外声明「用 Agnes」**。

不适用：

- 只是想在聊天里画个示意图（那种场景通常有别的轻量手段，不必调 API 花额度）
- 用户明确点名要用别的模型（按用户要求走，但先提示会产生对应消耗）

## 关键约束（先读这段）

### 模型与端点是硬锁的

脚本内常量，**与宿主 Agent 的模型设置完全无关**：

```python
DEFAULT_BASE_URL    = "https://api.agnes-ai.cn/v1"
IMAGE_MODEL_DEFAULT = "agnes-image-2.5-flash"
VIDEO_MODEL_DEFAULT = "agnes-video-2.5-flash"
```

自检输出会直接打出来，一眼可核对：

```
图像模型 : agnes-image-2.5-flash（脚本内写死）
视频模型 : agnes-video-2.5-flash（脚本内写死）
```

即使宿主里配了别的图像 / 视频模型，本 skill 跑出来的一定是上面这两个 —— 因为不走宿主的模型路由。

### 三道守卫

| 守卫 | 触发条件 | 行为 |
| --- | --- | --- |
| `guard_target()` | 模型 ID 不以 `agnes-` 开头，或域名不在 `agnes-ai.cn` 下（含子域） | 中止，退出码 **3**（打印 `[agnes-guard]`），**请求不发出** |
| `verify_response_model()` | 服务端自报模型名且不含 `agnes` | 中止，打印实际模型名 |
| `verify_artifact_host()` | 产物 URL 域名不含 `agnes` | 警告（非阻断），记入凭证 |

`guard_target()` 覆盖**创建任务**和**轮询查询**两条路径，`--base-url` /
`--poll-url` / `AGNES_POLL_URL` 都拦得住，`--resume` 也不例外。

**域名判断用「域后缀边界」，不是「含 agnes 字样」。** 后者会让 `agnes.evil.com`
（注册域是 `evil.com`，只是子域蹭了字样）和 `https://api.agnes-ai.cn@evil.com`
（netloc 里同样含 agnes）蒙混过关 —— 那不是「调到别的服务」，是把 API Key 直接发给别人。
所以判定走 `urlparse().hostname`（排除 userinfo），信任域是 `agnes-ai.cn` 及其子域。

逃生门：要走自建代理/网关，设 `AGNES_TRUSTED_HOSTS=your.host`（逗号分隔可多个）；
确实要彻底放开域名检查才设 `AGNES_ALLOW_ANY_HOST=1`。**两个都默认关。**

**退出码**：`0` 成功 · `2` 请求/网络/API 类失败 · `3` 被守卫在请求发出前拦下。
守卫单独用 3，是为了让「守卫生效」能被脚本验证 —— 和网络失败同为 2 就分不清了。

### 凭证日志（可事后对账）

每次成功调用追加一行 TSV 到 `~/.agnes/invocations.log`，**不含 API Key**。
列：`time  kind  model  endpoint  status  artifact  note`。

对账逻辑：让 agent 生成 N 张图，`--history` 里就该有 N 条 `image` 记录；
条数对不上说明有调用走了别处。日志路径可用 `AGNES_LOG_FILE` 覆盖。

## Quick Reference

`${HERMES_SKILL_DIR}` 由 Hermes 自动替换成本 skill 的绝对目录，直接用即可。

### 图像

| 目的 | 命令 |
| --- | --- |
| 文生图 | `python "${HERMES_SKILL_DIR}/scripts/agnes_image.py" "提示词" --size 2K --ratio 16:9` |
| 图生图 | `python "${HERMES_SKILL_DIR}/scripts/agnes_image.py" "改成雨夜赛博朋克，保留原构图" --image 输入图.png --size 2K` |
| 多图合成 | `python "${HERMES_SKILL_DIR}/scripts/agnes_image.py" "第一张作主角、第二张作产品参考，出海报" --image a.png --image b.png` |
| 只要 URL 不下载 | 加 `--url-only` |
| 看接口原始响应 | 加 `--json` |

### 视频

| 目的 | 命令 |
| --- | --- |
| 文生视频 | `python "${HERMES_SKILL_DIR}/scripts/agnes_video.py" "提示词" --seconds 5 --aspect-ratio 16:9` |
| 首尾帧 | `python "${HERMES_SKILL_DIR}/scripts/agnes_video.py" "提示词" --first-frame first.png --last-frame last.png` |
| 图片参考 | `python "${HERMES_SKILL_DIR}/scripts/agnes_video.py" "以 <Picture 1> 的角色为参考…" --image char.png` |
| 音频参考 | `python "${HERMES_SKILL_DIR}/scripts/agnes_video.py" "以 <Audio 1> 的节奏为参考…" --audio bgm.mp3` |
| 查已有任务 | `python "${HERMES_SKILL_DIR}/scripts/agnes_video.py" --resume task_xxxxx` |

`--mode` 不填会自动判断：给了 first/last-frame → `keyframe`；给了 image/audio → `reference`；都没给 → `text`。

### 审计

```bash
# 自检：密钥来源 / 端点 / 当前生效模型 / 累计调用数
python "${HERMES_SKILL_DIR}/scripts/agnes_common.py"

# 最近 20 条调用凭证
python "${HERMES_SKILL_DIR}/scripts/agnes_common.py" --history 20
```

### 关键参数速查

- 图像 `--size`：`1K` `2K` `3K` `4K`（官方推荐档位式写法），或 `1024x768` 这类历史精确写法。
  **官方未规定默认值**（`size` 是必填项），脚本默认取 `2K`
- 图像 `--ratio`：`1:1` `3:4` `4:3` `16:9` `9:16` `2:3` `3:2` `21:9`，官方默认 `1:1`
- 视频 `--size`：Flash **固定 `720P`**（其他值返回 HTTP 400）
- 视频 `--aspect-ratio`：`21:9` `16:9` `4:3` `1:1` `3:4` `9:16`，官方默认 `16:9`
- 视频 `--seconds`：字符串 `"4"`–`"12"`，官方默认 `"5"`
- 视频 `--seed`：随机种子，传相同种子可提高可复现性
- 单次请求超时：默认 300 秒（`--timeout`；官方建议 60–360 秒）
- 输出目录：默认 `~/agnes-output`（`--out-dir` 或 `AGNES_OUT_DIR` 覆盖）
- 视频轮询：默认间隔 5 秒、最长等 900 秒（`--poll-interval` / `--max-wait` 可调）

尺寸对照（档位 × 比例 → 实际像素）：

| ratio | 1K | 2K | 3K | 4K |
| --- | --- | --- | --- | --- |
| 1:1 | 1024×1024 | 2048×2048 | 3072×3072 | 4096×4096 |
| 16:9 | 1312×736 | 2624×1472 | 3936×2208 | 5248×2944 |
| 9:16 | 736×1312 | 1472×2624 | 2208×3936 | 2944×5248 |
| 3:4 | 864×1152 | 1728×2304 | 2592×3456 | 3456×4608 |
| 4:3 | 1152×864 | 2304×1728 | 3456×2592 | 4608×3456 |
| 2:3 | 832×1248 | 1664×2496 | 2496×3744 | 3328×4992 |
| 3:2 | 1248×832 | 2496×1664 | 3744×2496 | 4992×3328 |
| 21:9 | 1568×672 | 3136×1344 | 4704×2016 | 6272×2688 |

视频画幅 → 输出像素：`21:9`=1680×720，`16:9`=1280×704，`4:3`=960×720，`1:1`=720×720，`3:4`=720×960，`9:16`=720×1280。

## Procedure

1. **判断任务类型**：图像 → `agnes_image.py`；视频 → `agnes_video.py`。

2. **确认密钥可用**（首次调用做一次即可）：

   ```bash
   python "${HERMES_SKILL_DIR}/scripts/agnes_common.py"
   ```

   会打印密钥来源与脱敏后的 Key。报错就照提示补 Key（见文末「密钥与配置」），别硬试。

3. **写提示词**。图像按官方推荐结构组织：

   - 文生图：`[主体] + [场景/环境] + [风格] + [光照] + [构图] + [质量要求]`
   - 图生图：`[改变要求] + [新风格/场景] + [要加或删的元素] + [要保留的元素]`
   - 多图合成：说明每张参考图的角色 + 目标场景 + 图间关系 + 风格/光照/构图
   - 视频：写清主体动作 + 镜头运动 + 环境氛围。reference 模式用 `<Picture 1>` `<Audio 1>` 指代素材。

   提示词用中文即可。

4. **选尺寸**。横版封面 / 壁纸用 `--size 2K --ratio 16:9`（得到 2624×1472）；
   需要 1920×1080 就在下游裁切缩放 —— **不要**直接请求 `1920x1080`，
   那不是原生尺寸，会被标准化成别的档位。

5. **执行脚本**。视频任务是异步的，脚本会自己轮询到 `completed` 再下载，
   进度打到 stderr，不需要手动查。**但整段会占 2–3 分钟，调用方要留足超时。**

6. **交付**：脚本 stdout 最后一行是产物的**绝对路径**（单独占一行）。
   在回复里带上这一行，并按下一节的规则决定要不要加 `[[as_document]]`。

## 媒体交付（Hermes 专属，重要）

Hermes gateway 会从回复文本里抽取媒体路径，**默认交付成内联图片气泡**（会被有损压缩）。
如果产物是高分辨率图像或视频，压缩会明显伤质量 —— 这时在**回复末尾**加上字面指令：

```text
[[as_document]]
```

gateway 会把它剥掉，并把该回复里抽取到的**所有媒体路径改成可下载的文件附件**发出去。

规则：

- 产出了图像 / 视频文件要交给用户 → **在回复最后一行加 `[[as_document]]`**
- 只是贴个 URL 看看 → 不加，用内联预览更直观
- 远程后端（Docker / Modal）运行时**尤其重要**：产物落在后端文件系统上，
  本地和后端的 `~` 不是同一个地方，附件投递是最稳的交付方式

## Pitfalls

- **响应里不一定有服务端模型名（已实测）**。Agnes 图像接口**不返回** `model` 字段；
  视频**新建任务完成后**的响应**会返回** `model: agnes-video-2.5-flash`，
  但查询已有任务的接口不一定返回。所以凭证里「服务端自报模型=未返回」是正常现象，
  **不能**当作失败判据。判断「这次是不是 Agnes」请看**产物地址**：
  实测是 `cos-platform-outputs.agnes-ai.cn`；官方文档示例里给的是
  `storage.googleapis.com/agnes-aigc/...` —— **两者都算正常**，
  所以脚本对 host + path 一起判断 `agnes` 标识，只看 host 会误报。
- **别为了省事故意放宽守卫**。`AGNES_ALLOW_ANY_HOST=1` 是逃生门不是配置项，
  想走自建代理才开，开完记得关。
- **本地参考图必须先压到 300KB 以内（已实测）**。官方要求参考图是「可公开访问的 URL」，
  脚本会把本地文件转 Data URI 提交 —— **这条路实测走得通**，但图一大就崩：
  5 MB PNG → base64 ≈ 7 MB，创建任务的 POST 会在 60s 内
  `TimeoutError: read operation timed out`（任务没建成，**不计费**）。
  压到 768px 宽 / JPEG q88 ≈ 160 KB 后一次过，并给 `--timeout 240` 留余量：

  ```python
  Image.open(src).convert("RGB").resize((768, h*768//w)).save(dst, "JPEG", quality=88, optimize=True)
  ```

- **视频生成不能并发（已实测）**。同一时刻只能有 **1 个** 视频创建请求在飞。
  同时发 6 个 `agnes_video.py`，只有 1 个成功，其余全部
  `HTTP 429: 您已达到免费用户的 API 速率限制`。脚本自身的 2 次重试（2s / 4s）不够用，
  **必须由调用方串行 + 放大重试间隔**（≥30 秒）。批量出片请写驱动脚本按顺序调用，
  不要用并行工具调用。
- **除了 429 还有 `HTTP 503 视频队列已满`（已实测）**。这是服务端队列拥塞，与额度无关，
  同样靠退避重试解决。实测 `503 → 503 → 429` 连着来是常态，第 3 次（累计等 45+90s）
  才创建成功。调用方退避别低于 45s。
- **成品地址在顶层 `url`（官方文档与实测一致）**。完成响应里**没有 `metadata` 字段** ——
  `metadata` 只在**失败**响应里出现且为 `null`。实测地址形如
  `https://cos-platform-outputs.agnes-ai.cn/videos/.../task_xxx.mp4`。
  脚本另留了 `metadata.url` 兜底分支（历史兼容），正常不会命中；
  手写代码时认顶层 `url` 就够了。
- **别用 `internal_status` / `internal_progress` 判进度**。官方文档明示这两个是内部字段
  「不用于判断任务是否完成」，示例里固定为 `pending` / `0`，**即使任务已完成也不变**。
  只看 `status` 和 `progress`。
- **输出尺寸不是严格的 720×1280（已实测）**。`9:16` 名义上是 `720×1280`，
  但同一次任务里不同分镜会混出 **704×1280** 和 **720×1280** 两种宽度
  （音频也可能 32k / 44.1k）。
  **下游拼接前必须统一归一化**（`scale=…:force_original_aspect_ratio=increase` + `crop` +
  固定 fps/pix_fmt/音频参数），否则 `-f concat -c copy` 会直接拼坏画面。
- **轮询会被 429 限流（已实测）**。官方建议 1–2 秒查一次，实测该频率会被大量限流
  （2.5 秒仍频繁 `429 查询过于频繁`）。脚本默认间隔 **5 秒**，实测全程零限流；
  真被限流时指数退避（上限 30 秒）。4 秒视频从创建到完成约 2–2.5 分钟。
  别把 `--poll-interval` 压到 2 秒以下。
- **文生视频也会跑偏光线**。提示词写「深夜 / 暗调」，实测仍可能出**大白天**画面。
  要夜景就写「唯一光源是火光与冷月 / 没有日光」，并优先选**天然只在夜里发生的场面**
  （火光冲天、燃烧的屋脊、剪影），比堆砌「夜景」形容词有效。
  成片前**必须抽帧肉眼确认**，别只看任务 `completed`。
- **首尾帧 morph 实测可用**：`--first-frame A --last-frame B` 能生成从 A **连续变形**到 B
  的过渡画面，是做「变脸 / 幻化 / 蜕变」的唯一可靠手段，纯文生视频写 prompt 做不到。
  本地图转 Data URI 在 keyframe 模式同样可用；**两张图都要压到 ≤300KB**
  （推荐 720~768px 宽 JPEG q90），否则创建请求会超时。
  首帧还兼有「锁形象」作用 —— 想让同一角色跨镜一致，就从已满意的视频里抽一帧当首帧。
- **轮询必须带 `model_name`**。`keyframe` / `reference` 模式不带就查不到；
  只有 `text` 模式允许纯 `video_id` 查询。脚本默认带 `model_name`，404 时才回退。
- **`response_format` 放错层级**。顶层放 `response_format` 无效，
  必须进 `extra_body.response_format`。脚本已按官方写法处理，手写 curl 时别踩。
- **图生图不需要 `tags: ["img2img"]`**。官方明确说明，传了是多余的。
- **`1920x1080` / `2560x1440` 不是原生输出尺寸**，会被映射到最接近的档位
  （如 16:9 的 1K = 1312×736）。要这两个尺寸请用 `2K + 16:9` 再裁切。
- **视频 Flash 版的硬限制**：`size` 只能是 `"720P"`；`images` ≤ 5；`audios` ≤ 3；
  **不支持参考视频**。脚本已在本地前置校验，报错信息就是官方那两句。
- **超时别设太短**。图像生成可能几秒到几十秒，官方建议客户端超时 60–360 秒。
  两个脚本的单次请求超时默认都是 **300 秒**（`--timeout` 可调）。
  视频是异步的，不要靠单次请求超时去等，要靠轮询（默认最多等 900 秒）；
  给视频创建任务传大参考图时尤其要留足超时，否则会 `read operation timed out`。
- **视频任务失败不扣费，但别盲目重试**。先看 `error.message` 是不是参考媒体不合规。
- **远程后端读不到本机文件**。脚本在 Docker / Modal 里跑时，
  传给 `--image` 的本地路径是**后端**的路径。要参考本机文件，先把图传到后端可访问的位置，
  或者直接用公网 URL。
- **企业代理 / 自签证书环境**如果报 SSL 证书错误，设 `AGNES_INSECURE=1` 临时跳过校验；
  只在本机可信网络下这么用。
- **别把 API Key 写进日志、命令回显或公开仓库**。脚本只从参数 / 环境变量 / 本地配置读，
  Key 不出现在产物里；自检入口也只打印脱敏值。

## Verification

跑完这几步确认链路是通的：

```bash
# 1) 密钥来源检查（脱敏显示）
python "${HERMES_SKILL_DIR}/scripts/agnes_common.py"

# 2) 最小图像请求：应落盘一个 png，stdout 最后一行是其绝对路径
python "${HERMES_SKILL_DIR}/scripts/agnes_image.py" "一只戴墨镜的章鱼，扁平插画风" --size 1K

# 3) 最小视频请求：应打印 progress 递增直到 100%，最后落盘 mp4
python "${HERMES_SKILL_DIR}/scripts/agnes_video.py" "一只戴墨镜的章鱼在海底缓慢游动，镜头缓慢推进" --seconds 4

# 4) 对账：看调用凭证
python "${HERMES_SKILL_DIR}/scripts/agnes_common.py" --history 10

# 5) 守卫自测：下面三条都应以退出码 3 中止（打印 [agnes-guard]），且不发出任何请求
python "${HERMES_SKILL_DIR}/scripts/agnes_image.py" "test" --base-url "https://api.openai.com/v1"
python "${HERMES_SKILL_DIR}/scripts/agnes_image.py" "test" --model "dall-e-3"
python "${HERMES_SKILL_DIR}/scripts/agnes_image.py" "test" --base-url "https://agnes.evil.com/v1"
# 第 3 条专门验「伪装域名」。跑完 `echo $?` 应是 3（不是 2）—— 2 意味着守卫没拦住、请求真的发出去了
```

成功判据：

- 图像：`[agnes-image] 已保存 N KB` + 一行绝对路径
- 视频：`progress=100%` + `[agnes-video] 已保存 N MB` + 一行绝对路径
- 每次成功调用都有一行 `[agnes] 调用凭证 · …`，其中 **模型以 `agnes-` 开头**、
  **端点 host 是 `api.agnes-ai.cn`**、**产物域名是 `cos-platform-outputs.agnes-ai.cn`**
- `--history` 里的条数与实际产物数量一致

用 `ls -lh` 确认文件非 0 字节，图像能预览、视频能播放。

## 密钥与配置（Hermes）

本 skill 的 frontmatter 声明了 `required_environment_variables: AGNES_API_KEY`，
所以 **Hermes 会在本地 CLI 首次加载时引导你安全填写**，值存进 `~/.hermes/.env`，
不会暴露给模型；填好后会自动透传到 `terminal` / `execute_code` 沙箱（含 Docker、Modal 后端）。

> 在 gateway / 消息平台里加载时，Hermes **不会**在聊天中收集密钥，只提示你去本地配置 ——
> 密钥要先在本机 CLI 里配好。

解析顺序（命中即停）：

| 顺序 | 来源 | 说明 |
| --- | --- | --- |
| 1 | `--api-key sk-xxxx` | 临时单次调用 |
| 2 | 环境变量 `AGNES_API_KEY` | **Hermes 首选**，由 frontmatter 声明后自动注入 |
| 3 | 环境变量 `AGNES_AI_API_KEY` | 备选变量名 |
| 4 | `~/.agnes/config.json` 的 `api_key` | 手动放置的配置文件 |

如果脚本跑在远程后端且读不到变量，检查 `config.yaml` 里的：

```yaml
terminal:
  env_passthrough:
    - AGNES_API_KEY
```

可选配置（`~/.agnes/config.json`，模板见同目录 `config.example.json`）：

```json
{
  "api_key": "sk-xxxx",
  "base_url": "https://api.agnes-ai.cn/v1",
  "out_dir": "~/agnes-output",
  "timeout": 300,
  "image": { "model": "agnes-image-2.5-flash", "size": "2K", "ratio": "1:1" },
  "video": { "model": "agnes-video-2.5-flash", "seconds": "5", "size": "720P",
             "aspect_ratio": "16:9", "poll_interval": 5, "max_wait": 900 }
}
```

命令行参数优先级高于配置文件。

| 环境变量 | 作用 |
| --- | --- |
| `AGNES_API_KEY` / `AGNES_AI_API_KEY` | 密钥 |
| `AGNES_BASE_URL` | 覆盖 base URL |
| `AGNES_OUT_DIR` | 覆盖输出目录 |
| `AGNES_POLL_URL` | 覆盖视频轮询端点 |
| `AGNES_INSECURE` | 设为 `1` 跳过 SSL 校验（企业代理 / 自签证书场景） |
| `AGNES_LOG_FILE` | 覆盖凭证日志路径，默认 `~/.agnes/invocations.log` |
| `AGNES_TRUSTED_HOSTS` | 额外信任的 host，逗号分隔（自建代理/网关场景）。判定同样走「域后缀边界」 |
| `AGNES_ALLOW_ANY_HOST` | 设为 `1` 才彻底放开域名检查。**默认严格拦截** |

## 安装与更新

目录结构：

```
agnes-multimodal/
├── README.md              # 仓库门面（安装入口、设计要点）
├── LICENSE                # MIT
├── SKILL.md               # 本文件
├── INSTALL.md             # 安装与排障
├── config.example.json    # 配置模板
├── scripts/
│   ├── agnes_common.py    # 公共模块（密钥 / HTTP / 下载 / 守卫 / 凭证 / 自检）
│   ├── agnes_image.py     # 图像：文生图 / 图生图 / 多图合成
│   └── agnes_video.py     # 视频：文生视频 / 首尾帧 / 参考（异步轮询）
└── references/
    ├── image-api.md       # 图像接口速查（按需加载）
    └── video-api.md       # 视频接口速查（按需加载）
```

安装（详见 `INSTALL.md`）：

```bash
cp -r agnes-multimodal ~/.hermes/skills/
```

前置要求只有 Python 3.9+。**不要 pip 安装任何包** —— 脚本只用标准库。

## 维护

- 换成非 Flash 的视频模型（如 `agnes-video-2.5`）只需改 `--model`：
  那时 `--size` 可用 `720P / 1080P / 1K / 2K`，参考图上限放宽到 8 张，还支持参考视频。
- 官方接口如有变动，以两篇文档为准，同步更新 `references/` 与本文件。
