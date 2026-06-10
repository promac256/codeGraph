"""Tests for the C/C++ language parser."""

from __future__ import annotations

import pytest
from pathlib import Path

from codegraph.parsers.c_parser import CParser

FIXTURE = Path(__file__).parent / "fixtures" / "c_sample"
ANIMALS_CPP = FIXTURE / "animals.cpp"

try:
    import tree_sitter_cpp  # noqa: F401
    _CPP_AVAILABLE = True
except ImportError:
    _CPP_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _CPP_AVAILABLE, reason="tree-sitter-cpp not installed")


@pytest.fixture()
def parser() -> CParser:
    return CParser()


@pytest.fixture()
def result(parser):
    return parser.parse(ANIMALS_CPP, ANIMALS_CPP.read_bytes(), FIXTURE)


class TestCParserClasses:
    def test_parses_classes(self, result):
        names = [c.name for c in result.classes]
        assert "Animal" in names
        assert "Dog" in names

    def test_parses_struct_as_class(self, result):
        names = [c.name for c in result.classes]
        assert "Point" in names

    def test_parses_enum_as_class(self, result):
        names = [c.name for c in result.classes]
        assert "AdoptionStatus" in names

    def test_class_line_numbers(self, result):
        animal = next(c for c in result.classes if c.name == "Animal")
        assert animal.line_start > 0
        assert animal.line_end >= animal.line_start

    def test_class_docstring(self, result):
        animal = next(c for c in result.classes if c.name == "Animal")
        assert animal.docstring is not None
        assert "animal" in animal.docstring.lower()

    def test_class_bases(self, result):
        dog = next(c for c in result.classes if c.name == "Dog")
        assert "Animal" in dog.bases

    def test_struct_docstring(self, result):
        point = next(c for c in result.classes if c.name == "Point")
        assert point.docstring is not None

    def test_defines_edge_from_file(self, result):
        from codegraph.models import EdgeKind
        from codegraph.utils.hashing import make_class_id
        class_id = make_class_id("animals.cpp", "Animal")
        file_id = "file:animals.cpp"
        edge = next(
            (e for e in result.defines if e.src == file_id and e.dst == class_id), None
        )
        assert edge is not None
        assert edge.kind == EdgeKind.DEFINES


class TestCParserMethods:
    def test_parses_methods(self, result):
        names = [f.name for f in result.functions]
        assert "getName" in names
        assert "getSpecies" in names
        assert "displayName" in names
        assert "speak" in names

    def test_method_qualified_name(self, result):
        get_name = next(
            (f for f in result.functions if f.qualified_name == "Animal.getName"), None
        )
        assert get_name is not None

    def test_method_is_async_false(self, result):
        for fn in result.functions:
            assert fn.is_async is False

    def test_complexity_branching(self, result):
        # learnTrick has an if statement
        learn = next(
            (f for f in result.functions if f.qualified_name == "Dog.learnTrick"), None
        )
        assert learn is not None
        assert (learn.complexity or 1) > 1

    def test_defines_edge_from_class(self, result):
        from codegraph.models import EdgeKind
        from codegraph.utils.hashing import make_class_id, make_func_id
        class_id = make_class_id("animals.cpp", "Animal")
        func_id = make_func_id("animals.cpp", "Animal.getName")
        edge = next(
            (e for e in result.defines if e.src == class_id and e.dst == func_id), None
        )
        assert edge is not None
        assert edge.kind == EdgeKind.DEFINES

    def test_method_docstring(self, result):
        get_name = next(
            (f for f in result.functions if f.qualified_name == "Animal.getName"), None
        )
        assert get_name is not None
        assert get_name.docstring is not None

    def test_method_signature(self, result):
        get_name = next(
            (f for f in result.functions if f.qualified_name == "Animal.getName"), None
        )
        assert get_name is not None
        assert "getName" in (get_name.signature or "")


class TestCParserFreeFunction:
    def test_parses_free_function(self, result):
        names = [f.name for f in result.functions]
        assert "createAnimal" in names

    def test_free_function_not_classmethod(self, result):
        create = next(
            (f for f in result.functions if f.name == "createAnimal"), None
        )
        assert create is not None
        assert create.is_classmethod is False

    def test_free_function_complexity(self, result):
        create = next(
            (f for f in result.functions if f.name == "createAnimal"), None
        )
        assert create is not None
        assert (create.complexity or 1) > 1

    def test_free_function_defines_edge(self, result):
        from codegraph.models import EdgeKind
        from codegraph.utils.hashing import make_func_id
        func_id = make_func_id("animals.cpp", "createAnimal")
        file_id = "file:animals.cpp"
        edge = next(
            (e for e in result.defines if e.src == file_id and e.dst == func_id), None
        )
        assert edge is not None


class TestCParserIncludes:
    def test_parses_includes(self, result):
        modules = [e.meta.get("module") for e in result.imports]
        assert any("string" in (m or "") for m in modules)

    def test_system_include_not_relative(self, result):
        system = [e for e in result.imports if not e.meta.get("is_relative")]
        assert len(system) >= 1

    def test_local_include_is_relative(self, result):
        local = [e for e in result.imports if e.meta.get("is_relative")]
        assert len(local) >= 1
        assert any("shelter_config" in (e.meta.get("module") or "") for e in local)

    def test_import_dst_prefixed(self, result):
        for edge in result.imports:
            assert edge.dst.startswith("module:")

    def test_multiple_includes(self, result):
        assert len(result.imports) >= 3


class TestCParserTodos:
    def test_extracts_todo(self, result):
        kinds = [t["kind"] for t in result.todos]
        assert "TODO" in kinds

    def test_extracts_fixme(self, result):
        kinds = [t["kind"] for t in result.todos]
        assert "FIXME" in kinds

    def test_extracts_note(self, result):
        kinds = [t["kind"] for t in result.todos]
        assert "NOTE" in kinds

    def test_todo_text(self, result):
        todo = next(t for t in result.todos if t["kind"] == "TODO")
        assert len(todo["text"]) > 0


class TestCParserFileNode:
    def test_file_lang_cpp(self, result):
        assert result.file_node.lang == "cpp"

    def test_c_file_lang(self, parser):
        fake = FIXTURE / "main.c"
        r = parser.parse(fake, b"int main() { return 0; }", FIXTURE)
        assert r.file_node.lang == "c"

    def test_non_test_file(self, result):
        assert result.file_node.is_test is False

    def test_test_file_detection(self, parser):
        fake = FIXTURE / "animals_test.cpp"
        r = parser.parse(fake, b"void test_speak() {}", FIXTURE)
        assert r.file_node.is_test is True

    def test_tests_dir_detection(self, parser):
        fake = FIXTURE / "tests" / "animal_tests.cpp"
        r = parser.parse(fake, b"void test_foo() {}", FIXTURE)
        assert r.file_node.is_test is True

    def test_line_count(self, result):
        src = ANIMALS_CPP.read_text()
        assert result.file_node.line_count == len(src.splitlines())

    def test_can_parse_cpp(self, parser):
        assert parser.can_parse(Path("foo.cpp"))

    def test_can_parse_c(self, parser):
        assert parser.can_parse(Path("foo.c"))

    def test_can_parse_hpp(self, parser):
        assert parser.can_parse(Path("foo.hpp"))

    def test_cannot_parse_py(self, parser):
        assert not parser.can_parse(Path("foo.py"))
