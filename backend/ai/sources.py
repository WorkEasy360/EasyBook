"""The one response source/citation schema (phase sections 27/28).

A `Source` is only ever constructed by backend code from something the
backend actually executed or retrieved for THIS request — a tool result or
an authorized chunk. The model never creates one; it can only refer to a
`source_id` from the per-request registry, and anything else is rejected
(ai/orchestration/citations.py).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    source_id: str
    type: str  # report | invoice | bill | document
    label: str
    id: str | None = None
    route: str | None = None
    document_id: str | None = None
    page: int | None = None
    section: str | None = None

    def to_public(self) -> dict:
        return {
            "source_id": self.source_id,
            "type": self.type,
            "id": self.id,
            "label": self.label,
            "route": self.route,
            "document_id": self.document_id,
            "page": self.page,
            "section": self.section or None,
        }


def report_source(*, report: str, label: str, route: str | None, key: str) -> Source:
    return Source(source_id=f"report:{report}:{key}", type="report", id=report, label=label, route=route)
