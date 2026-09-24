# 安装：WorkBuddy 与 Hermes Agent

这个 skill 目录是**自包含、跨平台**的：一份代码同时用于 WorkBuddy 和 Hermes Agent，
区别只在「放到哪个目录」和「密钥怎么给」。

```
agnes-multimodal/
├── SKILL.md               # 主文件，两边都读它
├── INSTALL.md             # 本文件
├── config.example.json    # 配置模板
├── scripts/
│   ├── agnes_common.py    # 公共模块（密钥解析 / HTTP / 下载 / 自检入口）
│   ├── agnes_image.py     # 图像：文生图 / 图生图 / 多图合成
│   └── agnes_video.py     # 视频：文生视频 / 首尾帧 / 参考（异步轮询）
└── references/
    ├── image-api.md       # 图像接口速查（按需加载）
    └── video-api.md       # 视频接口速查（按需加载）
```

前置要求只有 Python 3.9+（脚本只用标准库，无需 pip 安装任何包）。
实测语法在 **Python 3.7+ 都能解析**，写 3.9+ 是留余量。

**标注约定**：✅ 实测（本机实测过或查过官方文档原文）／⚠️ 未验证（需你自行确认）／
💡 建议（判断或推荐，不是硬事实）。

---

## 一、装到 WorkBuddy

WorkBuddy 的 skill 目录是**扁平**的，直接放一级子目录：

```bash
mkdir -p ~/.workbuddy/skills
cp -r agnes-multimodal ~/.workbuddy/skills/
```

装完确认：

```bash
python ~/.workbuddy/skills/agnes-multimodal/scripts/agnes_common.py
```

### WorkBuddy 上的密钥

**不用配。** 只要你在 WorkBuddy 里已经接入了 `agnes-2.5-flash` 文本模型
（密钥存在 `~/.workbuddy/models.json`），脚本会自动复用 —— 那台机器上是零配置。

如果那台机器上没接过 Agnes，**不需要你自己改文件** —— 跑一次任务，agent 会问你要 Key
并帮你写入（见「三、通用密钥配置」的自举流程）。

另外可选环境变量：`AGNES_CONFIG_PATH`（密钥写入位置）、`AGNES_OUT_DIR`（产物目录）
等，完整清单见 `SKILL.md` 的变量表。

### 关于 Python 解释器

WorkBuddy 自带的托管 Python 在 `~/.workbuddy/binaries/python/versions/<版本>/python.exe`。
`python` 命令不可用时，直接用托管解释器的绝对路径跑脚本即可，无需改代码。

---

## 二、装到 Hermes Agent

### 2.1 先确定部署形态：原生安装还是 Docker Compose

这一步决定「文件放哪」「密钥写哪」「产物会不会丢」，**先做这一步，别跳**。

```bash
# 有没有 hermes 容器在跑
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' | grep -i hermes

# 有容器 → 看挂载配置（重点看容器的 /opt/data 映射到宿主机哪里）
docker inspect <容器名> --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'

# 没容器 → 看是不是原生安装
which hermes && hermes --version
```

| 情形 | 判断依据 | skill 放哪 |
| --- | --- | --- |
| **Docker Compose** | `docker ps` 里有 hermes 容器 | 宿主机上 compose 映射给 `/opt/data` 的那个目录的 `skills/` 下 |
| **原生安装** | `which hermes` 有输出 | 宿主机 `~/.hermes/skills/` |

✅ **实测依据**：Hermes 官方 Docker Compose 的挂载是 `~/.hermes:/opt/data` ——
**容器的数据目录 `/opt/data` 就是宿主机的 `~/.hermes`**。
所以放宿主机就等于放容器内，**不需要 `docker cp`**。

⚠️ **要你确认的**：上面 `docker inspect` 的输出请自己看一眼。若你的 compose 写的是
`./data:/opt/data` 这类相对路径，宿主机目录就是 compose 文件同级的 `data/`，
skill 相应地要放 `data/skills/`。

```bash
# 顺手记下容器里的 Python 版本与 home 目录（2.5 节要用）
docker exec <容器名> python3 --version
docker exec <容器名> python3 -c "from pathlib import Path; print(Path.home())"
```

