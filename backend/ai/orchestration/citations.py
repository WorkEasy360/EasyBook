"""Citation validation (phase sections 27/69/80).

The SourceRegistry holds exactly the sources the backend produced for this
request: sources of tool results that succeeded, and chunks actually placed
in the model's context. A model-supplied citation is kept only if its
source_id is in the registry. Source-id-looking strings embedded in the
answer text that are NOT in the registry are removed from the text. Because
every tool and retrieval ran under the user's permissions and tenant, an
unauthorized source can never be in the registry, so it can never be cited.
"""

import re

from ai.sources import Source

_INLINE_SOURCE_ID = re.compile(r"\[?\b(?:report|invoice|bill|chunk):[A-Za-z0-9:._-]+\]?")


class SourceRegistry:
    def __init__(self):
        self._sources: dict[str, Source] = {}

    def add(self, source: Source) -> None:
        self._sources.setdefault(source.source_id, source)

    def get(self, source_id) -> Source | None:
        return self._sources.get(source_id) if isinstance(source_id, str) else None

    def all(self) -> list[Source]:
        return list(self._sources.values())

    def __contains__(self, source_id) -> bool:
        return self.get(source_id) is not None

    def __len__(self) -> int:
        return len(self._sources)


def validate_citations(cited, registry: SourceRegistry) -> tuple[list[Source], list[str]]:
    valid, rejected, seen = [], [], set()
    for source_id in cited if isinstance(cited, list) else []:
        if not isinstance(source_id, str):
            rejected.append(repr(source_id)[:80])
            continue
        if source_id in seen:
            continue
        seen.add(source_id)
        source = registry.get(source_id)
        if source is None:
            rejected.append(source_id[:120])
        else:
            valid.append(source)
    return valid, rejected


def strip_unknown_inline_ids(answer: str, registry: SourceRegistry) -> tuple[str, list[str]]:
    removed = []

    def replace(match):
        token = match.group(0).strip("[]")
        if token in registry:
            return match.group(0)
        removed.append(token[:120])
        return ""

    return _INLINE_SOURCE_ID.sub(replace, answer), removed
