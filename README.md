# depmap

**Module dependency graphs for Python — with cycles and dead code called out.** Zero dependencies, single file, standard-library `ast` only. No tree-sitter, no repo config, no IDE switch.

```
$ python depmap.py path/to/asyncio
modules: 33    internal deps: 129
cycles: 1
  asyncio.tasks → asyncio.timeouts → asyncio.tasks
entry modules (depended on, depending on nothing): 8
```

Node IDs are full relative paths, so two `layout.py` files in different folders never collide. Relative imports are never fuzzy-matched, so it won't invent cycles that don't exist. Files that fail to parse are reported, never silently dropped.

```bash
python depmap.py . --format mermaid   # paste into GitHub / Notion / Typora
python depmap.py . --check            # exit 1 on a cycle — usable in CI
```

<sub>中文说明见下方 · Chinese documentation below</sub>

---

扫描 Python 项目，画出模块之间的依赖关系，顺带找出循环依赖和孤立模块。

**零依赖、单文件、只用标准库 `ast`。** 不装 tree-sitter，不写你的仓库配置，不要求你换个 IDE。

```
$ python depmap.py path/to/asyncio
根目录: .../Lib/asyncio
模块数: 33
内部依赖: 129 条

循环依赖: 1 处
  asyncio.tasks → asyncio.timeouts → asyncio.tasks

入口模块（只被依赖、不依赖别人）: 8 个
  asyncio.constants
  asyncio.coroutines
  asyncio.exceptions
  asyncio.log
  ...
```

---

## 解决什么问题

代码库超过几十个模块之后，「谁依赖谁」就没人说得清了。你想动一个模块，不知道会炸到哪儿；你想删一个文件，不知道还有没有人用。

`depmap` 用三秒钟给你答案：一张依赖图，加上三个直接可行动的结论——**哪里有环、哪里是入口、哪些文件是死代码**。

---

## 用法

不需要安装。Python 3.9+ 直接跑：

```bash
# 摘要（默认）
python depmap.py .

# Mermaid 图，粘进 GitHub / Notion / Typora 就能看
python depmap.py . --format mermaid

# Graphviz
python depmap.py . --format dot -o deps.dot && dot -Tsvg deps.dot -o deps.svg

# 结构化输出，接你自己的工具
python depmap.py . --format json

# CI 里用：有循环依赖就挂
python depmap.py . --check
```

### 输出示例

```bash
$ python depmap.py . --format mermaid
graph LR
  n0["app"]
  n1["app.api"]
  n2["app.core"]
  n3["app.core.db"]
  n0 --> n1
  n1 --> n2
  n2 --> n3
```

---

## 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--format` | `summary` | `summary` / `mermaid` / `dot` / `json` |
| `-o` | stdout | 输出文件 |
| `--check` | 关 | 发现循环依赖时退出码 1 |

退出码：`0` 正常，`1` 有循环依赖（仅 `--check`），`2` 路径无效或没有 .py 文件。

---

## 三条设计红线

**1. 节点标识用完整相对路径，不用文件名。**

两个 `layout.py` 在 `a/` 和 `b/` 下必须是两个节点。用文件名当 ID 会让它们的边互相串台——这种 bug 不报错，只是悄悄给你一张错的图，比崩溃危险得多。

**2. 相对导入不做模糊匹配。**

`from . import x` 里的 `x` 如果不存在，那就是不存在。不能兜底成「整个包」。一旦兜底，每个子模块都会连回 `__init__`，凭空造出一堆假环。

**3. 解析失败的文件必须报出来。**

`bad.py` 有语法错误时，它不会从图里静默消失，而是出现在「解析失败」列表里。看不见的文件比报错的文件更麻烦。

---

## 它不做什么

划清边界，别期待错：

- **不解析动态导入。** `importlib.import_module(name)`、`__import__(...)` 这类运行时才知道目标名的，看不见。
- **不做跨语言。** 只认 `.py`。TypeScript / Go / Rust 不处理。
- **不做函数级调用图。** 只到模块粒度。想知道 `foo()` 调了谁，这个工具帮不上。
- **不做语义检索。** 没有向量库，没有 embedding，不接 LLM。就是一张确定性的结构图。
- **不修改你的任何文件。** 只读。没有 `install` 子命令，不会往 `.codex/` 或 `.claude/` 里写东西。

---

## 实测

拿 CPython 标准库两个真实包跑的结果：

| 包 | 模块数 | 内部依赖 | 循环依赖 |
|---|---|---|---|
| `asyncio` | 33 | 129 | 1 |
| `email` | 29 | 74 | 11 |

`asyncio` 那一个环是 `tasks ↔ timeouts`，对应 `tasks.py:29` 的 `from . import timeouts` 和 `timeouts.py:8` 的 `from . import tasks`，真实存在。

---

## 测试

```bash
python -m unittest discover -s tests -v
```

30 个测试，覆盖模块收集、相对/绝对导入解析、循环检测、孤立模块、三种输出格式、CLI 退出码，以及三个针对误报的回归测试。

---

## 许可证

MIT，见 [LICENSE](LICENSE)。
