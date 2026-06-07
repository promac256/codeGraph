"""Tests for the Rust language parser."""

from __future__ import annotations

import pytest
from pathlib import Path

from codegraph.parsers.rust_parser import RustParser

FIXTURE = Path(__file__).parent / "fixtures" / "rust_sample"
ANIMALS_RS = FIXTURE / "animals.rs"

try:
    import tree_sitter_rust  # noqa: F401
    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _RUST_AVAILABLE, reason="tree-sitter-rust not installed")


@pytest.fixture()
def parser() -> RustParser:
    return RustParser()


@pytest.fixture()
def result(parser):
    return parser.parse(ANIMALS_RS, ANIMALS_RS.read_bytes(), FIXTURE)


class TestRustParserStructs:
    def test_parses_structs(self, result):
        names = [c.name for c in result.classes]
        assert "Animal" in names
        assert "Dog" in names
        assert "Cat" in names

    def test_exported_structs(self, result):
        animal = next(c for c in result.classes if c.name == "Animal")
        assert animal.is_exported is True

    def test_struct_line_numbers(self, result):
        dog = next(c for c in result.classes if c.name == "Dog")
        assert dog.line_start > 0
        assert dog.line_end >= dog.line_start

    def test_struct_docstring(self, result):
        animal = next(c for c in result.classes if c.name == "Animal")
        assert animal.docstring is not None
        assert "animal" in animal.docstring.lower()

    def test_struct_defines_edge(self, result):
        from codegraph.models import EdgeKind
        from codegraph.utils.hashing import make_class_id, make_file_id
        class_id = make_class_id("animals.rs", "Animal")
        file_id = "file:animals.rs"
        edge = next(
            (e for e in result.defines if e.src == file_id and e.dst == class_id), None
        )
        assert edge is not None
        assert edge.kind == EdgeKind.DEFINES


class TestRustParserEnums:
    def test_parses_enum(self, result):
        names = [c.name for c in result.classes]
        assert "AdoptionStatus" in names

    def test_enum_exported(self, result):
        status = next(c for c in result.classes if c.name == "AdoptionStatus")
        assert status.is_exported is True

    def test_enum_docstring(self, result):
        status = next(c for c in result.classes if c.name == "AdoptionStatus")
        assert status.docstring is not None


class TestRustParserTraits:
    def test_parses_trait(self, result):
        names = [t.name for t in result.types]
        assert "Speaker" in names

    def test_trait_exported(self, result):
        speaker = next(t for t in result.types if t.name == "Speaker")
        assert speaker.is_exported is True

    def test_trait_has_definition(self, result):
        speaker = next(t for t in result.types if t.name == "Speaker")
        assert speaker.definition is not None and len(speaker.definition) > 0


class TestRustParserTypeAliases:
    def test_parses_type_alias(self, result):
        names = [t.name for t in result.types]
        assert "AnimalId" in names

    def test_type_alias_exported(self, result):
        alias = next(t for t in result.types if t.name == "AnimalId")
        assert alias.is_exported is True


class TestRustParserFreeFunctions:
    def test_parses_free_functions(self, result):
        names = [f.name for f in result.functions]
        assert "create_animal" in names
        assert "index_by_name" in names

    def test_free_function_qualified_name(self, result):
        create = next(f for f in result.functions if f.name == "create_animal")
        assert create.qualified_name == "create_animal"

    def test_free_function_exported(self, result):
        create = next(f for f in result.functions if f.name == "create_animal")
        assert create.is_exported is True

    def test_complexity_match(self, result):
        create = next(f for f in result.functions if f.name == "create_animal")
        # match with multiple arms → complexity > 1
        assert (create.complexity or 1) > 1

    def test_function_signature(self, result):
        create = next(f for f in result.functions if f.name == "create_animal")
        assert "create_animal" in (create.signature or "")
        assert "kind" in (create.signature or "")


class TestRustParserMethods:
    def test_parses_inherent_methods(self, result):
        names = [f.name for f in result.functions]
        assert "new" in names
        assert "display_name" in names
        assert "name" in names

    def test_method_qualified_name(self, result):
        # Animal::new should be "Animal.new"
        animal_new = next(
            (f for f in result.functions if f.qualified_name == "Animal.new"), None
        )
        assert animal_new is not None

    def test_async_method(self, result):
        learn = next(
            (f for f in result.functions if f.name == "learn_trick"), None
        )
        assert learn is not None
        assert learn.is_async is True

    def test_non_async_method(self, result):
        new_fn = next(
            (f for f in result.functions if f.qualified_name == "Dog.new"), None
        )
        assert new_fn is not None
        assert new_fn.is_async is False

    def test_method_defines_edge_from_class(self, result):
        from codegraph.models import EdgeKind
        from codegraph.utils.hashing import make_class_id, make_func_id
        class_id = make_class_id("animals.rs", "Animal")
        func_id = make_func_id("animals.rs", "Animal.new")
        edge = next(
            (e for e in result.defines if e.src == class_id and e.dst == func_id), None
        )
        assert edge is not None
        assert edge.kind == EdgeKind.DEFINES

    def test_trait_impl_methods(self, result):
        # Dog implements Speaker → speak and describe should be parsed
        dog_speak = next(
            (f for f in result.functions if f.qualified_name == "Dog.speak"), None
        )
        assert dog_speak is not None

    def test_trait_impl_creates_implements_edge(self, result):
        from codegraph.models import EdgeKind
        from codegraph.utils.hashing import make_class_id, make_type_id
        class_id = make_class_id("animals.rs", "Dog")
        trait_id = make_type_id("animals.rs", "Speaker")
        edge = next(
            (e for e in result.exports if e.src == class_id and e.dst == trait_id), None
        )
        assert edge is not None
        assert edge.kind == EdgeKind.IMPLEMENTS


class TestRustParserImports:
    def test_parses_imports(self, result):
        modules = [e.meta.get("module") for e in result.imports]
        assert "std::fmt" in modules

    def test_import_dst_prefixed(self, result):
        for edge in result.imports:
            assert edge.dst.startswith("module:")

    def test_std_import_not_relative(self, result):
        fmt_import = next(e for e in result.imports if "fmt" in e.meta.get("module", ""))
        assert fmt_import.meta.get("is_relative") is False

    def test_multiple_imports(self, result):
        modules = [e.meta.get("module") for e in result.imports]
        assert len(modules) >= 2


class TestRustParserTodos:
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


class TestRustParserFileNode:
    def test_file_lang(self, result):
        assert result.file_node.lang == "rust"

    def test_non_test_file(self, result):
        assert result.file_node.is_test is False

    def test_test_file_detection(self, parser):
        fake = FIXTURE / "shelter_test.rs"
        r = parser.parse(fake, b"fn test_foo() {}", FIXTURE)
        assert r.file_node.is_test is True

    def test_tests_dir_detection(self, parser):
        fake = FIXTURE / "tests" / "integration.rs"
        r = parser.parse(fake, b"fn test_bar() {}", FIXTURE)
        assert r.file_node.is_test is True

    def test_line_count(self, result):
        src = ANIMALS_RS.read_text()
        assert result.file_node.line_count == len(src.splitlines())
