# 灵感来源：[Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify)

- **星标**：129,279
- **许可证**：Apache-2.0
- **语言**：Python
- **仓库规模**：31,482 KB，1,567 个未关 issue，712 个未关 issue（issue 类型）
- **它承诺解决**：把任意代码库连同文档、SQL schema、配置文件、PDF 一起，转成一张可查询的知识图谱。

---

## 它做对了什么

**1. 选对了抽象层。**

大多数代码分析工具止步于「列出函数」或「画个类图」。graphify 直接建图——节点是实体，边是关系，然后你就可以问「改这个会影响什么」。这比任何列表都更接近人真正想知道的东西。

**2. 本地确定性 AST 解析，不上向量库。**

README 里明确写了 "local deterministic AST parsing, every edge explained, no vector store"。这是个克制且正确的决定：代码结构是确定的，用 embedding 去近似它，只会引入不确定性而没有收益。

**3. 每条边都能解释来源。**

不是「这两个文件相关度 0.87」，而是「因为第 42 行 import 了它」。可解释性直接决定了这个结果能不能被信任。

**4. 覆盖面极广。**

31 种语言的 AST 提取、多平台安装（Claude Code / Cursor / Codex / Gemini CLI）、文档和 SQL 和 PDF 全吃。这也是它能冲到 129k 星的原因。

---

## 它在哪里让人痛苦

**痛点一：`install` 会往你的仓库里写机器绝对路径。**

> "`graphify install` (0.9.50, Windows, uv tool install) writes a **machine-absolute binary path** into the repo-tracked Codex hooks file `.codex/hooks.json`"
>
> —— [issue #3129](https://github.com/Graphify-Labs/graphify/issues/3129)

关键在 `.codex/hooks.json` 是**被 git 跟踪的共享文件**。写进去的 `C:/Users/<user>/.local/bin/graphify.EXE` 换台机器就废了，而且会污染别人的 checkout。

**痛点二：CLI 主函数 2,560 行。**

> "`graphify/cli.py` is 2,777 lines; `dispatch_command` starts at line 215 and runs to the end — roughly **2,560 lines in a single function**, an `if cmd == … / elif cmd == …` chain."
>
> —— [issue #2474](https://github.com/Graphify-Labs/graphify/issues/2474)

一个函数里塞 2,560 行 if/elif，意味着每加一个子命令都在给这坨东西继续加压。

**痛点三：节点 ID 撞车，边会串台。**

> "`graphify.extract` derives every node ID from `path.stem` (the extension-less basename). Two source files that share a basename therefore collapse into a single graph node — regardless of language or directory. This **silently cross-wires unrelated code**."
>
> —— [issue #1829](https://github.com/Graphify-Labs/graphify/issues/1829)

`a/layout.py` 和 `b/layout.ts` 会变成同一个节点。不报错，只是给你一张错的图。

**痛点四：同输入不同输出。**

[#1667](https://github.com/Graphify-Labs/graphify/issues/1667) 报告相同语料跑两次，社区划分结果不一致。对结构分析工具来说，不可复现的输出基本等于不可用。

---

## 我们的取舍

### 砍掉了什么

| 砍掉 | 理由 |
|------|------|
| 多语言 AST（tree-sitter） | 一个语言做到准，比三十个语言做到半准有用。先只做 Python，标准库 `ast` 就够，零依赖 |
| 文档 / SQL / PDF 摄入 | 那是「知识图谱」，我们只做「代码结构」。混在一起会让输出无法验证 |
| MCP / agent 集成 / install 子命令 | #3129 的根源。一个只读工具不需要往你仓库里写任何东西 |
| 语义层 / embedding | 代码结构是确定的，用近似只会引入噪声 |
| 社区划分（Leiden） | #1667 的不确定性来源。模块依赖图不需要聚类也有用 |

### 保留了什么

- **确定性 AST 解析**：同一份代码跑一百次，输出一模一样
- **每条边可追溯**：边就是 import 语句，没有推断
- **多种输出格式**：Mermaid / DOT / JSON，接得上你现有的工具链

### 加了三条原版没有的

**1. 节点 ID 用完整相对路径。** 直接对应 #1829。`a/layout.py` 和 `b/layout.py` 是两个节点，永远不合并。

**2. 循环依赖检测 + CI 退出码。** `--check` 有环就返回 1，可以直接挂进 CI。原版是给你一张图让你自己看。

**3. 解析失败必须上报。** 有语法错误的文件会出现在 `unparsed` 列表里，不会静默消失。

---

## 开发中修掉的三个误报

这是这个工具最值得说的部分。第一版在 `asyncio` 上报了 7 处循环依赖，看起来很像真的。逐个核对后发现全是误报：

**误报一：扫包目录时，绝对导入被当成内部模块。**

`asyncio/events.py` 里的 `import subprocess` 指的是标准库的 `subprocess`，但因为我以 `asyncio/` 为根扫描，同目录正好有个 `subprocess.py`，就被解析成了内部依赖。

修法：如果根目录本身是包（有 `__init__.py`），模块名统一加包名前缀（`asyncio.events`），绝对导入就再也匹配不上同目录文件了。

**误报二：`from . import x` 把包本身也算成了依赖。**

`asyncio/base_events.py` 里全是 `from . import events` 这类语句。我最初把「包」也放进了候选目标，结果每个子模块都连回 `asyncio/__init__.py`，凭空造出 `asyncio → asyncio.base_events → asyncio` 这种假环。

修法：相对导入只依赖具体的子模块，不依赖包本身。

**误报三：不存在的子模块被兜底成整个包。**

`from . import staggered` 如果 `staggered.py` 不存在，我的「逐级向上找」逻辑会退回 `asyncio`——又一条假边。

修法：模糊匹配只对绝对导入开启。相对导入的基准是确定的，不该猜。

修完这三处，`asyncio` 的循环依赖从 7 处降到 1 处，而那 1 处经 grep 核对是真的（`tasks.py:29` 与 `timeouts.py:8` 互相导入）。三个误报都写了回归测试锁住。

**这件事说明一个道理**：分析工具报出 7 个问题、其中 6 个是假的，比一个都不报更糟——你会开始怀疑它报的第 7 个。

---

## 与原版的差异

| 维度 | graphify | depmap |
|------|----------|--------|
| 安装 | 需要 install 子命令 | **无。下载一个 `.py` 文件** |
| 依赖 | tree-sitter 等 | **标准库，零依赖** |
| 语言覆盖 | 31 种 | 仅 Python |
| 摄入范围 | 代码 + 文档 + SQL + PDF | 仅代码 |
| 是否改你的仓库 | 会写 `.codex/hooks.json` | **只读，不写任何文件** |
| 节点 ID | 文件名（会撞车） | **完整相对路径** |
| 循环依赖 | 不给结论 | **直接列出，可接 CI** |
| 确定性 | 社区划分不确定 | **完全确定** |
| 代码量 | 31 MB 仓库 | 单文件约 380 行 |
| 适合谁 | 想要跨语言知识图谱的 agent 用户 | 想快速看清一个 Python 项目结构的人 |

---

## 许可证说明

参考项目 **Graphify-Labs/graphify** 采用 **Apache-2.0** 许可证。

本工具（`depmap.py`）**未复用该项目的任何代码**。实现完全独立：用 Python 标准库 `ast` 自行解析，模块解析、环检测、渲染逻辑均为自行设计。命名、输出格式、CLI 参数均与该项目的接口不同。

MIT 与 Apache-2.0 兼容，本仓库整体采用 MIT 许可证不构成冲突。