⚠️ 容器里若没有 `python3` 这个命令，换成 `python` 再试。

### 2.2 把 skill 文件放到宿主机

skill 的目录层级必须是：

```
<宿主机数据目录>/skills/[<分类>/]agnes-multimodal/SKILL.md
```

`<分类>` 那层可有可无 —— ✅ **实测依据**：Hermes 官方原文「The category folder is
optional too — a skill can sit directly under `~/.hermes/skills/`」。

**原生安装**直接放 `~/.hermes/skills/`：

```bash
mkdir -p ~/.hermes/skills/media
cp -r agnes-multimodal ~/.hermes/skills/media/
```

**Docker Compose 部署**：放进宿主机那个映射目录（即 2.1 查到的 `/opt/data` 对应的宿主目录），
效果等同于放进容器。若你习惯在服务器上直接操作，也可以 `git clone` 到该目录：

```bash
cd <宿主机数据目录>/skills        # 例如 ~/.hermes/skills
git clone https://github.com/xuhaosl/agnes-multimodal.git
```

容器 / 服务器上若没有 `git`，或连 GitHub 不稳（国内常见），就下载 ZIP 再传上去：

```
https://github.com/xuhaosl/agnes-multimodal/archive/refs/heads/main.zip
```

解压后目录名是 `agnes-multimodal-main`，**改成 `agnes-multimodal`**，再用你惯用的方式
（scp / SMB / 网盘 / 面板文件管理）传到宿主机的 `<数据目录>/skills/` 下。
`.hermes` 若是隐藏目录，先打开「显示隐藏文件」。

**Docker 情形再确认一遍容器里也看得到：**

```bash
docker exec <容器名> ls /opt/data/skills/agnes-multimodal/
```

能看到 `SKILL.md` `scripts` 就对了。

### 2.3 确认它能被扫到

装了之后 skill 名会**自动注册成斜杠命令**，也可以用 `-s` 在启动时预加载：

```bash
# 一次性跑完就退出（要加 --oneshot：真实 TTY 下光用 -q，这句话会被当作交互会话第一轮，
# 回答完会话仍然开着）
hermes chat -s agnes-multimodal --oneshot -q "生成一张 2K 的赛博朋克城市封面图"

# 交互式
hermes
# 进入后直接输入：/agnes-multimodal 生成一张 2K 的赛博朋克城市封面图
```

Docker 部署下要先进容器：`docker exec -it <容器名> hermes chat -s agnes-multimodal ...`

如果 `-s agnes-multimodal` 报「skill 不存在」，依次检查：

- 目录层级是否为 `<数据目录>/skills/[<分类>/]agnes-multimodal/SKILL.md`
- frontmatter 是否完整（`name` 与 `description` 是仅有的两个必填项）
- Docker 情形：`docker exec <容器名> ls /opt/data/skills/agnes-multimodal/` 是否看得到

### 2.4 Hermes 上的密钥

**先看有没有现成的。** 如果你已经把 Agnes 接给了 Hermes（`~/.hermes/config.yaml` 里有
agnes 的 provider），脚本会**直接复用同一把 Key** —— 不需要，也不应该让你再配第二遍：

```bash
python scripts/agnes_common.py --check-key
# 命中时的输出形如：
#   来源=Hermes 配置 ~/.hermes/config.yaml 的 custom_providers「Agnes」
#        （依据：base_url 指向 agnes-ai.cn、模型列表含 agnes-* 条目）
```

实测的 Hermes 结构（2026-09 版本，顶层是**序列**不是映射）：

```yaml
custom_providers:
- name: Agnes                                    # ← 名字由用户自定，脚本不作判据
  base_url: https://api.agnes-ai.cn/v1
  key_env: HERMES_CUSTOM_API_AGNES_AI_CN_API_KEY  # 变量值放 ~/.hermes/.env
  model: agnes-2.5-flash
  api_mode: chat_completions
  models:
    agnes-image-2.5-flash: {}
    agnes-video-2.5-flash: {}
  models_discovered: true
```

