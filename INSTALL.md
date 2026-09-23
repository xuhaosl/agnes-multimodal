# 安装到 Hermes Agent

本目录是一个**自包含**的 Hermes Skill，不依赖任何外部 Python 包，
也不读取宿主应用的私有配置。拷进去、配一次 Key 就能用。

## 前置要求

- Hermes Agent（任意后端：local / Docker / Modal）
- Python 3.9+（脚本只用标准库，**不需要 pip install 任何东西**）

## 一、安装

先把它克隆到本地（不习惯命令行的话，GitHub 页面上的 **Code** 按钮里可以直接下载 ZIP
解压，效果一样），再把整个文件夹拷进 Hermes 的 skill 主目录 `~/.hermes/skills/`
（分类层是**可选**的，想归类也可以放到 `~/.hermes/skills/media/agnes-multimodal/`）：

```bash
git clone https://github.com/xuhaosl/agnes-multimodal.git
cp -r agnes-multimodal ~/.hermes/skills/
```

> 在 Windows 上如果 `~/.hermes` 不存在，先确认你用的到底是 WSL2 还是原生环境 ——
> Hermes 装在哪个环境里，skill 就要放到哪个环境的 `~/.hermes/skills/` 下。

## 二、确认 Hermes 能看到它

装了之后，`~/.hermes/skills/` 下的 skill 名会**自动注册成同名斜杠命令**，
也可以用 `-s` 在启动时预加载：

```bash
# 一次性跑完就退出（要加 --oneshot：真实 TTY 下光用 -q，这句话会被当作交互会话的第一轮，
# 回答完会话仍然开着）
hermes chat -s agnes-multimodal --oneshot -q "生成一张 2K 的赛博朋克城市封面图"

# 交互式
hermes
# 进入后直接输入斜杠命令：
#   /agnes-multimodal 生成一张 2K 的赛博朋克城市封面图
# 只打 /agnes-multimodal 会加载 skill 本身，然后 agent 会问你要做什么
```

如果 `-s agnes-multimodal` 报「skill 不存在」，说明它没被扫到 —— 依次检查：

- 目录层级是否为 `~/.hermes/skills/[<分类>/]agnes-multimodal/SKILL.md`
  （`<分类>` 那层可有可无，两种层级都能被扫到）
- frontmatter 是否完整（`name` 与 `description` 是仅有的两个必填项）

## 三、配置 API Key

本 skill 的 frontmatter 里声明了 `required_environment_variables: AGNES_API_KEY`，
所以有三种方式，任选一种。

### 方式 1：让 Hermes 引导你填（推荐）

**在本地 CLI 里**首次加载本 skill 时，Hermes 会弹出安全提示让你填 `AGNES_API_KEY`，
值存进 `~/.hermes/.env`，**不会把明文暴露给模型**（也可以跳过不填，skill 仍能加载，
只是脚本跑起来会报缺密钥）。填好后自动注入 `terminal` / `execute_code` 沙箱
（含 Docker、Modal 后端），脚本直接读到。

> ⚠️ 在 **gateway / 消息平台**（微信、Telegram、Discord 等）里加载时，
> Hermes **不会**在聊天中收集密钥，只会提示你去本地配置 —— 这是官方的安全设计。
> 所以密钥要**先在本机 CLI 里配好**，消息端才用得上。

### 方式 2：手动写进 `~/.hermes/.env`

```bash
echo 'AGNES_API_KEY=sk-你的key' >> ~/.hermes/.env
```

如果脚本跑在远程后端（Docker / Modal）却读不到变量，检查 `config.yaml`：

```yaml
terminal:
  env_passthrough:
    - AGNES_API_KEY
```

### 方式 3：用配置文件

```bash
mkdir -p ~/.agnes
cp config.example.json ~/.agnes/config.json
# 然后编辑 ~/.agnes/config.json，把 api_key 填上
```

远程后端跑的话，这个文件也要在**后端可见**的位置。

Key 从 https://www.agnes-ai.cn 控制台获取，形如 `sk-xxxx`。

## 四、密钥解析顺序

命中即停：

| 顺序 | 来源 | 适用场景 |
| --- | --- | --- |
| 1 | `--api-key sk-xxxx` | 临时单次调用 |
| 2 | 环境变量 `AGNES_API_KEY` | **Hermes 首选** |
| 3 | 环境变量 `AGNES_AI_API_KEY` | 备选变量名 |
| 4 | `~/.agnes/config.json` 的 `api_key` | 需要跨环境共享配置时 |

