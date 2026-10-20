"""depmap 的测试。只用标准库 unittest，跑 `python -m unittest discover` 即可。"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import depmap  # noqa: E402


def make_project(root: pathlib.Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


class TestCollect(unittest.TestCase):
    def test_module_names_and_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            make_project(root, {
                "pkg/__init__.py": "",
                "pkg/core.py": "",
                "pkg/sub/__init__.py": "",
                "pkg/sub/deep.py": "",
                "top.py": "",
            })
            modules = depmap.collect_modules(root)
            self.assertEqual(
                sorted(modules), ["pkg", "pkg.core", "pkg.sub", "pkg.sub.deep", "top"]
            )

    def test_skips_noise_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            make_project(root, {
                "keep.py": "",
                "__pycache__/junk.py": "",
                ".venv/lib/x.py": "",
                "node_modules/y.py": "",
            })
            modules = depmap.collect_modules(root)
            self.assertEqual(sorted(modules), ["keep"])

    def test_same_basename_stays_distinct(self):
        """回归测试：同名文件不能塌成同一个节点（对应上游 issue #1829）。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            make_project(root, {"a/layout.py": "", "b/layout.py": ""})
            modules = depmap.collect_modules(root)
            self.assertIn("a.layout", modules)
            self.assertIn("b.layout", modules)
            self.assertEqual(len(modules), 2)

    def test_root_that_is_a_package_gets_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "mypkg"
            make_project(root, {
                "__init__.py": "",
                "events.py": "",
                "sub/__init__.py": "",
            })
            modules = depmap.collect_modules(root)
            self.assertEqual(sorted(modules), ["mypkg", "mypkg.events", "mypkg.sub"])


class TestResolve(unittest.TestCase):
    def _graph(self, files: dict[str, str]) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            make_project(root, files)
            return depmap.build_graph(root)

    def test_relative_import_from_dot(self):
        g = self._graph({
            "pkg/__init__.py": "",
            "pkg/mod.py": "from . import helper\n",
            "pkg/helper.py": "",
        })
        self.assertIn(("pkg.mod", "pkg.helper"), g["edges"])

    def test_relative_import_from_named_module(self):
        g = self._graph({
            "pkg/__init__.py": "",
            "pkg/mod.py": "from .helper import thing\n",
            "pkg/helper.py": "thing = 1\n",
        })
        self.assertIn(("pkg.mod", "pkg.helper"), g["edges"])

    def test_parent_relative_import(self):
        g = self._graph({
            "pkg/__init__.py": "",
            "pkg/core.py": "",
            "pkg/sub/__init__.py": "",
            "pkg/sub/mod.py": "from ..core import x\n",
        })
        self.assertIn(("pkg.sub.mod", "pkg.core"), g["edges"])

    def test_absolute_internal_import(self):
        g = self._graph({
            "pkg/__init__.py": "",
            "pkg/core.py": "",
            "pkg/mod.py": "import pkg.core\n",
        })
        self.assertIn(("pkg.mod", "pkg.core"), g["edges"])

    def test_from_import_resolves_submodule(self):
        g = self._graph({
            "pkg/__init__.py": "",
            "pkg/sub/__init__.py": "",
            "pkg/mod.py": "from pkg import sub\n",
        })
        self.assertIn(("pkg.mod", "pkg.sub"), g["edges"])

    def test_external_packages_counted_not_edged(self):
        g = self._graph({"mod.py": "import requests\nimport os\n"})
        self.assertEqual(g["edges"], [])
        self.assertIn("requests", g["external"])
        self.assertIn("os", g["external"])

    def test_self_import_ignored(self):
        g = self._graph({"pkg/__init__.py": "", "pkg/mod.py": "from . import mod\n"})
        self.assertNotIn(("pkg.mod", "pkg.mod"), g["edges"])

    def test_absolute_import_not_confused_with_sibling_file(self):
        """回归测试：扫一个包目录时，`import subprocess` 指的是标准库，
        不能解析成同目录下的 subprocess.py。这是实测 asyncio 时发现的误报。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "mypkg"
            make_project(root, {
                "__init__.py": "",
                "events.py": "import subprocess\n",
                "subprocess.py": "",
            })
            g = depmap.build_graph(root)
            self.assertNotIn(("mypkg.events", "mypkg.subprocess"), g["edges"])
            self.assertIn("subprocess", g["external"])

    def test_relative_import_still_works_inside_package_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "mypkg"
            make_project(root, {
                "__init__.py": "",
                "events.py": "",
                "subprocess.py": "from . import events\n",
            })
            g = depmap.build_graph(root)
            self.assertIn(("mypkg.subprocess", "mypkg.events"), g["edges"])

    def test_relative_import_of_missing_module_creates_no_edge(self):
        """回归测试：`from . import 不存在的模块` 不能兜底成整个包，
        否则每个子模块都会连回 __init__，凭空造出假环。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "mypkg"
            make_project(root, {
                "__init__.py": "",
                "mod.py": "from . import totally_missing\n",
            })
            g = depmap.build_graph(root)
            self.assertEqual(g["edges"], [])

    def test_absolute_import_still_trims_to_real_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            make_project(root, {
                "pkg/__init__.py": "",
                "pkg/mod.py": "",
                "main.py": "import pkg.deeply.nested.missing\n",
            })
            g = depmap.build_graph(root)
            self.assertIn(("main", "pkg"), g["edges"])


