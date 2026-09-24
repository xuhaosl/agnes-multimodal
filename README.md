# agnes-multimodal

用 Agnes 的 `agnes-image-2.5-flash` 和 `agnes-video-2.5-flash` 生成图像与视频。
纯 Python 标准库，零依赖，只需要一个 API Key。

支持 Hermes Agent（原生安装或 Docker Compose 部署）与 WorkBuddy。

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

本仓库的根目录**就是这个 skill 本体**。先克隆到本地，再拷进宿主应用的 skill 目录：

```bash
git clone https://github.com/xuhaosl/agnes-multimodal.git

# Hermes Agent
cp -r agnes-multimodal ~/.hermes/skills/

# WorkBuddy
cp -r agnes-multimodal ~/.workbuddy/skills/
```

> 分类层是可选的。想归类也可以放到 `~/.hermes/skills/media/agnes-multimodal/`。

**密钥不用手动配。** skill 按顺序自己找，命中即停：

1. `--api-key` 命令行参数
2. 环境变量 `AGNES_API_KEY` / `AGNES_AI_API_KEY`
3. 配置文件（`~/.agnes/config.json` 等）
4. **宿主应用里已经配好的同一把 Key** —— Hermes 的 `config.yaml` provider、
   WorkBuddy 的 `models.json`。已经把 Agnes 接给宿主的话，这一条会直接命中，
   **不需要配第二遍**

都没有时才向你要，拿到 Key 后由 skill 自己写入并接着跑完任务。
「读取宿主配置」只读本地、只用于调 Agnes；归属判定认不出来就不取，
宁可让你补一次，也不会拿别家 provider 的 Key 去调 Agnes。

然后直接用：

```bash
hermes chat -s agnes-multimodal --oneshot -q "生成一张 2K 的赛博朋克城市封面图"
```

完整的安装步骤、密钥配置方式、Docker Compose 部署注意事项、升级与排障，
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
├── references/
│   ├── image-api.md        # 图像接口速查
│   └── video-api.md        # 视频接口速查
└── tests/
    ├── run_all.py                  # 一条命令跑齐下面 8 套，并核对项数
    ├── test_hermes_key.py          # 复用宿主已配好的同一把 Key
    ├── test_hermes_real_config.py  # 真实部署结构（序列 + key_env）与升级改名
    ├── test_readonly_hermes.py     # 只读铁律：写入被拦 + 目标文件字节零变化
    ├── test_key_bootstrap.py       # 密钥自举：检查 / 写入 / 覆盖 / 缺失引导
    ├── test_domain_guard.py        # 域名判定：域后缀边界 + 三道守卫
    ├── test_guard_bypass.py        # 守卫的绕过路径（走真实命令行）
    ├── test_py39_syntax.py         # Python 3.9 兼容性
    └── test_zero_deps.py           # 零依赖：无第三方 import（含判据自检）
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
| `4` | 被只读边界拦下（试图写入宿主应用的文件） |

**只读铁律。** 读取宿主应用的配置（Hermes 的 `config.yaml` / `.env`）时**只有只读权限**。
所有写入通道都设了闸门：把 Key 写到这些文件上会被**直接拒绝**（退出码 `4`），
日志路径指向它们时**放弃写日志并告警**（日志是追加写，落到 YAML 上比覆盖更隐蔽）。
验收标准不是「拒绝成功」，而是**目标文件字节零变化** ——
每次拒绝都做 sha256 前后比对 + 目录快照，确认没有 `.tmp` 残留。

**产物位置不设限。** 想放哪就放哪，三级覆盖：`--out-dir` > 环境变量 `AGNES_OUT_DIR` >
配置文件的 `out_dir`。默认 `~/agnes-output` 只是兜底值。
Docker 部署时记得指到挂载卷内，否则容器重建产物会丢（见 INSTALL.md）。

**调用可对账。** 每次成功调用写一行凭证到 `~/.agnes/invocations.log`，
记录时间、类型、模型、端点、状态、产物、备注 —— **不含 API Key**。
查询历史：

```bash
# <SKILL_DIR> = 你放置本 skill 的目录
python <SKILL_DIR>/scripts/agnes_common.py --history 20
```

## 已验证

分两组：前七条由**仓库内自动化测试**覆盖（合计 117 项，跑 `tests/run_all.py` 就能复现）；
后两条是**人工验证**的结论，**不在**那 117 项里。

**自动化测试覆盖**

- **守卫**：12 项 —— 走**真实命令行**验证 `--resume` / `--poll-url` / `AGNES_POLL_URL` /
  `--base-url` 四个口子，以及 `agnes.evil.com`、`api.agnes-ai.cn@evil.com` 两种伪装域名。
  退出码断言**恰好等于 `3`**（而不是「非 0」），并配两个负向对照 ——
  脚本因别的原因崩掉同样返回非 0，只断言非 0 就会假通过
- **域名判定**：29 项 —— 域后缀边界（大小写、结尾点、`notagnes-ai.cn` 这类无点边界、
  信任域出现在中间）；`guard_target` 的域名与 `agnes-` 模型名前缀；
  白名单 `AGNES_TRUSTED_HOSTS` 与整体放行开关；响应模型名、产物域名两道后续守卫
- **密钥解析**：25 项 —— 宿主 `config.yaml` 的两种写法（映射 / 序列）、
  顶层键改名、明文与变量两种存法，以及 9 项**误用防护**（配串行、只配了别家 provider、
  变量不存在等场景一律不取，宁可让你补一次）
- **只读边界**：22 项 —— 必须拒绝 8 项、必须放行 4 项（防止误伤别处同名文件）、
  读取范围未越权、日志通道降级、产物目录提醒；每次拒绝都验字节零变化
- **密钥自举**：20 项 —— 无 Key 时的结构化引导、写入与读回闭环、覆盖时保留其它字段、
  不回显完整 Key
- **兼容性**：3 层 —— 3.9 语法解析（全仓库 12 个文件）、联合类型注解必须有
  `from __future__ import annotations` 兜底（PEP 604 在语法上是合法的按位或，
  **语法层拦不住**；缺了这行在 3.9 上 import 阶段就崩）、3.10+ 专有 API 黑名单扫描
  （**只扫 `scripts/`**，那才是「脚本」）
- **零依赖**：6 项 —— `scripts/` 与 `tests/` 都不许出现第三方 import；
  另加三道防「假绿」的闸：① 扫不到文件时后面断言会**自动通过**，故单独卡一道空集检查；
  ② 判据自身的输入（标准库 / site-packages 目录清单）非空；
  ③ **判序用 9 个合成用例锁死** —— site-packages 通常就嵌在标准库目录下面，
  必须先判 site，判反了 `requests` 会被当成标准库；另把探针写进**真实** site-packages 再验一次

**人工验证（没有自动化测试）**

- **接口事实**：图像尺寸表、视频画幅像素、响应字段、默认值逐项对照官方文档原文 ——
  纯文档知识，代码里没有对应常量可供自动比对
- **端到端**：图像与视频真实调用均正常落盘 —— 需要真实 Key 与额度，不适合放进测试

合计 **117 项断言**（即上面那七条）。不用信我，仓库自带测试，零依赖（只要 Python 3.9+）：

```bash
python tests/run_all.py
```

其中「本机真实环境 check-key」1 项在未配置密钥的机器上自动跳过（116 项）；
「零依赖」里的真实探针在 site-packages 不可写时跳过（有上一条②兜底）。

## License

MIT