脚本**不写死顶层键名**（`providers`、`custom_providers`、以及将来升级改成的别的名字都吃），
序列与映射两种写法都认，所以 Hermes 升级改名也不影响。下表是你可能担心的变化，
全都**不影响**命中：

| 变化 | 是否影响 |
| --- | --- |
| provider 的 `name` 改成任意自定义名字（含中文） | 不影响（`name` 从不单独成立） |
| Hermes 升级后顶层键改名（`custom_providers` → 别的） | 不影响（脚本遍历所有顶层块） |
| 写法从序列变成映射 | 不影响（两种都吃） |
| Key 从 `key_env` 改成 `api_key` 明文 | 不影响（两种都认） |

判定归属靠内容自证：**写了 `base_url` 就必须指向 `agnes-ai.cn`**，否则一律不取 ——
「Key 配串行」很常见，取出来就是拿别家凭据去打 Agnes。
**唯一会拒绝的情况**就是这个。

**没配过才会走「密钥自举」**：发现没配 → 问你要 Key → 写入 → 重跑任务。
你只管把 Key 发过去就行。

```bash
python scripts/agnes_common.py --check-key             # 只检查：有没有配、来源、脱敏指纹
python scripts/agnes_common.py --set-key sk-xxxx       # 写入（agent 用这条）
```

写入位置由脚本自己判断 —— Docker 部署下自动落到挂载卷 `/opt/data/agnes/config.json`，
容器重建不丢。想固定到别处：设 `AGNES_CONFIG_PATH`，或用 `--config-path <路径>`。

> **只读铁律：你的 Hermes 配置，技能一个字都不会改。**
> 这条是硬保证，不是承诺 —— 技能里加了闸门，并有 22 项自动化测试盯着：

| 通道 | 行为 |
| --- | --- |
| 读 `config.yaml` / `.env` 取 Key | 只读，**没有任何写操作** |
| `--set-key` 若指向 `config.yaml` / `.env` / `settings.yaml` | **拒绝**（退出码 4），并提示换路径 |
| 日志路径 `AGNES_LOG_FILE` 指向这些文件 | **放弃写日志** + 告警（日志是追加写，落到 YAML 上会把结构毁掉，比覆盖更隐蔽） |
| 产物 `--out-dir` | 只会新建目录，不会覆盖你的文件 |

拦下来时**连同目录里的 `.tmp` 都不会落下** —— 原子写入会在目标旁边建临时文件，
所以闸门放在动手之前，而不是「拦覆盖」。

另外，脚本只读 Hermes 的 `config.yaml` 和 `.env` 这两个文件，**不碰**会话记录、
数据库等其它文件（有测试断言这两个读点是唯一入口）。
给 skill 配 Key 请用独立路径（默认就是 `~/.agnes/config.json`，Docker 下是
`/opt/data/agnes/config.json`，是挂载卷里的**自有子目录**，和 Hermes 的文件互不干扰）。
要改 Hermes 自己的配置，请用 `hermes setup` 或手动编辑。

**如果想让 Key 完全不经过对话**（值不进模型上下文），可以改用 Hermes 自带的引导机制：
本 skill 的 frontmatter 声明了 `required_environment_variables: AGNES_API_KEY`，
首次 `skill_view` 加载时会弹安全提示让你填，值存进 `~/.hermes/.env`，不暴露给模型。

这一步**不能省，原因很硬**：Hermes 的子进程默认跑在最小环境里，官方原文写着 ——
变量名里含 `KEY`、`TOKEN`、`SECRET`、`PASSWORD`、`CREDENTIAL`、`PASSWD`、`AUTH`
的环境变量**一律被剥离**，只放行 `PATH`、`HOME`、`LANG` 这类安全系统变量。
`AGNES_API_KEY` 名字里带 `KEY`，**skill 不声明就会被 Hermes 挡在门外**。
声明之后，Hermes 才会把它透传进 `terminal` 和 `execute_code` 子进程。

也可以直接手写这个文件（Key 不进模型上下文，也不依赖交互提示）：

