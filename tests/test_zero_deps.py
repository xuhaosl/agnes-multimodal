#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""README「零依赖：不需要 pip 装任何包」的可复现证据。

为什么需要它：这句话是写死的结论。一旦有人往脚本里加一句 `import requests`，
它就变成假话，而且**没有任何东西会报警** —— 直到用户在干净环境里跑挂。
这条测试把「人工查过」变成「每次都能自己跑出来」。

六项：

  ① 空集检查 —— 先确认真的扫到了文件。
     扫不到文件时循环体不执行，后面所有断言都会**自动通过**。
     这是「永远绿的测试」最典型的产生方式，所以它单独作为一项。

  ② 判据输入健全 —— stdlib / site-packages 目录清单都非空。
     清单为空就等于判据坏了，此时「无第三方依赖」是假绿。

  ③ scripts/ 无第三方 import —— README 承诺的对象。
  ④ tests/ 也无第三方 import —— 否则「零依赖的测试」自己就跑不起来，
     承诺等于从后门破了（比如有人为了顺手而引入 pytest）。

  ⑤ 顺序逻辑（纯函数，合成路径）—— 判据必须先判 site、再判 stdlib。
     这一项**不碰文件系统**，所以在任何环境下结论都稳定，专门锁住下面那个坑。

  ⑥ 真实探针 —— 现场把一个包写进真实的 site-packages，判据必须认出它是第三方。
     比合成路径更贴近实战，用来兜住「合成用例构造得不对」的风险。
     site-packages 不可写、或两个来源认的目录没有交集时跳过（有 ② 兜底）。

判据必须防的那个坑（本机实测）：
    site-packages 通常**就嵌在 stdlib 目录下面**
    （Windows：Lib\\ 与 Lib\\site-packages；Linux：lib/python3.x/ 与 .../site-packages）。
    所以必须**先判 site、再判 stdlib**。顺序反了，`requests` 会被判成标准库，
    这条测试就成了摆设 —— ⑤ 就是为此存在的。

踩过的坑（都是负向验证抓出来的，不是想出来的）：
    * 扫描器咬到自己：动态导入最初用正则在**全文**里找，结果本文件 docstring 里
      举例写的那句 import_module 调用被当成真实依赖抓出来（报 `req` 缺失）。
      教训：扫代码用 AST（只认真实节点）；正则会吃进 docstring、注释、示例文本。
    * 自检和被测对象共用输入：探针**落点**最初从 SITE_DIRS 里挑，于是判据一坏
      （SITE_DIRS 变空）探针就放不出去，自检返回 None 被当成「环境不支持」静默跳过，
      整套反而全绿。保护机制不能从被测对象身上取参数 —— 现已改为独立来源 + 取交集。
    * 独立来源不保证对：`site.getsitepackages()` 在 Windows 上是
      [Python 安装根目录, ...\\Lib\\site-packages]，第一项**不是** site-packages，
      它存在且可写，盲取第一项会把探针丢进解释器根目录，自检假失败。
      所以落点取「独立来源 ∩ 判据」的交集。

局限（别把它当成「绝对零依赖」的证明）：
    * 只证明源码没有 import 第三方，**不证明**只用了 3.9 就有的标准库模块
      —— 因为跑测试的机器通常是新版 Python，新版标准库里有的模块在 3.9 上没有。
      要 100% 确认，得在真 3.9 环境里跑一次。
    * 字符串拼接拼出来的模块名（运行期才成形）静态扫不到。
      这里只额外兜住两种常见写法：双下划线 import 与 import_module，
      且参数必须是字符串字面量。
    * 判据靠「路径前缀」区分标准库与第三方，前提是判据算路径与
      `find_spec` 给的 origin 用**同一个**规范化变换（都走 norm_path）。
      若某天这里对不齐（比如一边 resolve 了软链接、另一边没 resolve），
      判据会整体失效 —— ⑤ 的真实探针就是防它静默失效的。
