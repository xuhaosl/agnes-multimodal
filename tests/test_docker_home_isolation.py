#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Docker 兜底数据目录（/opt/data）的归属判定。

背景（真机上实测出来的 P0）：
原实现里 _hermes_homes() 与 writable_config_path() **无条件**使用 /opt/data。
后果是 —— 只要进程跑在 Linux 上而 /opt/data 恰好存在，那么无论 HERMES_HOME
指向哪里都会被它穿透。测试隔离、多 profile、非官方部署形态全部失效：
本该隔离的用例读到了真实 home 的 Key。真机表现为 13 项失败。

最阴的地方是**只在容器里复现**：本机 Windows 因为 os.name == "nt" 永远绿，
所以「本机全过」根本证明不了没事 —— 典型的假绿。

这道测试的难点也是平台。直接改 os.name 不行 —— pathlib 会改为实例化
PosixPath，在 Windows 上直接抛 NotImplementedError。所以实现里把平台探测
抽成了 _os_is_posix()，这里 patch 它，配合把 DOCKER_DATA_DIR 指到临时目录，
就能在本机把 posix 分支整条跑一遍。

判据只用一个不变量：返回值要么是 DOCKER_DATA_DIR 本身，要么是 None。
"""
import os
import sys
import tempfile
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

import agnes_common as ac  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("[PASS] " if cond else "[FAIL] ") + name
          + (f"\n         <- {detail}" if detail and not cond else ""))


if not hasattr(ac, "_os_is_posix"):
    print("[FAIL] 实现里没有 _os_is_posix()：平台判断没抽出来，本测试无法注入平台")
    print("")
    print("合计 0/1 项通过")
    sys.exit(1)


class FakeDocker:
    """伪装成「posix 平台 + 数据目录在别处」，退出时还原。

    进入时清掉 HERMES_HOME，保证每个用例从干净的起点开始。
    """

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self._orig_posix = ac._os_is_posix
        self._orig_dir = ac.DOCKER_DATA_DIR
        self._orig_home = os.environ.pop(ac.HERMES_HOME_ENV, None)

    def __enter__(self):
        ac._os_is_posix = lambda: True
        ac.DOCKER_DATA_DIR = str(self.data_dir)
        return self

    def __exit__(self, *exc):
        ac._os_is_posix = self._orig_posix
        ac.DOCKER_DATA_DIR = self._orig_dir
        if self._orig_home is None:
            os.environ.pop(ac.HERMES_HOME_ENV, None)
        else:
            os.environ[ac.HERMES_HOME_ENV] = self._orig_home
        return False


def same(a, b):
    return a is not None and Path(a).resolve() == Path(b).resolve()


with tempfile.TemporaryDirectory() as _td:
    _td = Path(_td)
    data = _td / "opt-data"     # 冒充容器里的 /opt/data
    other = _td / "other-home"  # 冒充隔离 / 多 profile 时用的 HERMES_HOME
    probe = _td / "probe"       # 专门测判据用，避免污染 data 的状态
    for d in (data, other, probe):
        d.mkdir()

    # --------------------------------------------- ⓪ 测试装置自检（先卡这道）
    # 注入若失效，后面「不穿透」的结论全部可疑 —— 所以它单独作为一项，
    # 而且必须真的让数据目录被认出来，而不是「反正都返回 None」。
    (data / "config.yaml").write_text("x: 1\n", encoding="utf-8")
    with FakeDocker(data):
        got = ac._docker_data_dir()
        check("⓪ 测试装置自检：注入 posix + 数据目录后，它确实被认出来",
              same(got, data),
              f"实得 {got} —— 注入没生效，下面几条「不穿透」都会假绿")

    # ---------------------------------------------------------------- ① 判据
    check("① 空目录不算 Hermes home", not ac._looks_like_hermes_home(probe))
    (probe / "config.yaml").write_text("x: 1\n", encoding="utf-8")
    check("① 有 config.yaml 才算", ac._looks_like_hermes_home(probe))
    (probe / "config.yaml").unlink()
    (probe / "config.yml").write_text("x: 1\n", encoding="utf-8")
    check("① config.yml 同样认（两种扩展名都有人在用）", ac._looks_like_hermes_home(probe))
    (probe / "config.yml").unlink()
    (probe / ".env").write_text("A=1\n", encoding="utf-8")
    check("① 只有 .env 也认（Key 可能就放在 env 里）", ac._looks_like_hermes_home(probe))

    # ------------------------------------------- ② 不给 HERMES_HOME：靠内容判定
    with FakeDocker(data):
        got = ac._docker_data_dir()
        check("② 不给 HERMES_HOME + 数据目录里确有 Hermes 配置 → 命中（Docker 正常场景）",
              same(got, data), f"实得 {got}")

    (data / "config.yaml").unlink()     # 现在 data 是空目录
    with FakeDocker(data):
        got = ac._docker_data_dir()
        check("② 不给 HERMES_HOME + 目录里没有 Hermes 配置 → None（不能光凭「目录存在」就认）",
              got is None, f"实得 {got}")

    # ------------------------------------- ③ ★ 核心修复：HERMES_HOME 指向别处
    (data / "config.yaml").write_text(
        "custom_providers:\n"
        "- name: Agnes\n"
        "  base_url: https://api.agnes-ai.cn/v1\n"
        "  api_key: sk-REALHOMESHOULDNEVERLEAK\n",
        encoding="utf-8")

    with FakeDocker(data):
        os.environ[ac.HERMES_HOME_ENV] = str(other)

        got = ac._docker_data_dir()
        check("③ HERMES_HOME 指向别处 → 不再把数据目录当自己家",
              got is None, f"实得 {got} ← 非 None 就是穿透，会读到不属于本 home 的配置")

        homes = [Path(p).resolve() for p in ac._hermes_homes()]
        check("③ _hermes_homes() 里不再出现那个数据目录（读侧穿透已堵）",
              data.resolve() not in homes, f"实得 {[str(h) for h in homes]}")

        os.environ[ac.HERMES_HOME_ENV] = str(data)
        got = ac._docker_data_dir()
        check("③ HERMES_HOME 正好就是数据目录 → 照常命中（INSTALL.md 2.5 承诺不变）",
              same(got, data), f"实得 {got}")

    # --------------------------------------------------- ④ ★ 写侧也不能穿透
    with FakeDocker(data):
        os.environ[ac.HERMES_HOME_ENV] = str(other)
        os.environ.pop(ac.CONFIG_PATH_ENV, None)
        got = Path(ac.writable_config_path())
        check("④ HERMES_HOME 指向别处 → 默认写入点不是 Docker 那份配置（写侧穿透已堵）",
              got != Path(ac.DOCKER_CONFIG_PATH), f"实得 {got}")

    # ------------------------------------------- ⑤ 平台判断必须真的起作用
    # 这里**不**去 patch _os_is_posix —— 那等于把被测对象整个换掉，测了个寂寞
    # （本轮负向验证抓出来的：那样写在 Windows 上永远绿）。
    # 期望值改用**独立依据** os.name 来算；若也用 ac._os_is_posix()，
    # 判据与被测对象共用同一个输入，必然自洽 —— 这个坑本轮又踩了一次。
    # 数据目录此时确有 config.yaml，所以「认不认」只取决于平台判断在不在。
    _orig_dir = ac.DOCKER_DATA_DIR
    _orig_home = os.environ.pop(ac.HERMES_HOME_ENV, None)
    ac.DOCKER_DATA_DIR = str(data)
    try:
        want = data.resolve() if os.name != "nt" else None
        got = ac._docker_data_dir()
        ok = (got is None) if want is None else same(got, want)
        check("⑤ 数据目录的采用严格跟随平台（非 posix 时再像也不认）", ok,
              f"os.name={os.name}，期望 {want}，实得 {got}")
    finally:
        ac.DOCKER_DATA_DIR = _orig_dir
        if _orig_home is not None:
            os.environ[ac.HERMES_HOME_ENV] = _orig_home

    # -------------------------------------------- ⑥ 不变量：不返回越界路径
    with FakeDocker(data):
        seen = []
        for knob in (None, str(data), str(other), "/opt/data", ""):
            if knob is None:
                os.environ.pop(ac.HERMES_HOME_ENV, None)
            else:
                os.environ[ac.HERMES_HOME_ENV] = knob
            seen.append(ac._docker_data_dir())
        bad = [str(v) for v in seen
               if v is not None and Path(v).resolve() != data.resolve()]
        check("⑥ 返回值恒为「数据目录本身」或 None，从不返回别处路径",
              not bad, f"越界值 {bad}")

# ---------------------------------------------- ⑦⑧ 两个常量之间的不变量
check("⑦ 默认 Docker 写入点仍在读候选清单里（写进去必须能读回来）",
      any(Path(p) == Path(ac.DOCKER_CONFIG_PATH) for p in ac._config_candidates()),
      f"DOCKER_CONFIG_PATH={ac.DOCKER_CONFIG_PATH} 不在 "
      f"{[str(p) for p in ac._config_candidates()]}")

check("⑧ DOCKER_CONFIG_PATH 位于 DOCKER_DATA_DIR 之下（两个常量不脱钩）",
      str(ac.DOCKER_CONFIG_PATH).startswith(str(ac.DOCKER_DATA_DIR).rstrip("/") + "/"),
      f"{ac.DOCKER_DATA_DIR} → {ac.DOCKER_CONFIG_PATH}")

# ---------------------------------------------------------------- 汇总
passed = sum(1 for _, ok in results if ok)
print("")
print("合计 %d/%d 项通过" % (passed, len(results)))
sys.exit(0 if passed == len(results) else 1)