```bash
mkdir -p ~/.hermes
printf 'AGNES_API_KEY=sk-你的真实key\n' >> ~/.hermes/.env

# 立刻回读确认（只看前 10 位，别把整串打到屏幕上）
grep -o 'AGNES_API_KEY=sk-.\{0,6\}' ~/.hermes/.env

# 收一下权限，别让同机器其他账号读到
chmod 600 ~/.hermes/.env
```

⚠️ 注意 `>>` 是**追加**。如果你之前已经写过、要改值，用编辑器改那行，别反复追加
（环境变量文件里出现两条同名项时，行为取决于解析实现，容易踩坑）。查重复：

```bash
grep -c '^AGNES_API_KEY=' ~/.hermes/.env     # 应该是 1
```

Docker 情形还要确认 `.env` 真的被容器读到了：

```bash
docker exec <容器名> sh -c "grep -o 'AGNES_API_KEY=sk-.\{0,6\}' /opt/data/.env"
```

若你的 compose 没把整个数据目录挂上去，`.env` 可能不在 `/opt/data/.env`，
用 2.1 的 `docker inspect` 输出对照一下就知道。

如果脚本跑在远程后端（Docker / Modal），确认这个变量在 `config.yaml` 的
`terminal.env_passthrough` 名单里 —— **但本 skill 已声明，正常不需要这条**，
只有在你删改了 frontmatter 时才用得上。

**注意优先级**：环境变量 > 配置文件。若 `AGNES_API_KEY` 已存在于环境中，
`--set-key` 写入的配置**不会生效**，脚本会明确告警，此时要改的是环境变量。

### 2.5 Docker Compose 专属：三处「会丢」的地方

这是 Docker 部署最容易踩的坑：**脚本默认输出目录是 `~/agnes-output`，
而容器里的 `~` 是容器的 home（通常是 `/root`），不是你的宿主机目录**。
容器一重建，产物和日志就没了 —— 而且在宿主机的文件管理里也找不到它们。

✅ **实测依据**：脚本里 `DEFAULT_OUT_DIR = "~/agnes-output"`、
`LOG_PATH = "~/.agnes/invocations.log"`，两者都基于 `Path.home()`。

**解决办法**：既然容器里 `/opt/data` = 宿主机数据目录，就把这两个路径都指过去。

写进 `config.json`：

```json
{
  "api_key": "sk-你的key",
  "out_dir": "/opt/data/agnes-output"
}
```

日志路径用环境变量覆盖（`AGNES_LOG_FILE` 优先级高于默认值），加进 `.env`：

```
AGNES_LOG_FILE=/opt/data/.agnes/invocations.log
```

这样：

| 内容 | 容器内路径 | 宿主机的可见位置 |
| --- | --- | --- |
| 生成的图片 / 视频 | `/opt/data/agnes-output/` | `<数据目录>/agnes-output/` |
| 调用凭证日志 | `/opt/data/.agnes/invocations.log` | `<数据目录>/.agnes/invocations.log` |

**原生安装不用改**：`~` 就是你的真实家目录，默认值可以直接用。

> 顺带一个通用技巧：只想偶尔换个输出位置，不用改配置，命令里加
> `--out-dir /opt/data/agnes-output` 就行（命令行参数优先级最高）。

配置文件也可以放 **skill 根目录下的 `config.json`**（是候选路径之一，且
`.gitignore` 已排除它，不会被误提交）：

```bash
cd <数据目录>/skills/agnes-multimodal
cp config.example.json config.json
vi config.json        # 填 api_key
chmod 600 config.json
```

**容器重建前必读** —— 确认这三样都在挂载卷里，否则会丢：

| 内容 | 安全位置 |
| --- | --- |
| API Key | `~/.hermes/.env`（容器内 `/opt/data/.env`） |
| 配置文件（若用上面的方式） | `<数据目录>/skills/agnes-multimodal/config.json` |
| 产物与日志（若按本节配了） | `<数据目录>/agnes-output/`、`<数据目录>/.agnes/` |

判断原则就一条：**凡是你希望留下来的东西，都得在 `/opt/data` 底下**
（即宿主机的数据目录）。放在 `/root`、`/tmp` 里的东西，容器一删就没了。

