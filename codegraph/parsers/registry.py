"""Parser registry: maps file extensions to language parsers."""

from __future__ import annotations

from pathlib import Path

from codegraph.parsers.base import LanguageParser
from codegraph.parsers.generic_parser import GenericParser


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers: list[LanguageParser] = []
        self._fallback = GenericParser()

    def register(self, parser: LanguageParser) -> None:
        self._parsers.append(parser)

    def get_parser(self, path: Path) -> LanguageParser | None:
        for parser in self._parsers:
            if parser.can_parse(path):
                return parser
        return self._fallback

    @classmethod
    def default(cls) -> "ParserRegistry":
        registry = cls()

        # Python
        try:
            from codegraph.parsers.python_parser import PythonParser
            registry.register(PythonParser())
        except ImportError:
            pass

        # TypeScript / JavaScript
        try:
            from codegraph.parsers.typescript_parser import TypeScriptParser
            registry.register(TypeScriptParser())
        except ImportError:
            pass

        return registry