## 五、验证安装

`${HERMES_SKILL_DIR}` 由 Hermes 自动替换成 skill 的绝对路径。手动在终端跑时，
把它换成实际路径（如 `~/.hermes/skills/agnes-multimodal`）。

```bash
# 0) 自检：密钥来源（脱敏）、Base URL、轮询端点、当前生效模型、输出目录
python ~/.hermes/skills/agnes-multimodal/scripts/agnes_common.py

# 1) 图像最小请求（几秒到几十秒）
python ~/.hermes/skills/agnes-multimodal/scripts/agnes_image.py \
  "一只戴墨镜的章鱼，扁平插画风" --size 1K

# 2) 视频最小请求（异步，自动轮询，约 2-2.5 分钟）
python ~/.hermes/skills/agnes-multimodal/scripts/agnes_video.py \
  "一只戴墨镜的章鱼在海底缓慢游动，镜头缓慢推进" --seconds 4
```

成功判据：

- 图像：`[agnes-image] 已保存 N KB`，stdout 最后一行是**绝对路径**
- 视频：`progress=100%` + `[agnes-video] 已保存 N MB`，最后一行是**绝对路径**
- 每次成功调用都有一行 `[agnes] 调用凭证 · …`

想在聊天里验证媒体交付，可以发一句：

```
/agnes-multimodal 生成一张 1K 测试图
```

然后看产物是作为**图片气泡**还是**文件附件**发回来 —— 这取决于回复里有没有
`[[as_document]]` 指令（见 SKILL.md「媒体交付」一节）。

## 六、远程后端注意事项

跑在 Docker / Modal 上时：

- **产物路径是后端的路径**，不是你本机的。所以交付媒体时应该在回复里加
  `[[as_document]]`，让 gateway 以附件形式投递，而不是甩一个后端路径给用户。
- **`--image` / `--first-frame` 传的本地路径也是后端路径**。要参考本机文件，
  先把图传到后端可访问的位置，或者直接用公网 URL。
- **`~` 在容器里是容器的 home**，不是你在 NAS / WSL2 上的 home。
  需要固定输出位置就用 `--out-dir` 显式指定。

## 七、升级

直接覆盖脚本与 SKILL.md 即可，`~/.agnes/invocations.log` 和 `~/.hermes/.env`
不受影响。当初用 `git clone` 拿的，先在仓库目录里 `git pull` 取到最新版；
当初下 ZIP 的，重新下载解压即可：

```bash
cp -r agnes-multimodal ~/.hermes/skills/
```

## 八、卸载

```bash
rm -rf ~/.hermes/skills/agnes-multimodal
# 可选：清理调用凭证与配置
rm -rf ~/.agnes
```

## 九、排障

| 现象 | 原因与处理 |
| --- | --- |
| `没有找到 Agnes API Key` | 检查 `~/.hermes/.env` 里有没有 `AGNES_API_KEY`；远程后端还要确认 `env_passthrough` |
| `目标地址不是 Agnes 域名，已中止` | 守卫正常工作（退出码 **3** + `[agnes-guard]`）。要走自建代理请设 `AGNES_TRUSTED_HOSTS=<你的域名>`，别一上来就 `AGNES_ALLOW_ANY_HOST=1` |
| 注意到退出码 `2` 但以为是守卫拦的 | `2` 是请求 / 网络 / API 类失败（**请求已经发出去了**）；被守卫拦下是 `3`。两个码分开正是为了别把这两种搞混 |
| `模型 ID 不是 Agnes 模型` | 传了 `--model` 且不是 agnes-* 系列。去掉该参数即可 |
| `HTTP 429 您已达到免费用户的 API 速率限制` | 视频创建**不能并发**，且重试退避要 ≥30 秒 |
| `HTTP 503 视频队列已满` | 服务端队列拥塞，退避 45 秒以上重试，与额度无关 |
| 创建任务 `TimeoutError` | 参考图太大。压到 300KB 以内（768px 宽 JPEG q88） |
| SSL 证书错误 | 企业代理 / 自签证书环境，设 `AGNES_INSECURE=1`（仅可信网络） |
| 产物在聊天里是压缩过的图 | 回复末尾加 `[[as_document]]` 改为附件投递 |
| 中文乱码 / UnicodeEncodeError | 理论上不会：脚本已把 stdout/stderr 强制为 UTF-8。若出现请反馈 |
