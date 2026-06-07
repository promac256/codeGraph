"""Tests for the Go language parser."""

from __future__ import annotations

import pytest
from pathlib import Path

from codegraph.parsers.go_parser import GoParser

FIXTURE = Path(__file__).parent / "fixtures" / "go_sample"
MODELS_GO = FIXTURE / "models.go"
SERVER_GO = FIXTURE / "server.go"

try:
    import tree_sitter_go  # noqa: F401
    _GO_AVAILABLE = True
except ImportError:
    _GO_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _GO_AVAILABLE, reason="tree-sitter-go not installed")


@pytest.fixture()
def parser() -> GoParser:
    return GoParser()


@pytest.fixture()
def models_result(parser):
    return parser.parse(MODELS_GO, MODELS_GO.read_bytes(), FIXTURE)


@pytest.fixture()
def server_result(parser):
    return parser.parse(SERVER_GO, SERVER_GO.read_bytes(), FIXTURE)


class TestGoParserStructs:
    def test_parses_plain_struct(self, models_result):
        names = [c.name for c in models_result.classes]
        assert "Animal" in names
        assert "Shelter" in names

    def test_parses_embedded_struct_bases(self, models_result):
        dog = next(c for c in models_result.classes if c.name == "Dog")
        assert "Animal" in dog.bases

        cat = next(c for c in models_result.classes if c.name == "Cat")
        assert "Animal" in cat.bases

    def test_embedded_generates_inherits_edges(self, models_result):
        edge_dsts = [e.dst for e in models_result.inherits]
        assert "class:?::Animal" in edge_dsts

    def test_exported_flag(self, models_result):
        animal = next(c for c in models_result.classes if c.name == "Animal")
        assert animal.is_exported is True

    def test_struct_line_numbers(self, models_result):
        shelter = next(c for c in models_result.classes if c.name == "Shelter")
        assert shelter.line_start > 0
        assert shelter.line_end >= shelter.line_start


class TestGoParserInterfaces:
    def test_parses_interface(self, models_result):
        names = [t.name for t in models_result.types]
        assert "Speaker" in names

    def test_interface_is_exported(self, models_result):
        speaker = next(t for t in models_result.types if t.name == "Speaker")
        assert speaker.is_exported is True


class TestGoParserTypeAliases:
    def test_parses_type_alias(self, models_result):
        names = [t.name for t in models_result.types]
        assert "AdoptionStatus" in names

    def test_type_alias_not_struct_or_interface(self, models_result):
        status = next(t for t in models_result.types if t.name == "AdoptionStatus")
        assert status.definition is not None


class TestGoParserFunctions:
    def test_parses_top_level_function(self, models_result):
        names = [f.name for f in models_result.functions]
        assert "NewShelter" in names
        assert "CreateAnimal" in names

    def test_function_qualified_name(self, models_result):
        new_shelter = next(f for f in models_result.functions if f.name == "NewShelter")
        assert new_shelter.qualified_name == "NewShelter"

    def test_function_signature(self, models_result):
        new_shelter = next(f for f in models_result.functions if f.name == "NewShelter")
        assert "NewShelter" in (new_shelter.signature or "")
        assert "name" in (new_shelter.signature or "")

    def test_is_not_async(self, models_result):
        for fn in models_result.functions:
            assert fn.is_async is False

    def test_complexity_branching(self, models_result):
        create = next(f for f in models_result.functions if f.name == "CreateAnimal")
        # switch with 3 cases → complexity > 1
        assert (create.complexity or 1) > 1


class TestGoParserMethods:
    def test_parses_methods_with_receiver(self, models_result):
        names = [f.name for f in models_result.functions]
        assert "Speak" in names
        assert "Describe" in names
        assert "AddAnimal" in names
        assert "FindByName" in names
        assert "Count" in names

    def test_method_qualified_name(self, models_result):
        dog_speak = next(
            f for f in models_result.functions
            if f.qualified_name == "Dog.Speak"
        )
        assert dog_speak.name == "Speak"

    def test_pointer_receiver_stripped(self, models_result):
        # Shelter methods use *Shelter receiver — type should be "Shelter" not "*Shelter"
        shelter_methods = [f for f in models_result.functions if f.qualified_name.startswith("Shelter.")]
        assert len(shelter_methods) >= 3

    def test_method_defines_edge_from_class(self, models_result):
        # Methods should have a DEFINES edge whose src is the class node id
        from codegraph.models import EdgeKind
        from codegraph.utils.hashing import make_class_id, make_func_id

        rel = "models.go"
        class_id = make_class_id(rel, "Dog")
        func_id = make_func_id(rel, "Dog.Speak")
        edge = next(
            (e for e in models_result.defines if e.src == class_id and e.dst == func_id),
            None,
        )
        assert edge is not None
        assert edge.kind == EdgeKind.DEFINES


class TestGoParserImports:
    def test_parses_imports(self, models_result):
        import_modules = [e.meta.get("module") for e in models_result.imports]
        assert "fmt" in import_modules
        assert "time" in import_modules

    def test_import_dst_prefixed(self, models_result):
        for edge in models_result.imports:
            assert edge.dst.startswith("module:")

    def test_server_imports(self, server_result):
        modules = [e.meta.get("module") for e in server_result.imports]
        assert "encoding/json" in modules
        assert "net/http" in modules


class TestGoParserTodos:
    def test_extracts_todo(self, models_result):
        kinds = [t["kind"] for t in models_result.todos]
        assert "TODO" in kinds

    def test_extracts_fixme(self, models_result):
        kinds = [t["kind"] for t in models_result.todos]
        assert "FIXME" in kinds

    def test_extracts_note_from_server(self, server_result):
        kinds = [t["kind"] for t in server_result.todos]
        assert "NOTE" in kinds


class TestGoParserFileNode:
    def test_file_node_lang(self, models_result):
        assert models_result.file_node.lang == "go"

    def test_test_file_detection(self, parser):
        # _test.go suffix should set is_test=True
        fake_path = FIXTURE / "shelter_test.go"
        source = b"package store\n"
        # We just need a parseable file; create a minimal stub if needed
        result = parser.parse(
            Path(str(FIXTURE / "shelter_test.go")),
            b"package store\n",
            FIXTURE,
        )
        assert result.file_node.is_test is True

    def test_non_test_file(self, models_result):
        assert models_result.file_node.is_test is False

    def test_line_count(self, models_result):
        src = MODELS_GO.read_text()
        assert models_result.file_node.line_count == len(src.splitlines())
