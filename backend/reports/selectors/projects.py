"""Project reports (PHASE 8 spec §15).

Projects/Timesheets (Phase 5) is complete, so this report is built — see
projects/CLAUDE.md. It is a pure fan-out over the organization's projects,
calling `projects.selectors.get_project_profitability` per project: that
selector already computes revenue (from invoice lines), labour cost, expense
cost, margin, and every hours split (billable/non-billable/approved/
invoiced) this phase's spec asks for. Nothing here recomputes any of that —
duplicating it would risk a second, drifting definition of "revenue" or
"margin" for exactly the figures projects/CLAUDE.md is most careful about.
"""

from projects.selectors import get_project_profitability


def get_project_profitability_report(*, organization, status=None) -> list[dict]:
    from projects.models.project import Project

    projects = Project.objects.filter(organization=organization)
    if status:
        projects = projects.filter(status=status)
    return [get_project_profitability(project=project) for project in projects.order_by("project_code")]
