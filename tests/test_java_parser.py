"""Tests for the Java language parser."""

from __future__ import annotations

import pytest
from pathlib import Path

from codegraph.parsers.java_parser import JavaParser

FIXTURE = Path(__file__).parent / "fixtures" / "java_sample"
ANIMALS_JAVA = FIXTURE / "Animals.java"

try:
    import tree_sitter_java  # noqa: F401
    _JAVA_AVAILABLE = True
except ImportError:
    _JAVA_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _JAVA_AVAILABLE, reason="tree-sitter-java not installed")


@pytest.fixture()
def parser() -> JavaParser:
    return JavaParser()


@pytest.fixture()
def result(parser):
    return parser.parse(ANIMALS_JAVA, ANIMALS_JAVA.read_bytes(), FIXTURE)


class TestJavaParserClasses:
    def test_parses_classes(self, result):
        names = [c.name for c in result.classes]
        assert "Animal" in names
        assert "Dog" in names
        assert "Cat" in names

    def test_parses_enum_as_class(self, result):
        names = [c.name for c in result.classes]
        assert "AdoptionStatus" in names

    def test_class_exported(self, result):
        animal = next(c for c in result.classes if c.name == "Animal")
        assert animal.is_exported is True

    def test_class_abstract(self, result):
        animal = next(c for c in result.classes if c.name == "Animal")
        assert animal.is_abstract is True

    def test_non_abstract_class(self, result):
        dog = next(c for c in result.classes if c.name == "Dog")
        assert dog.is_abstract is False

    def test_class_line_numbers(self, result):
        dog = next(c for c in result.classes if c.name == "Dog")
        assert dog.line_start > 0
        assert dog.line_end >= dog.line_start

    def test_class_docstring(self, result):
        animal = next(c for c in result.classes if c.name == "Animal")
        assert animal.docstring is not None
        assert "animal" in animal.docstring.lower()

    def test_class_bases(self, result):
        dog = next(c for c in result.classes if c.name == "Dog")
        assert "Animal" in dog.bases

    def test_defines_edge_from_file(self, result):
        from codegraph.models import EdgeKind
        from codegraph.utils.hashing import make_class_id
        class_id = make_class_id("Animals.java", "Animal")
        file_id = "file:Animals.java"
        edge = next(
            (e for e in result.defines if e.src == file_id and e.dst == class_id), None
        )
        assert edge is not None
        assert edge.kind == EdgeKind.DEFINES

    def test_factory_class(self, result):
        names = [c.name for c in result.classes]
        assert "AnimalFactory" in names


class TestJavaParserInterfaces:
    def test_parses_interface(self, result):
        names = [t.name for t in result.types]
        assert "Speaker" in names

    def test_interface_has_definition(self, result):
        speaker = next(t for t in result.types if t.name == "Speaker")
        assert speaker.definition and len(speaker.definition) > 0

    def test_interface_docstring(self, result):
        speaker = next(t for t in result.types if t.name == "Speaker")
        assert speaker.docstring is not None

    def test_interface_defines_edge(self, result):
        from codegraph.models import EdgeKind
        from codegraph.utils.hashing import make_type_id
        type_id = make_type_id("Animals.java", "Speaker")
        file_id = "file:Animals.java"
        edge = next(
            (e for e in result.defines if e.src == file_id and e.dst == type_id), None
        )
        assert edge is not None
        assert edge.kind == EdgeKind.DEFINES


class TestJavaParserImplements:
    def test_dog_implements_speaker(self, result):
        from codegraph.models import EdgeKind
        from codegraph.utils.hashing import make_class_id, make_type_id
        class_id = make_class_id("Animals.java", "Dog")
        type_id = make_type_id("Animals.java", "Speaker")
        edge = next(
            (e for e in result.exports if e.src == class_id and e.dst == type_id), None
        )
        assert edge is not None
        assert edge.kind == EdgeKind.IMPLEMENTS

    def test_cat_implements_speaker(self, result):
        from codegraph.models import EdgeKind
        from codegraph.utils.hashing import make_class_id, make_type_id
        class_id = make_class_id("Animals.java", "Cat")
        type_id = make_type_id("Animals.java", "Speaker")
        edge = next(
            (e for e in result.exports if e.src == class_id and e.dst == type_id), None
        )
        assert edge is not None