---

## 三、通用密钥配置（两平台通用）

密钥解析顺序，命中即停：

| 顺序 | 来源 | 适用 |
| --- | --- | --- |
| 1 | `--api-key sk-xxxx` | 临时单次调用 |
| 2 | 环境变量 `AGNES_API_KEY` | Hermes 引导填写的落地位置 |
| 3 | 环境变量 `AGNES_AI_API_KEY` | 备选变量名 |
| 4 | `AGNES_CONFIG_PATH` 指向的配置文件 | `--set-key` 自定义写入点 |
| 5 | `~/.agnes/config.json` 的 `api_key` | 两平台共用，`--set-key` 默认写入点 |
| 6 | `~/.config/agnes/config.json` | 备选位置 |
| 7 | `<skill 目录>/config.json` | 跟 skill 一起带走 |
| 8 | `/opt/data/agnes/config.json` | **Hermes Docker 挂载卷自动命中** |
| 9 | **`~/.hermes/config.yaml` 里的 agnes provider** | **Hermes 已配过 Agnes 时自动命中** |
| 10 | **`~/.hermes/.env` 里的 `AGNES_API_KEY` 等** | 同上，变量名自证 |
| 11 | `~/.workbuddy/models.json` 里的 agnes 条目 | **WorkBuddy 自动命中** |

第 9、10 条是「**复用你已经配好的那把 Key**」。Hermes 场景下基本都会直接命中第 9 条，
你不需要为 skill 再做任何配置。现场结构、写法兼容性与归属判定规则见 2.4 节。
**认不出来就不取**，绝不拿别家 provider 的 Key 去调 Agnes。
这两个来源只读本机文件、只用于调 Agnes，不会外传。

✅ **实测**：用假密钥跑过 6 组对照实验，四条基础来源的优先级与上表完全一致 ——
主变量胜过备选名、环境变量胜过配置文件、都空时如实报「未找到」。

写和读用的是同一份候选清单 —— 凡是 `--set-key` 会写进去的位置，脚本都能读回来。

其它可选环境变量：`AGNES_BASE_URL`、`AGNES_OUT_DIR`、`AGNES_POLL_URL`、`AGNES_INSECURE`、
`AGNES_LOG_FILE`（覆盖凭证日志路径）、`AGNES_TRUSTED_HOSTS`（额外信任的 host，逗号分隔，
自建代理 / 网关场景用）、`AGNES_ALLOW_ANY_HOST`（设为 `1` 才彻底放开域名检查，默认严格拦截）。

域名守卫判定走「**域后缀边界**」，不是「含 agnes 字样」：只认 `agnes-ai.cn` 及其子域，
`agnes.evil.com`（注册域是 `evil.com`）和 `https://api.agnes-ai.cn@evil.com` 这类写法
都会被拦下 —— 否则 API Key 会被发到别人的服务器上。

守卫拦截时**退出码是 3** 并打印 `[agnes-guard]`；请求 / 网络 / API 类失败才是 `2`。
两个码分开，自测才能区分「守卫真的生效」和「请求发出去了但失败」。

`AGNES_INSECURE=1` 用于企业代理 / 自签证书环境（跳过 SSL 校验），**只在本机可信网络下临时使用**。

**密钥不会被写进日志**：凭证日志 `~/.agnes/invocations.log` 每列是
`time / kind / model / endpoint / status / artifact / note`，**不含 Key**；
自检输出打印的是掩码形态（前 6 位 + 后 4 位）；日志里的路径会把家目录缩写成 `~`，
不暴露用户名。

---

## 四、验证安装

按顺序跑，**每一步都有明确的成功判据**。

