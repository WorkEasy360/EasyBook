"""Minimal pgvector support without a third-party package.

pgvector's official Python bindings pull in numpy for what is, for this
codebase, a text round-trip: pgvector accepts and returns vectors in the
documented text form `[1,2,3]`. A small field plus one distance expression
covers every use here (ai/CLAUDE.md "PGVECTOR").

The column is created dimension-less (`vector`) so the table can hold more
than one embedding model's vectors during a migration between models; the
0001 migration adds a CHECK tying `vector_dims(embedding)` to the row's
`embedding_dimensions`, and retrieval only ever compares vectors that share
one `embedding_config_key`.
"""

from django.db import models
from django.db.models import FloatField, Func, Value
from django.db.models.functions import Cast


def to_pgvector_text(values) -> str:
    return "[" + ",".join(repr(float(value)) for value in values) + "]"


def parse_pgvector_text(value: str) -> list[float]:
    body = value.strip()[1:-1]
    return [float(part) for part in body.split(",")] if body else []


class VectorField(models.Field):
    description = "pgvector vector"

    def db_type(self, connection):
        return "vector"

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        return parse_pgvector_text(value)

    def to_python(self, value):
        if value is None or isinstance(value, list):
            return value
        return parse_pgvector_text(value)

    def get_prep_value(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return to_pgvector_text(value)


class CosineDistance(Func):
    """`embedding <=> query::vector` — pgvector's cosine distance operator
    (0 = identical direction, 2 = opposite). The query vector is always a
    bound parameter cast to `vector`, never interpolated SQL."""

    arg_joiner = " <=> "
    template = "(%(expressions)s)"
    output_field = FloatField()

    def __init__(self, expression, query_vector):
        super().__init__(expression, Cast(Value(to_pgvector_text(query_vector)), VectorField()))
