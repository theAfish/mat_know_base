"""Per-client registries for consumer-defined parsers and reusable steps."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mkb.exceptions import ConflictError, NotFoundError, ValidationError
from mkb.pipelines import Step
from mkb.ports import ContentParser

if TYPE_CHECKING:
    from mkb.sdk import KnowledgeBase

ParserHandler = Callable[["ParserContext", bytes], Any]


@dataclass(frozen=True)
class ParserContext:
    """Configured client and immutable metadata supplied to a parser."""

    knowledge_base: KnowledgeBase
    parser_name: str
    source_type: str
    parameters: Mapping[str, Any]


@dataclass(frozen=True)
class Parser:
    """Consumer-defined conversion from source bytes to a parsed value."""

    name: str
    handler: ParserHandler
    source_types: frozenset[str]
    version: str = "1"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValidationError("parser name must not be empty")
        if not callable(self.handler):
            raise ValidationError("parser handler must be callable")
        normalized = frozenset(item.strip().lower() for item in self.source_types if item.strip())
        if not normalized:
            raise ValidationError("parser must declare at least one source type")
        object.__setattr__(self, "source_types", normalized)


class Parsers:
    """Parser registry owned by exactly one ``KnowledgeBase`` instance."""

    def __init__(self, knowledge_base: KnowledgeBase):
        self._knowledge_base = knowledge_base
        self._registry: dict[str, Parser] = {}

    def register(self, parser: Parser, *, replace: bool = False) -> Parser:
        if parser.name in self._registry and not replace:
            raise ConflictError(f"Parser already registered: {parser.name}")
        self._registry[parser.name] = parser
        return parser

    def register_adapter(
        self,
        adapter: ContentParser,
        *,
        version: str = "1",
        replace: bool = False,
    ) -> Parser:
        """Register a parser port without coupling it to SDK context types."""
        parser = Parser(
            name=adapter.name,
            source_types=adapter.source_types,
            version=version,
            handler=lambda context, content: adapter.parse(
                content,
                parameters=dict(context.parameters),
            ),
        )
        return self.register(parser, replace=replace)

    def get(self, name: str) -> Parser | None:
        return self._registry.get(name)

    def require(self, name: str) -> Parser:
        parser = self.get(name)
        if parser is None:
            raise NotFoundError(f"Parser not found: {name}")
        return parser

    def list(self) -> list[Parser]:
        return [self._registry[name] for name in sorted(self._registry)]

    def for_source_type(self, source_type: str) -> list[Parser]:
        normalized = source_type.strip().lower()
        if not normalized:
            raise ValidationError("source_type must not be empty")
        return [parser for parser in self.list() if normalized in parser.source_types]

    def parse(
        self,
        parser: Parser | str,
        content: bytes,
        *,
        source_type: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> Any:
        definition = self.require(parser) if isinstance(parser, str) else parser
        normalized = source_type.strip().lower()
        if normalized not in definition.source_types:
            raise ValidationError(
                f"Parser {definition.name!r} does not support source type {source_type!r}"
            )
        if not isinstance(content, bytes):
            raise ValidationError("parser content must be bytes")
        context = ParserContext(
            knowledge_base=self._knowledge_base,
            parser_name=definition.name,
            source_type=normalized,
            parameters=dict(parameters or {}),
        )
        return definition.handler(context, content)


class Steps:
    """Per-client registry for reusable standalone pipeline steps."""

    def __init__(self) -> None:
        self._registry: dict[str, Step] = {}

    def register(self, step: Step, *, replace: bool = False) -> Step:
        if step.name in self._registry and not replace:
            raise ConflictError(f"Step already registered: {step.name}")
        self._registry[step.name] = step
        return step

    def get(self, name: str) -> Step | None:
        return self._registry.get(name)

    def require(self, name: str) -> Step:
        step = self.get(name)
        if step is None:
            raise NotFoundError(f"Step not found: {name}")
        return step

    def list(self) -> list[Step]:
        return [self._registry[name] for name in sorted(self._registry)]