"""
import ast
import importlib.util
import os
import shutil
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
TESTS_DIR = ROOT / "tests"

# 同仓库代码：scripts/ 下的脚本互为本地模块，不算第三方
LOCAL_MODULES = set(p.stem for p in SCRIPTS_DIR.glob("*.py"))

SKIP_DIRS = set([".git", "__pycache__", ".venv", "venv", "env", "node_modules", ".tox"])

PROBE_NAME = "zz_agnes_zerodep_probe"

DYNAMIC_CALLABLES = set(["__import__", "import_module"])


def norm_path(p):
    """判据与被判对象必须共用这一个规范化变换。"""
    return os.path.normcase(str(Path(p).resolve()))


def py_files(base):
    return sorted(
        p for p in base.rglob("*.py")
        if not any(part in SKIP_DIRS for part in p.parts)
    )


def _dirs():
    paths = sysconfig.get_paths()

    def norm(key):
        v = paths.get(key)
        return norm_path(v) if v else None

    stdlib = [x for x in (norm("stdlib"), norm("platstdlib")) if x]
    site = [x for x in (norm("purelib"), norm("platlib")) if x]
    return stdlib, site


STDLIB_DIRS, SITE_DIRS = _dirs()


def _probe_dirs():
    """探针的候选落点 —— 独立来源（site 模块），不复用 SITE_DIRS。

    注意：这里**不能**盲取第一项，详见 self_test_judge 的说明。
    """
    cands = []
    try:
        import site as _site
        cands += list(_site.getsitepackages())
    except Exception:
        pass
    purelib = sysconfig.get_paths().get("purelib")
    if purelib:
        cands.append(purelib)

    seen = set()
    out = []
    for d in cands:
        n = norm_path(d)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


PROBE_DIRS = _probe_dirs()


def classify_origin(origin, stdlib_dirs=None, site_dirs=None):
    """纯函数：给一个模块文件路径，判断它属于标准库还是第三方。

    不碰文件系统，所以可以直接用合成路径断言（见 ⑤）。
    """
    stdlib_dirs = STDLIB_DIRS if stdlib_dirs is None else stdlib_dirs
    site_dirs = SITE_DIRS if site_dirs is None else site_dirs

    origin = origin or ""
    if origin in ("built-in", "frozen") or origin.startswith("<"):
        return "stdlib"
    if not origin:
        return "missing"
    where = Path(norm_path(origin))
    # 顺序不能反：site-packages 常常嵌在 stdlib 目录下面。
    # 用 is_relative_to 而不是字符串 startswith —— 后者不做路径边界判断，
    # 会把 .../site-packages-evil/xxx.py 也当成 site 里的包（前缀吞掉）。
    for s in site_dirs:
        if where.is_relative_to(Path(s)):
            return "third"
    for s in stdlib_dirs:
        if where.is_relative_to(Path(s)):
            return "stdlib"
    return "other"


def classify(name):
    """返回 stdlib / third / missing / other 之一。"""
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ModuleNotFoundError, ValueError):
        return "missing"
    if spec is None:
        return "missing"
    return classify_origin(spec.origin)


def top_level_imports(path):
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                out.add(node.module.split(".")[0])
    return out


def dynamic_imports(path):
    """AST 级的动态导入：只认调用节点 + 字符串字面量参数。

    不用正则在全文里找 —— 那会把 docstring、注释里的示例当成真实依赖。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Name):
            callee = fn.id
        elif isinstance(fn, ast.Attribute):
            callee = fn.attr
        else:
            continue
        if callee not in DYNAMIC_CALLABLES:
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            out.add(first.value.split(".")[0])
    return out


def imports_of(path):
    return top_level_imports(path) | dynamic_imports(path)


def scan(files):
    """返回 (非标准库模块 -> {(分类, 文件)}, 全部模块名)。"""
    bad = {}
    seen = set()
    for f in files:
        for name in imports_of(f):
            seen.add(name)
            if name in LOCAL_MODULES:
                continue
            kind = classify(name)
            if kind == "stdlib":
                continue
            bad.setdefault(name, set()).add((kind, f.relative_to(ROOT).as_posix()))
    return bad, seen


def describe(bad):
    parts = []
    for name in sorted(bad):
        where = sorted(bad[name])
        parts.append("%s[%s] <- %s" % (name, where[0][0], ",".join(w[1] for w in where)))
    return "; ".join(parts[:4])


def probe_targets():
    """落点 = 独立来源 ∩ 判据。取交集而不是取其一，理由见下的实测记录。"""
    site_set = set(SITE_DIRS)
    return [d for d in PROBE_DIRS if d in site_set and Path(d).is_dir()]


def cleanup_probe():
    """清掉上次跑崩可能留下的探针（候选目录全扫，不限于本次落点）。"""
    for d in set(PROBE_DIRS) | set(SITE_DIRS):
        shutil.rmtree(Path(d) / PROBE_NAME, ignore_errors=True)
    importlib.invalidate_caches()


def self_test_judge():
    """在真实 site-packages 里放探针，看判据认不认得出来。无法做则返回 None。

    为什么落点要取交集（本机实测）：
        site.getsitepackages() 在本机返回
            [<Python 安装根目录>, <根目录>\\Lib\\site-packages]
        第一项**不是** site-packages，但它存在且可写 ——
        盲取第一项就会把探针放到解释器根目录里，判据自然认不出，自检假失败。
    """
    cleanup_probe()
    targets = probe_targets()
    if not targets:
        return None

    target = None
    for s in targets:
        d = Path(s) / PROBE_NAME
        if d.exists():
            continue
        try:
            d.mkdir()
            (d / "__init__.py").write_text("", encoding="utf-8")
            target = d
            break
        except OSError:
            continue
    if target is None:
        return None

    importlib.invalidate_caches()
    try:
        return classify(PROBE_NAME)
    finally:
        shutil.rmtree(target, ignore_errors=True)
        importlib.invalidate_caches()


results = []
skipped = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    if cond or not detail:
        print(("[PASS] " if cond else "[FAIL] ") + name)
    else:
        print("[FAIL] " + name + "\n         <- " + detail)