class TestJavaParserMethods:
    def test_parses_methods(self, result):
        names = [f.name for f in result.functions]
        assert "getName" in names
        assert "displayName" in names
        assert "speak" in names
        assert "describe" in names

    def test_constructor_parsed(self, result):
        names = [f.name for f in result.functions]
        assert "Animal" in names  # constructor has same name as class

    def test_method_qualified_name(self, result):
        get_name = next(
            (f for f in result.functions if f.qualified_name == "Animal.getName"), None
        )
        assert get_name is not None

    def test_static_method_is_classmethod(self, result):
        create = next(
            (f for f in result.functions if f.qualified_name == "AnimalFactory.create"), None
        )
        assert create is not None
        assert create.is_classmethod is True

    def test_instance_method_not_classmethod(self, result):
        speak = next(
            (f for f in result.functions if f.qualified_name == "Dog.speak"), None
        )
        assert speak is not None
        assert speak.is_classmethod is False

    def test_method_is_async_false(self, result):
        # Java has no native async keyword
        for fn in result.functions:
            assert fn.is_async is False

    def test_method_exported(self, result):
        get_name = next(f for f in result.functions if f.qualified_name == "Animal.getName")
        assert get_name.is_exported is True

    def test_method_signature(self, result):
        create = next(f for f in result.functions if f.qualified_name == "AnimalFactory.create")
        assert "create" in (create.signature or "")
        assert "String" in (create.signature or "")

    def test_method_docstring(self, result):
        display = next(
            (f for f in result.functions if f.qualified_name == "Animal.displayName"), None
        )
        assert display is not None
        assert display.docstring is not None

    def test_complexity_branching(self, result):
        # AdoptionStatus.label() has switch with 4 cases
        label = next(
            (f for f in result.functions if f.qualified_name == "AdoptionStatus.label"), None
        )
        assert label is not None
        assert (label.complexity or 1) > 1

    def test_defines_edge_from_class(self, result):
        from codegraph.models import EdgeKind
        from codegraph.utils.hashing import make_class_id, make_func_id
        class_id = make_class_id("Animals.java", "Animal")
        func_id = make_func_id("Animals.java", "Animal.getName")
        edge = next(
            (e for e in result.defines if e.src == class_id and e.dst == func_id), None
        )
        assert edge is not None
        assert edge.kind == EdgeKind.DEFINES

    def test_enum_method_parsed(self, result):
        is_final = next(
            (f for f in result.functions if f.qualified_name == "AdoptionStatus.isFinal"), None
        )
        assert is_final is not None

    def test_index_by_name_method(self, result):
        index = next(
            (f for f in result.functions if f.qualified_name == "AnimalFactory.indexByName"), None
        )
        assert index is not None


class TestJavaParserImports:
    def test_parses_imports(self, result):
        modules = [e.meta.get("module") for e in result.imports]
        assert any("java.util" in (m or "") for m in modules)

    def test_import_dst_prefixed(self, result):
        for edge in result.imports:
            assert edge.dst.startswith("module:")

    def test_import_not_relative(self, result):
        for edge in result.imports:
            assert edge.meta.get("is_relative") is False

    def test_multiple_imports(self, result):
        assert len(result.imports) >= 2


class TestJavaParserTodos:
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


class TestJavaParserFileNode:
    def test_file_lang(self, result):
        assert result.file_node.lang == "java"

    def test_non_test_file(self, result):
        assert result.file_node.is_test is False

    def test_test_file_detection(self, parser):
        fake = FIXTURE / "AnimalTests.java"
        r = parser.parse(fake, b"public class AnimalTests {}", FIXTURE)
        assert r.file_node.is_test is True

    def test_tests_dir_detection(self, parser):
        fake = FIXTURE / "tests" / "AnimalIT.java"
        r = parser.parse(fake, b"public class AnimalIT {}", FIXTURE)
        assert r.file_node.is_test is True

    def test_line_count(self, result):
        src = ANIMALS_JAVA.read_text()
        assert result.file_node.line_count == len(src.splitlines())