class TestAnalysis(unittest.TestCase):
    def test_detects_two_node_cycle(self):
        edges = [("a", "b"), ("b", "a")]
        cycles = depmap.find_cycles(edges)
        self.assertEqual(len(cycles), 1)
        self.assertEqual(sorted(cycles[0]), ["a", "b"])

    def test_detects_three_node_cycle(self):
        edges = [("a", "b"), ("b", "c"), ("c", "a")]
        cycles = depmap.find_cycles(edges)
        self.assertEqual(len(cycles), 1)
        self.assertEqual(sorted(cycles[0]), ["a", "b", "c"])

    def test_no_cycle_in_dag(self):
        self.assertEqual(depmap.find_cycles([("a", "b"), ("b", "c")]), [])

    def test_cycle_reported_once(self):
        # 同一个环从不同起点都能走通，但不能重复上报
        edges = [("a", "b"), ("b", "c"), ("c", "a")]
        self.assertEqual(len(depmap.find_cycles(edges)), 1)

    def test_orphans(self):
        modules = ["a", "b", "lonely"]
        edges = [("a", "b")]
        self.assertEqual(depmap.find_orphans(modules, edges), ["lonely"])

    def test_roots(self):
        modules = ["a", "b", "c"]
        edges = [("a", "b")]
        # c 没有任何边，算孤立不算入口
        self.assertEqual(depmap.find_roots(modules, edges), ["b"])


class TestRender(unittest.TestCase):
    def setUp(self):
        self.graph = {
            "root": "/tmp/x",
            "modules": ["a", "b"],
            "edges": [("a", "b")],
            "external": {"requests": 2},
            "unparsed": [],
        }

    def test_mermaid_has_edge(self):
        out = depmap.to_mermaid(self.graph)
        self.assertTrue(out.startswith("graph LR"))
        self.assertIn("-->", out)
        self.assertIn('"a"', out)

    def test_dot_has_edge(self):
        out = depmap.to_dot(self.graph)
        self.assertIn('"a" -> "b";', out)
        self.assertTrue(out.startswith("digraph deps {"))

    def test_summary_mentions_counts(self):
        text = depmap.to_summary(self.graph, [], ["a", "b"], ["b"])
        self.assertIn("模块数: 2", text)
        self.assertIn("循环依赖: 无", text)


class TestCli(unittest.TestCase):
    def test_json_output_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            make_project(root, {"a.py": "import b\n", "b.py": ""})
            out = root / "g.json"
            code = depmap.main([str(root), "--format", "json", "-o", str(out)])
            self.assertEqual(code, 0)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertIn(["a", "b"], data["edges"])

    def test_check_fails_on_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            make_project(root, {"a.py": "import b\n", "b.py": "import a\n"})
            self.assertEqual(depmap.main([str(root), "--check"]), 1)

    def test_check_passes_without_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            make_project(root, {"a.py": "import b\n", "b.py": ""})
            self.assertEqual(depmap.main([str(root), "--check"]), 0)

    def test_broken_file_reported_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            make_project(root, {"good.py": "", "bad.py": "def broken(:\n"})
            out = root / "g.json"
            depmap.main([str(root), "--format", "json", "-o", str(out)])
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(len(data["unparsed"]), 1)
            self.assertEqual(data["unparsed"][0]["module"], "bad")

    def test_missing_dir_returns_2(self):
        self.assertEqual(depmap.main(["/definitely/not/a/dir"]), 2)

    def test_empty_dir_returns_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(depmap.main([tmp]), 2)


if __name__ == "__main__":
    unittest.main()