# ---------------------------------------------------------------- ① 空集检查
scripts = py_files(SCRIPTS_DIR)
tests = py_files(TESTS_DIR)
check("扫描范围非空：scripts %d 个 / tests %d 个 .py" % (len(scripts), len(tests)),
      bool(scripts) and bool(tests),
      "扫不到文件时后续断言会全部「自动通过」，必须先把这条卡住")

# ---------------------------------------------------------------- ② 判据输入健全
check("判据输入健全：stdlib / site-packages 目录清单都非空",
      bool(STDLIB_DIRS) and bool(SITE_DIRS),
      "stdlib=%d 个 site=%d 个 —— 清单为空说明判据本身坏了，"
      "此时「无第三方依赖」会假绿" % (len(STDLIB_DIRS), len(SITE_DIRS)))

# ---------------------------------------------------------------- ③ scripts/
bad_scripts, seen_scripts = scan(scripts)
check("scripts/ %d 个脚本无第三方依赖" % len(scripts),
      not bad_scripts, describe(bad_scripts))

# ---------------------------------------------------------------- ④ tests/
bad_tests, seen_tests = scan(tests)
check("tests/ %d 个文件自身也无第三方依赖（否则零依赖从后门破）" % len(tests),
      not bad_tests, describe(bad_tests))

# ---------------------------------------------------------------- ⑤ 顺序逻辑
FAKE_STDLIB = norm_path("Q:/fake/Lib")
FAKE_SITE = norm_path("Q:/fake/Lib/site-packages")   # 故意嵌在 stdlib 目录下
FAKE_OTHER = norm_path("Q:/other/Lib")               # 另一个不相干的 stdlib
FAKE_ELSE = norm_path("Q:/nowhere/mod.py")

# (origin, stdlib 清单, site 清单, 期望, 说明)
ORDER_CASES = [
    (os.path.join(FAKE_SITE, "requests", "__init__.py"), [FAKE_STDLIB], [FAKE_SITE], "third",
     "site-packages 里的包 → third（site 嵌在 stdlib 下时，判序反了会误判成 stdlib）"),
    (os.path.join(FAKE_STDLIB, "json", "__init__.py"), [FAKE_STDLIB], [FAKE_SITE], "stdlib",
     "stdlib 里的模块 → stdlib"),
    (os.path.join(FAKE_SITE, "requests.py"), [FAKE_STDLIB], [FAKE_SITE], "third",
     "site-packages 下的单文件模块 → third"),
    (os.path.join(FAKE_SITE + "-evil", "bad", "__init__.py"), [FAKE_OTHER], [FAKE_SITE], "other",
     "字符串前缀相同但不是子目录（site-packages-evil）→ other，不能被前缀吞掉"),
    (os.path.join(FAKE_SITE + "-evil", "bad", "__init__.py"), [FAKE_STDLIB], [FAKE_SITE], "stdlib",
     "同上，但它在真 stdlib 下 → stdlib，总之绝不能是 third"),
    ("built-in", [FAKE_STDLIB], [FAKE_SITE], "stdlib", "内置模块 → stdlib"),
    ("frozen", [FAKE_STDLIB], [FAKE_SITE], "stdlib", "冻结模块 → stdlib"),
    ("", [FAKE_STDLIB], [FAKE_SITE], "missing", "origin 为空 → missing"),
    (FAKE_ELSE, [FAKE_STDLIB], [FAKE_SITE], "other", "两个清单都不匹配 → other"),
]

order_bad = []
for origin, std_dirs, site_dirs, want, why in ORDER_CASES:
    got = classify_origin(origin, std_dirs, site_dirs)
    if got != want:
        order_bad.append("%s 期望 %s 实得 %s" % (why, want, got))
check("顺序逻辑（%d 个合成用例）：先判 site、再判 stdlib，且路径前缀不吃边界" % len(ORDER_CASES),
      not order_bad, "; ".join(order_bad))

# ---------------------------------------------------------------- ⑥ 真实探针
probe_result = self_test_judge()
if probe_result is None:
    skipped.append("真实探针（site-packages 不可写或与判据无交集，"
                   "已由②判据输入健全兜底）")
else:
    check("真实探针：写进真实 site-packages 的包被判为 third",
          probe_result == "third",
          "探针被判成了 %r —— 判据把第三方当标准库，这条测试会假绿" % probe_result)

# ---------------------------------------------------------------- 汇总
all_seen = seen_scripts | seen_tests
passed = sum(1 for _, ok in results if ok)
print("")
print("覆盖：scripts %d 个 + tests %d 个，共 %d 个顶层 import"
      % (len(scripts), len(tests), len(all_seen)))
print("判据：stdlib 目录 %d 个 / site-packages 目录 %d 个 / 探针候选 %d 个"
      % (len(STDLIB_DIRS), len(SITE_DIRS), len(PROBE_DIRS)))
line = "合计 %d/%d 项通过" % (passed, len(results))
if skipped:
    line += "，跳过 %d 项" % len(skipped)
print(line)
for s in skipped:
    print("[SKIP] " + s)
sys.exit(0 if passed == len(results) else 1)