```bash
# 0) 自检：打印密钥来源（脱敏）、Base URL、轮询端点、输出目录、Python 版本（不消耗额度）
python <SKILL_DIR>/scripts/agnes_common.py

# 0.5) 只查密钥：有没有配 / 来源 / 脱敏指纹（不改动任何文件，退出码 1 = 没配）
python <SKILL_DIR>/scripts/agnes_common.py --check-key

# 1) 图像最小请求（消耗少量额度）
python <SKILL_DIR>/scripts/agnes_image.py "一只戴墨镜的章鱼，扁平插画风" --size 1K

# 2) 视频最小请求（异步，约 2-2.5 分钟，会自动轮询到完成）
python <SKILL_DIR>/scripts/agnes_video.py "一只戴墨镜的章鱼在海底缓慢游动，镜头缓慢推进" --seconds 4
```

`<SKILL_DIR>` 是 skill 所在绝对目录：

- WorkBuddy：`~/.workbuddy/skills/agnes-multimodal`
- Hermes 原生：`~/.hermes/skills/media/agnes-multimodal`
- Hermes 里也可以直接用内置变量：`${HERMES_SKILL_DIR}/scripts/agnes_image.py`
- **Docker 部署**：在容器内跑，路径是 `/opt/data/skills/agnes-multimodal`

```bash
docker exec <容器名> python3 /opt/data/skills/agnes-multimodal/scripts/agnes_common.py
```

自检的期望输出：

```
Agnes 多模态 skill 自检
----------------------------------------------
密钥来源 : 环境变量 AGNES_API_KEY
密钥     : sk-abcd...wxyz
Base URL : https://api.agnes-ai.cn/v1
轮询端点 : https://api.agnes-ai.cn/agnesapi
图像模型 : agnes-image-2.5-flash（脚本内写死）
视频模型 : agnes-video-2.5-flash（脚本内写死）
输出目录 : /opt/data/agnes-output
Python   : 3.x.x (/usr/bin/python3)
凭证日志 : /opt/data/.agnes/invocations.log
累计调用 : 还没有记录，跑一次图像或视频脚本后这里会有凭证
----------------------------------------------
密钥可用。可以跑 agnes_image.py / agnes_video.py 了。
```

**逐项核对这 5 行**：

| 行 | 看什么 | 不对怎么办 |
| --- | --- | --- |
| `密钥来源` | 不能是「未找到」 | 见第六节排障第 1 条 |
| `Base URL` | 必须是 `https://api.agnes-ai.cn/v1` | 被配置覆盖了，检查 `config.json` |
| `图像 / 视频模型` | 必须是 `agnes-*`，且标「脚本内写死」 | 显示「被配置文件覆盖」就查 `config.json` 的 model 字段 |
| `输出目录` | Docker 下应是 `/opt/data/...` | 还是 `/root/...` 说明 2.5 节没配 |
| `Python` | **≥ 3.9** | 低于 3.9 见第六节 |

成功判据：图像看到 `[agnes-image] 已保存 N KB`，视频看到 `progress=100%` 和
`[agnes-video] 已保存 N MB`，最后都是一行**绝对路径**。

**Docker 情形最后确认产物真的落到你能看到的地方**（在**宿主机**上跑，不是在容器里）：

```bash
ls -lh <数据目录>/agnes-output/
```

---

## 五、跨平台注意事项

- **不要用 pip 安装任何包**。脚本只用标准库，装了反而在 Hermes 的 Docker/Modal 后端里多一层依赖。
  这对容器部署尤其重要 —— 不用折腾包管理。
- **输出目录**默认 `~/agnes-output`。Docker 部署下要按 2.5 节指到挂载卷里。
- **代理**：`api.agnes-ai.cn` **直连可达**（✅ 实测：不走代理即 TLS 校验通过
  `ssl_verify_result=0`），Agnes 是国内站点，**和为了访问 GitHub 配的那套代理是两回事**。
  在服务器 / 容器上：
  - **调 Agnes API** —— 直连即可，**不要挂代理**
  - **git clone / 下载 ZIP** —— 这一步才可能需要代理（这也是推荐 ZIP 上传的原因）

  > 特别注意本机（Windows）上那种环境变量代理：如果服务器 shell 里也设了
  > `HTTP_PROXY` / `HTTPS_PROXY`，Python 的 `urllib` 会读取它们，一旦代理不通
  > 就会出现莫名其妙的连接失败。排障时先验一下：`env | grep -i proxy`

