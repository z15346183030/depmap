#!/usr/bin/env python3
"""depmap —— 扫描 Python 项目，生成模块依赖图。

零依赖，只用标准库。输出 Mermaid / DOT / JSON / 摘要，并标出循环依赖和孤立模块。

用法:
    python depmap.py .                        # 打印摘要
    python depmap.py . --format mermaid       # 输出 Mermaid 图
    python depmap.py . --format dot -o g.dot  # 输出 Graphviz
    python depmap.py . --format json          # 输出结构化结果
    python depmap.py . --check                # 有循环依赖时退出码为 1
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from pathlib import Path

__version__ = "0.1.0"

SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", ".venv", "venv", "env", "node_modules",
    "build", "dist", ".eggs", "site-packages",
}


# ---------------------------------------------------------------- 收集模块


def collect_modules(root: Path) -> dict[str, Path]:
    """遍历目录，返回 {模块名: 文件路径}。

    如果 root 本身就是一个包（含 __init__.py），模块名会带上包名前缀，
    否则 root 下的绝对导入会被误判成内部模块。比如直接扫 asyncio/ 时，
    events.py 里的 `import subprocess` 指的是标准库，不能解析成同目录的
    asyncio/subprocess.py。
    """
    prefix = root.name if (root / "__init__.py").is_file() else ""
    modules: dict[str, Path] = {}

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            path = Path(dirpath) / filename
            rel = path.relative_to(root)
            parts = list(rel.with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]

            if prefix:
                module = ".".join([prefix] + parts) if parts else prefix
            else:
                if not parts:
                    continue
                module = ".".join(parts)

            modules[module] = path
    return modules


def is_package(path: Path) -> bool:
    return path.name == "__init__.py"


def package_of(module: str, path: Path) -> str:
    """返回模块所在的包名（相对导入的基准）。"""
    if is_package(path):
        return module
    head, _, _ = module.rpartition(".")
    return head


# ---------------------------------------------------------------- 解析导入


def parse_imports(path: Path) -> tuple[list[tuple[int, str | None, list[str]]], str | None]:
    """解析一个文件里的所有 import 语句。

    返回 ([(level, module, [names]), ...], 错误信息或 None)。
    level=0 表示绝对导入，level>=1 表示相对导入（1 是当前包）。
    """
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, ValueError) as exc:
        return [], f"{type(exc).__name__}: {exc}"

    found: list[tuple[int, str | None, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((0, alias.name, []))
        elif isinstance(node, ast.ImportFrom):
            names = [alias.name for alias in node.names if alias.name != "*"]
            found.append((node.level or 0, node.module, names))
    return found, None


def _pick(candidates: list[str], modules: dict[str, Path], allow_trim: bool = False) -> str | None:
    """从候选里挑第一个真实存在的模块。

    allow_trim 只对绝对导入开启：`import a.b.c` 里如果 a.b.c 不是项目模块，
    但 a.b 是，那就认为依赖的是 a.b。相对导入的基准是确定的，不能这么猜，
    否则不存在的子模块会被兜底成整个包，凭空造出依赖边。
    """
    for cand in candidates:
        if cand in modules:
            return cand
    if not allow_trim:
        return None
    for cand in candidates:
        parts = cand.split(".")
        while len(parts) > 1:
            parts.pop()
            trimmed = ".".join(parts)
            if trimmed in modules:
                return trimmed
    return None


def resolve(
    level: int,
    module: str | None,
    names: list[str],
    current: str,
    current_path: Path,
    modules: dict[str, Path],
) -> tuple[set[str], set[str]]:
    """把一条 import 语句解析成项目内部模块和外部包名。"""
    internal: set[str] = set()
    external: set[str] = set()

    if level > 0:
        base = package_of(current, current_path)
        base_parts = base.split(".") if base else []
        drop = level - 1
        if drop:
            base_parts = base_parts[: max(len(base_parts) - drop, 0)]
        base = ".".join(base_parts)

        # 注意：不要把 base（包本身）当成依赖目标。
        # `from . import x` 依赖的是 x，不是包；否则每个子模块都会连回
        # 包的 __init__，凭空造出一堆假环。
        roots = []
        if module:
            roots.append(f"{base}.{module}" if base else module)
        for name in names:
            roots.append(f"{base}.{name}" if base else name)
    else:
        roots = [module] if module else []
        for name in names:
            roots.append(f"{module}.{name}" if module else name)

    for root in roots:
        if not root:
            continue
        hit = _pick([root], modules, allow_trim=(level == 0))
        if hit:
            internal.add(hit)
        elif level == 0:
            external.add(root.split(".")[0])

    return internal, external


def build_graph(root: Path) -> dict:
    modules = collect_modules(root)
    edges: set[tuple[str, str]] = set()
    external: dict[str, int] = {}
    unparsed: list[dict] = []

    for module, path in modules.items():
        imports, error = parse_imports(path)
        if error:
            unparsed.append({"module": module, "path": str(path.relative_to(root)), "error": error})
            continue
        for level, mod, names in imports:
            internal, ext = resolve(level, mod, names, module, path, modules)
            for target in internal:
                if target != module:
                    edges.add((module, target))
            for name in ext:
                external[name] = external.get(name, 0) + 1

    return {
        "root": str(root),
        "modules": sorted(modules),
        "edges": sorted(edges),
        "external": dict(sorted(external.items(), key=lambda kv: (-kv[1], kv[0]))),
        "unparsed": sorted(unparsed, key=lambda u: u["module"]),
    }


# ---------------------------------------------------------------- 分析


def find_cycles(edges: list[tuple[str, str]]) -> list[list[str]]:
    """用 DFS 找出循环依赖，每条环路只报一次。"""
    graph: dict[str, list[str]] = {}
    for src, dst in edges:
        graph.setdefault(src, []).append(dst)
    for node in graph:
        graph[node].sort()

    state: dict[str, int] = {}  # 0 未访问 / 1 在栈上 / 2 已完成
    stack: list[str] = []
    seen: set[frozenset] = set()
    cycles: list[list[str]] = []

    def walk(node: str) -> None:
        state[node] = 1
        stack.append(node)
        for nxt in graph.get(node, []):
            if state.get(nxt, 0) == 0:
                walk(nxt)
            elif state.get(nxt) == 1:
                idx = stack.index(nxt)
                cycle = stack[idx:]
                key = frozenset(cycle)
                if key not in seen:
                    seen.add(key)
                    cycles.append(list(cycle))
        stack.pop()
        state[node] = 2

    for node in sorted(graph):
        if state.get(node, 0) == 0:
            walk(node)

    return sorted(cycles, key=lambda c: (len(c), c))


def find_orphans(modules: list[str], edges: list[tuple[str, str]]) -> list[str]:
    """没有任何模块导入它、它自己也不导入别人的模块。"""
    has_in = {dst for _, dst in edges}
    has_out = {src for src, _ in edges}
    return [m for m in modules if m not in has_in and m not in has_out]


def find_roots(modules: list[str], edges: list[tuple[str, str]]) -> list[str]:
    """被别的模块导入、但自己不导入任何模块的模块。"""
    has_in = {dst for _, dst in edges}
    has_out = {src for src, _ in edges}
    return [m for m in modules if m in has_in and m not in has_out]


# ---------------------------------------------------------------- 输出


def to_mermaid(graph: dict) -> str:
    lines = ["graph LR"]
    ids = {name: f"n{i}" for i, name in enumerate(graph["modules"])}
    for name in graph["modules"]:
        lines.append(f'  {ids[name]}["{name}"]')
    for src, dst in graph["edges"]:
        lines.append(f"  {ids[src]} --> {ids[dst]}")
    return "\n".join(lines)


def to_dot(graph: dict) -> str:
    lines = ["digraph deps {", "  rankdir=LR;", '  node [shape=box, fontname="monospace"];']
    for name in graph["modules"]:
        lines.append(f'  "{name}";')
    for src, dst in graph["edges"]:
        lines.append(f'  "{src}" -> "{dst}";')
    lines.append("}")
    return "\n".join(lines)


def to_summary(graph: dict, cycles: list[list[str]], orphans: list[str], roots: list[str]) -> str:
    out = [
        f"根目录: {graph['root']}",
        f"模块数: {len(graph['modules'])}",
        f"内部依赖: {len(graph['edges'])} 条",
        "",
    ]

    if cycles:
        out.append(f"循环依赖: {len(cycles)} 处")
        for cycle in cycles[:10]:
            out.append("  " + " → ".join(cycle + [cycle[0]]))
        if len(cycles) > 10:
            out.append(f"  …另有 {len(cycles) - 10} 处未列出")
    else:
        out.append("循环依赖: 无")
    out.append("")

    out.append(f"入口模块（只被依赖、不依赖别人）: {len(roots)} 个")
    for name in roots[:10]:
        out.append(f"  {name}")
    if len(roots) > 10:
        out.append(f"  …另有 {len(roots) - 10} 个")
    out.append("")

    out.append(f"孤立模块（既不导入也不被导入）: {len(orphans)} 个")
    for name in orphans[:10]:
        out.append(f"  {name}")
    if len(orphans) > 10:
        out.append(f"  …另有 {len(orphans) - 10} 个")

    if graph["external"]:
        out.append("")
        out.append(f"外部依赖: {len(graph['external'])} 个")
        top = list(graph["external"].items())[:10]
        out.append("  " + ", ".join(f"{k}({v})" for k, v in top))

    if graph["unparsed"]:
        out.append("")
        out.append(f"解析失败: {len(graph['unparsed'])} 个文件")
        for item in graph["unparsed"][:10]:
            out.append(f"  {item['path']} — {item['error']}")

    return "\n".join(out)


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="depmap",
        description="扫描 Python 项目，生成模块依赖图。零依赖，只用标准库。",
    )
    ap.add_argument("path", nargs="?", default=".", help="项目根目录（默认当前目录）")
    ap.add_argument("--format", choices=["summary", "mermaid", "dot", "json"], default="summary",
                    help="输出格式（默认 summary）")
    ap.add_argument("-o", "--output", help="输出文件（默认打印到 stdout）")
    ap.add_argument("--check", action="store_true",
                    help="发现循环依赖时以退出码 1 结束，可用于 CI")
    ap.add_argument("--version", action="version", version=f"depmap {__version__}")
    args = ap.parse_args(argv)

    root = Path(args.path).resolve()
    if not root.is_dir():
        print(f"depmap: {root} 不是目录", file=sys.stderr)
        return 2

    graph = build_graph(root)
    if not graph["modules"]:
        print(f"depmap: {root} 下没找到 .py 文件", file=sys.stderr)
        return 2

    cycles = find_cycles(graph["edges"])
    orphans = find_orphans(graph["modules"], graph["edges"])
    roots = find_roots(graph["modules"], graph["edges"])

    if args.format == "summary":
        text = to_summary(graph, cycles, orphans, roots)
    elif args.format == "mermaid":
        text = to_mermaid(graph)
    elif args.format == "dot":
        text = to_dot(graph)
    else:
        payload = dict(graph)
        payload["cycles"] = cycles
        payload["orphans"] = orphans
        payload["roots"] = roots
        text = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"depmap: 已写入 {args.output}", file=sys.stderr)
    else:
        print(text)

    if args.check and cycles:
        print(f"depmap: 发现 {len(cycles)} 处循环依赖", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
