# agnes-multimodal

用 Agnes 的 `agnes-image-2.5-flash` 和 `agnes-video-2.5-flash` 生成图像与视频。
纯 Python 标准库，零依赖，只需要一个 API Key。

适用于 Hermes Agent。

## 能做什么

| 能力 | 说明 |
| --- | --- |
| 文生图 | 一句提示词直接出图，支持 1K / 2K / 3K / 4K 与 8 种画幅比例 |
| 图生图 | 给参考图，改写风格或内容 |
| 多图合成 | 把多张图的元素合成到一张新图里 |
| 文生视频 | 文本出视频，可指定秒数与画幅 |
| 首尾帧 | 指定起始帧与结束帧，生成过渡动画 |
| 图片 / 音频参考 | 视频以图片为参考、以音频驱动 |

## 快速开始

本仓库的根目录**就是这个 skill 本体**。先克隆到本地，再拷进 Hermes 的 skill 目录：

```bash
git clone https://github.com/xuhaosl/agnes-multimodal.git
cp -r agnes-multimodal ~/.hermes/skills/
```

> 分类层是可选的。想归类也可以放到 `~/.hermes/skills/media/agnes-multimodal/`。

首次加载时 Hermes 会引导你填 API Key（存进 `~/.hermes/.env`），然后直接用：

```bash
hermes chat -s agnes-multimodal --oneshot -q "生成一张 2K 的赛博朋克城市封面图"
```

完整的安装步骤、三种密钥配置方式、远程后端注意事项、升级与排障，
见 [INSTALL.md](INSTALL.md)。

## 包结构

```
agnes-multimodal/
├── README.md               ← 你在看的东西
├── LICENSE                 ← MIT
├── SKILL.md                ← 主文件：完整用法、参数速查、Pitfalls、验证步骤
├── INSTALL.md              ← 安装、配置、升级、卸载、排障
├── config.example.json
├── scripts/
│   ├── agnes_common.py     # 公共模块：密钥 / HTTP / 下载 / 守卫 / 凭证 / 自检
│   ├── agnes_image.py      # 图像：文生图 / 图生图 / 多图合成
│   └── agnes_video.py      # 视频：文生视频 / 首尾帧 / 图片·音频参考（异步轮询）
└── references/
    ├── image-api.md        # 图像接口速查
    └── video-api.md        # 视频接口速查
```

前置要求只有 Python 3.9+，**不需要 pip 装任何包**。

## 设计要点

**模型硬锁。** 图像与视频的模型名写死在脚本常量里，与宿主应用的模型设置完全脱钩。
即使外部配置被改动，请求也不会落到别的模型上。

**三道守卫。** 防的是「调用跑偏」和「密钥外发」：

| 守卫 | 时机 | 行为 |
| --- | --- | --- |
| `guard_target()` | 请求发出前 | 目标域名或模型名不含 agnes 时中止，**请求不发出** |
| `verify_response_model()` | 响应返回后 | 校验服务端自报的模型名 |
| `verify_artifact_host()` | 下载产物前 | 校验产物地址的域名 |

域名判定用的是**域后缀边界**（只认 `agnes-ai.cn` 及其子域），不是「字符串里含 agnes」——
所以 `agnes.evil.com` 这类只蹭了子域名的伪装域名拦得住。模型名必须 `agnes-` 开头。
自建代理场景可用 `AGNES_TRUSTED_HOSTS` 追加信任域，不必关掉整个守卫。

**退出码可区分失败类型：**

| 退出码 | 含义 |
| --- | --- |
| `0` | 成功 |
| `2` | 请求 / 网络 / API 类失败（**请求已发出**） |
| `3` | 被守卫在请求发出前拦下 |

**调用可对账。** 每次成功调用写一行凭证到 `~/.agnes/invocations.log`，
记录时间、类型、模型、端点、状态、产物、备注 —— **不含 API Key**。
查询历史：

```bash
python ~/.hermes/skills/agnes-multimodal/scripts/agnes_common.py --history 20
```

## 已验证

- **接口事实**：图像尺寸表、视频画幅像素、响应字段、默认值逐项对照 Agnes 官方文档原文
- **守卫**：覆盖 `--resume` / `--poll-url` 等绕过路径，以及 `agnes.evil.com`、
  `api.agnes-ai.cn@evil.com` 两种伪装域名
- **域名判定**：单元测试覆盖大小写、结尾点、子域混淆
- **兼容性**：全部脚本通过 Python 3.9 语法校验
- **端到端**：图像与视频真实调用均正常落盘

## License

MIT
