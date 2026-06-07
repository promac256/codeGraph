"""Tests for language parsers."""

from __future__ import annotations

from pathlib import Path

import pytest

from codegraph.parsers.python_parser import PythonParser
from codegraph.parsers.generic_parser import GenericParser


PYTHON_SAMPLE = Path(__file__).parent / "fixtures" / "python_sample"
TS_SAMPLE = Path(__file__).parent / "fixtures" / "typescript_sample"


class TestPythonParser:
    def test_parses_classes(self, tmp_path):
        parser = PythonParser()
        src = PYTHON_SAMPLE / "models.py"
        result = parser.parse(src, src.read_bytes(), PYTHON_SAMPLE)
        class_names = [c.name for c in result.classes]
        assert "Animal" in class_names
        assert "Dog" in class_names
        assert "Cat" in class_names

    def test_parses_functions(self, tmp_path):
        parser = PythonParser()
        src = PYTHON_SAMPLE / "models.py"
        result = parser.parse(src, src.read_bytes(), PYTHON_SAMPLE)
        func_names = [f.name for f in result.functions]
        assert "create_animal" in func_names
        assert "fetch_animal_data" in func_names

    def test_detects_async(self, tmp_path):
        parser = PythonParser()
        src = PYTHON_SAMPLE / "models.py"
        result = parser.parse(src, src.read_bytes(), PYTHON_SAMPLE)
        async_funcs = [f for f in result.functions if f.is_async]
        assert any(f.name == "fetch_animal_data" for f in async_funcs)

    def test_extracts_todos(self, tmp_path):
        parser = PythonParser()
        src = PYTHON_SAMPLE / "models.py"
        result = parser.parse(src, src.read_bytes(), PYTHON_SAMPLE)
        kinds = {t["kind"] for t in result.todos}
        assert "TODO" in kinds or "FIXME" in kinds or "NOTE" in kinds

    def test_inheritance_edges(self, tmp_path):
        parser = PythonParser()
        src = PYTHON_SAMPLE / "models.py"
        result = parser.parse(src, src.read_bytes(), PYTHON_SAMPLE)
        inherit_srcs = [e.src for e in result.inherits]
        assert any("Dog" in s for s in inherit_srcs)
        assert any("Cat" in s for s in inherit_srcs)

    def test_file_node_metadata(self):
        parser = PythonParser()
        src = PYTHON_SAMPLE / "models.py"
        result = parser.parse(src, src.read_bytes(), PYTHON_SAMPLE)
        assert result.file_node.lang == "python"
        assert result.file_node.line_count > 0
        assert result.file_node.sha256 != ""

    def test_detects_test_file(self):
        parser = PythonParser()
        src = PYTHON_SAMPLE / "test_models.py"
        result = parser.parse(src, src.read_bytes(), PYTHON_SAMPLE)
        assert result.file_node.is_test is True

    def test_imports_extracted(self):
        parser = PythonParser()
        src = PYTHON_SAMPLE / "test_models.py"
        result = parser.parse(src, src.read_bytes(), PYTHON_SAMPLE)
        assert len(result.imports) > 0


class TestGenericParser:
    def test_parses_unknown_file(self, tmp_path):
        path = tmp_path / "notes.txt"
        path.write_text("Some text\n# TODO: clean this up\n# FIXME: broken")
        parser = GenericParser()
        result = parser.parse(path, path.read_bytes(), tmp_path)
        assert result.file_node.lang == "txt"
        assert any(t["kind"] == "TODO" for t in result.todos)
        assert any(t["kind"] == "FIXME" for t in result.todos)
