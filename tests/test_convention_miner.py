"""Tests for ConventionMiner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codegraph.enrichment.convention_miner import ConventionMiner, _naming_style, _file_naming_style
from codegraph.models import ClassNode, FileNode, FunctionNode, GraphEdge, EdgeKind
from codegraph.utils.hashing import sha256_bytes


# ---------------------------------------------------------------------------
# Unit: naming style helpers
# ---------------------------------------------------------------------------


class TestNamingStyle:
    def test_snake_case(self):
        assert _naming_style("create_animal") == "snake_case"

    def test_pascal_case(self):
        assert _naming_style("AnimalService") == "PascalCase"

    def test_camel_case(self):
        assert _naming_style("getUserById") == "camelCase"

    def test_upper_snake(self):
        assert _naming_style("MAX_RETRIES") == "UPPER_SNAKE"

    def test_dunder(self):
        assert _naming_style("__init__") == "dunder"

    def test_private_single(self):
        assert _naming_style("_helper") == "private"

    def test_empty(self):
        assert _naming_style("") == "unknown"


class TestFileNamingStyle:
    def test_snake_case_file(self):
        assert _file_naming_style("codegraph/graph/graph_store.py") == "snake_case"

    def test_kebab_case_file(self):
        assert _file_naming_style("src/my-component.ts") == "kebab-case"

    def test_pascal_case_file(self):
        assert _file_naming_style("src/MyComponent.tsx") == "PascalCase"

    def test_lowercase_file(self):
        assert _file_naming_style("main.go") == "lowercase"


# ---------------------------------------------------------------------------
# Integration: mine() against a populated store
# ---------------------------------------------------------------------------


def _populate_store(store):
    """Insert a representative set of nodes for convention analysis."""
    # Files — all use snake_case stems so dominant is snake_case
    store.upsert_node(FileNode(
        node_id="file:src/animal_models.py", path="src/animal_models.py", lang="python",
        line_count=100, sha256=sha256_bytes(b""),
    ))
    store.upsert_node(FileNode(
        node_id="file:tests/test_models.py", path="tests/test_models.py", lang="python",
        line_count=50, sha256=sha256_bytes(b""), is_test=True,
    ))
    store.upsert_node(FileNode(
        node_id="file:src/api_client.ts", path="src/api_client.ts", lang="typescript",
        line_count=80, sha256=sha256_bytes(b""),
    ))

    # Functions — mix of styles
    for name, kwargs in [
        ("create_animal", {"is_exported": True, "docstring": "Creates an animal.", "complexity": 3}),
        ("index_by_name", {"is_exported": True, "docstring": None, "complexity": 2}),
        ("_validate", {"is_exported": False, "docstring": None, "complexity": 1}),
        ("getUserById", {"is_exported": True, "docstring": "Gets a user.", "complexity": 1}),
        ("fetchData", {"is_exported": True, "docstring": None, "complexity": 5, "is_async": True}),
        ("test_create", {"is_exported": True, "docstring": None, "complexity": 1}),
    ]:
        fn = FunctionNode(
            node_id=f"func:src/animal_models.py::{name}",
            name=name,
            qualified_name=name,
            file="file:src/animal_models.py",
            signature=f"def {name}():",
            **kwargs,
        )
        store.upsert_node(fn)

    # High complexity function
    store.upsert_node(FunctionNode(
        node_id="func:src/animal_models.py::big_fn",
        name="big_fn", qualified_name="big_fn",
        file="file:src/animal_models.py",
        signature="def big_fn(x):",
        complexity=15,
        is_exported=True,
    ))

    # Classes
    store.upsert_node(ClassNode(
        node_id="class:src/animal_models.py::Animal",
        name="Animal", file="file:src/animal_models.py",
        docstring="Base animal.", is_exported=True, is_abstract=True,
    ))
    store.upsert_node(ClassNode(
        node_id="class:src/animal_models.py::Dog",
        name="Dog", file="file:src/animal_models.py",
        docstring=None, is_exported=True, is_dataclass=True,
    ))
    store.upsert_node(ClassNode(
        node_id="class:src/animal_models.py::undocumented",
        name="undocumented", file="file:src/animal_models.py",
        docstring=None, is_exported=False,
    ))

    # Import edges
    store.upsert_edge(GraphEdge(
        src="file:src/animal_models.py", dst="module:os",
        kind=EdgeKind.IMPORTS, meta={"module": "os", "is_relative": False},
    ))
    store.upsert_edge(GraphEdge(
        src="file:src/animal_models.py", dst="module:os.path",
        kind=EdgeKind.IMPORTS, meta={"module": "os.path", "is_relative": False},
    ))
    store.upsert_edge(GraphEdge(
        src="file:src/api_client.ts", dst="module:react",
        kind=EdgeKind.IMPORTS, meta={"module": "react", "is_relative": False},
    ))
    store.commit_transaction()


class TestConventionMinerMine:
    @pytest.fixture
    def miner(self, tmp_db):
        _populate_store(tmp_db)
        tmp_db.load_graph_to_memory()
        # Re-populate graph from db
        _populate_store(tmp_db)
        return ConventionMiner(tmp_db)

    def test_returns_all_sections(self, miner):
        report = miner.mine()
        assert "naming" in report
        assert "documentation" in report
        assert "patterns" in report
        assert "complexity" in report
        assert "imports" in report
        assert "tests" in report
        assert "languages" in report

    # --- Naming ---

    def test_detects_snake_case_dominant(self, miner):
        report = miner.mine()
        assert report["naming"]["function_style"] == "snake_case"

    def test_detects_pascal_class_style(self, miner):
        report = miner.mine()
        # Animal, Dog are PascalCase; "undocumented" is snake_case — PascalCase should win
        assert report["naming"]["class_style"] == "PascalCase"

    def test_function_style_counts_present(self, miner):
        report = miner.mine()
        counts = report["naming"]["function_style_counts"]
        assert "snake_case" in counts
        assert counts["snake_case"] >= 1

    def test_file_naming_detected(self, miner):
        report = miner.mine()
        assert report["naming"]["file_style"] == "snake_case"

    # --- Documentation ---

    def test_doc_coverage_totals_nonzero(self, miner):
        report = miner.mine()
        assert report["documentation"]["public_functions"]["total"] > 0

    def test_documented_count_correct(self, miner):
        report = miner.mine()
        # create_animal has docstring; getUserById has docstring
        assert report["documentation"]["public_functions"]["documented"] >= 2

    def test_public_class_coverage(self, miner):
        report = miner.mine()
        coverage = report["documentation"]["public_classes"]
        # Animal is documented, Dog is not
        assert coverage["total"] >= 2
        assert coverage["documented"] >= 1

    # --- Patterns ---

    def test_async_count(self, miner):
        report = miner.mine()
        assert report["patterns"]["async_functions"] >= 1

    def test_abstract_count(self, miner):
        report = miner.mine()
        assert report["patterns"]["abstract_classes"] >= 1

    def test_dataclass_count(self, miner):
        report = miner.mine()
        assert report["patterns"]["dataclasses"] >= 1

    def test_exported_pct_positive(self, miner):
        report = miner.mine()
        assert report["patterns"]["exported_functions_pct"] > 0

    # --- Complexity ---

    def test_complexity_average(self, miner):
        report = miner.mine()
        assert report["complexity"]["average"] >= 1.0

    def test_high_complexity_detected(self, miner):
        report = miner.mine()
        assert report["complexity"]["high_complexity_count"] >= 1
        top = report["complexity"]["high_complexity_functions"]
        assert len(top) >= 1
        assert top[0]["name"] == "big_fn"
        assert top[0]["complexity"] == 15

    # --- Imports ---

    def test_top_imports_present(self, miner):
        report = miner.mine()
        modules = [e["module"] for e in report["imports"]["top_imports"]]
        assert "os" in modules or "react" in modules

    def test_import_total_positive(self, miner):
        report = miner.mine()
        assert report["imports"]["total_import_edges"] >= 3

    # --- Tests ---

    def test_test_file_count(self, miner):
        report = miner.mine()
        assert report["tests"]["test_files"] == 1
        assert report["tests"]["code_files"] >= 1

    # --- Languages ---

    def test_language_breakdown(self, miner):
        report = miner.mine()
        langs = report["languages"]["files_by_lang"]
        assert "python" in langs
        assert "typescript" in langs

    def test_primary_language_python(self, miner):
        report = miner.mine()
        # 2 Python files vs 1 TypeScript
        assert report["languages"]["primary_language"] == "python"


class TestConventionMinerPersistence:
    def test_mine_and_save_stores_report(self, tmp_db):
        _populate_store(tmp_db)
        miner = ConventionMiner(tmp_db)
        report = miner.mine_and_save()
        assert isinstance(report, dict)

        loaded = ConventionMiner.load(tmp_db)
        assert loaded is not None
        assert "naming" in loaded
        assert loaded["naming"] == report["naming"]

    def test_load_returns_none_when_not_run(self, tmp_db):
        result = ConventionMiner.load(tmp_db)
        assert result is None

    def test_load_returns_dict_after_save(self, tmp_db):
        _populate_store(tmp_db)
        ConventionMiner(tmp_db).mine_and_save()
        result = ConventionMiner.load(tmp_db)
        assert isinstance(result, dict)