- **媒体输入**：图生图 / 视频参考吃公网 URL 或 Data URI。
  本地文件会被自动转成 Data URI，但官方要求视频参考媒体是**可公开访问的 URL**，
  本地转 Data URI 只是尽力而为 —— 报 `400 / Invalid reference media` 就先把文件传到公网。
  在远程后端上尤其注意：**脚本在后端跑，读不到你本机的 C 盘**。
- **产物回传**：WorkBuddy 里脚本把产物路径单独成行打到 stdout，直接读这一行即可。
  在 Hermes 里，gateway 会把回复中的媒体路径**默认渲染成内联图**（有损压缩）；
  高分辨率图 / 视频要在回复末尾加 `[[as_document]]`，gateway 才会改成
  可下载的**文件附件**。路径单独成行是设计（两个平台都靠它定位产物），不是巧合。
- **中文输出**：脚本已把 stdout/stderr 编码强制为 UTF-8，
  Windows 的 cp936 控制台不会出现乱码或 UnicodeEncodeError。

---

## 六、排障

| 现象 | 原因与处理 |
| --- | --- |
| 自检报「**密钥来源: 未找到**」 | ① `.env` 里有没有 `AGNES_API_KEY=`（用 `grep -c` 确认只有 1 条）；② **skill 的 frontmatter 有没有 `required_environment_variables`** —— 没有的话 Hermes 会把带 `KEY` 的变量剥离掉；③ Docker 情形确认 `.env` 在容器内的正确路径（用 2.1 的 `docker inspect`） |
| 「**没有找到 Agnes API Key**」 | 同上。另外确认你是从**宿主机**写 `.env`，而不是 `docker exec` 进容器后写进了 `/root/.env` |
| 写完 `.env` 但自检仍读不到 | 环境变量一般在**进程启动时**读取 —— 重启容器再试：`docker restart <容器名>`。<br>⚠️ 未验证：Hermes 是否支持热加载 `.env`，没找到官方说明 |
| Docker 里设了环境变量仍读不到 | 官方补充手段：`config.yaml` 里加 `terminal.env_passthrough: [AGNES_API_KEY]`。**本 skill 已声明，正常不需要** —— 只有删改了 frontmatter 时才用得上 |
| 产物「找不到」 | 见 2.5 节：默认输出在容器内的 `~/agnes-output`，要设 `out_dir: /opt/data/agnes-output` |
| `目标地址不是 Agnes 域名，已中止` | 守卫正常工作（退出码 **3** + `[agnes-guard]`）。要走自建代理请设 `AGNES_TRUSTED_HOSTS=<你的域名>`，别一上来就 `AGNES_ALLOW_ANY_HOST=1` |
| 退出码 `2` 但以为是守卫拦的 | `2` 是请求 / 网络 / API 类失败（**请求已发出**）；被守卫拦下是 `3`。两个码分开正是为了区分这两种 |
| `No module named ...` | 理论上不会（零依赖）。若出现，说明跑的不是 Python 3，检查 `python3 --version` |
| `SyntaxError` | 容器里的 Python 低于 3.7。换个 Python 或升级镜像 |
| `HTTP 404 Not Found`（GET 某个地址时） | ✅ 正常：`/v1` 是 POST 端点，GET 返回 404 不代表服务不可用 |
| `HTTP 429 您已达到免费用户的 API 速率限制` | 视频创建**不能并发**，重试退避要 ≥30 秒 |
| `HTTP 503 视频队列已满` | 服务端队列拥塞，退避 45 秒以上重试，与额度无关 |
| SSL 证书错误 | 企业代理 / 自签证书环境，设 `AGNES_INSECURE=1`（仅可信网络） |
| 中文乱码 / UnicodeEncodeError | 理论上不会：脚本已把 stdout/stderr 强制为 UTF-8 |

---

## 七、升级

**git 克隆来的**：

```bash
cd <SKILL_DIR>
git pull
```

**ZIP 上传的**：重新下载 ZIP 解压，覆盖同名文件即可。

两种方式都**不会**影响 `.env` 和 `config.json`
（它们在覆盖范围之外，或已被 git 忽略）。
