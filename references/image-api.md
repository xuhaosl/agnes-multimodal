# Agnes Image 2.5 Flash — 接口速查

来源：https://www.agnes-ai.cn/zh-Hans/docs/agnes-image-25-flash

## 基本信息

- 模型名：`agnes-image-2.5-flash`
- Endpoint：`POST https://api.agnes-ai.cn/v1/images/generations`
- 请求头：`Authorization: Bearer <API_KEY>`、`Content-Type: application/json`
- 价格：所有输出分辨率档位与输入参考图**当前免费**
- 能力：文生图、图生图、多图合成
- 同步接口（无异步任务），结果以 URL 或 Base64 返回

## 请求参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `model` | string | 是 | `agnes-image-2.5-flash` |
| `prompt` | string | 是 | 生成或编辑的文本指令 |
| `size` | string | 是 | 档位 `1K`/`2K`/`3K`/`4K`；也兼容 `1024x768` 等历史精确写法，不支持的会被标准化 |
| `ratio` | string | 否 | `1:1`(默认) `3:4` `4:3` `16:9` `9:16` `2:3` `3:2` `21:9` |
| `image` | string[] | 图生图必填 | 输入图像数组，公网 URL 或 Data URI Base64；多图合成传多张 |
| `return_base64` | boolean | 否 | **文生图**要 Base64 返回时用 |
| `extra_body` | object | 否 | 高级工作流附加参数 |
| `extra_body.response_format` | string | 否 | `url` 或 `b64_json` |

⚠️ 图生图/多图合成的输入图**要放在 `extra_body.image`**（文档在「重要说明」和技术示例里混用了 `image` 与 `extra_body.image`；
示例代码统一用 `extra_body.image`，本 skill 按示例实现）。

## 输出尺寸对照表

| ratio | 1K | 2K | 3K | 4K |
| --- | --- | --- | --- | --- |
| 1:1 | 1024x1024 | 2048x2048 | 3072x3072 | 4096x4096 |
| 3:4 | 864x1152 | 1728x2304 | 2592x3456 | 3456x4608 |
| 4:3 | 1152x864 | 2304x1728 | 3456x2592 | 4608x3456 |
| 16:9 | 1312x736 | 2624x1472 | 3936x2208 | 5248x2944 |
| 9:16 | 736x1312 | 1472x2624 | 2208x3936 | 2944x5248 |
| 2:3 | 832x1248 | 1664x2496 | 2496x3744 | 3328x4992 |
| 3:2 | 1248x832 | 2496x1664 | 3744x2496 | 4992x3328 |
| 21:9 | 1568x672 | 3136x1344 | 4704x2016 | 6272x2688 |

`1920x1080` 与 `2560x1440` **不是原生输出尺寸**，会被标准化（例如映射到 16:9 的 1K = `1312x736`）。
要这两种尺寸：请求 `size: "2K"` + `ratio: "16:9"`，再在下游裁剪缩放。

## 响应格式

URL 输出：

```json
{
  "created": 1780000000,
  "data": [
    { "url": "https://storage.googleapis.com/agnes-aigc/xxx.png", "b64_json": null, "revised_prompt": null }
  ]
}
```

Base64 输出：

```json
{
  "created": 1780000000,
  "data": [
    { "url": null, "b64_json": "iVBORw0KGgoAAAANSUhEUgAA...", "revised_prompt": null }
  ]
}
```

取值路径：`data[0].url` / `data[0].b64_json`。

## 官方点名过的坑

1. **`response_format` 不能放顶层**，必须进 `extra_body.response_format`。
   顶层写法会被忽略。
2. **图生图不要传 `tags: ["img2img"]`**，不需要。
3. **输入图必须公网可访问**：公共 HTTPS、无需登录/cookie/私有请求头；
   否则改用 Data URI Base64（`data:image/png;base64,BASE64_HERE`）。
4. **图生图/多图合成缺 `extra_body.image` 会失败**，是必填项。
5. **超时**：生成可能几秒到几十秒，官方建议客户端超时设 `60s–360s`。

## 提示词结构（官方推荐）

- 文生图：`[主体] + [场景/环境] + [风格] + [光照] + [构图] + [质量要求]`
- 图生图：`[改变要求] + [新风格/场景] + [要添加或移除的元素] + [要保留的元素]`
- 多图合成：`[参考图角色] + [目标场景] + [图像之间的关系] + [风格/光照/构图]`
- 高信息密度：讲清视觉层次——主要主体、背景环境、重要次要细节、风格、光照、构图约束

官方示例（多图合成）：
「将第一张图作为主要角色，第二张图作为产品参考，生成一张电影级活动海报，保留角色身份和产品外形，使用自然光照和干净的商业构图。」

## 定价

按刊例价：1K ¥0.07/张、2K ¥0.12/张、3K ¥0.14/张、4K ¥0.16/张；第 4 张起的输入参考图 ¥0.02/张
（前 3 张输入参考图不额外收费）。**当前现价全部为 ¥0。**
