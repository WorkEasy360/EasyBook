"""Generates src/lib/authz/permissions.ts from backend/authz/roles.py.

The backend's role catalog is code-defined, not database rows, so the
frontend can mirror it exactly rather than guessing. Generating it means the
mirror cannot silently drift from the source when a permission is added.

The mirror is a UI HINT ONLY. Every request is still authorized server-side
by authz.permissions.HasOrgPermission — see the header written into the
generated file.

Run from backend/ with the project venv:
    .venv/Scripts/python.exe ../frontend/scripts/generate-permissions.py
"""

import pathlib
import sys

# Run from anywhere: put backend/ (this file's ../../backend) on the path so
# `authz` imports regardless of the caller's working directory.
BACKEND = pathlib.Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

import django
from django.conf import settings

settings.configure(INSTALLED_APPS=[], DATABASES={})
django.setup()

from authz.roles import ROLE_PERMISSIONS, Permission, Role  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parents[1] / "src" / "lib" / "authz" / "permissions.ts"

constants = sorted(
    ((name, value) for name, value in vars(Permission).items() if not name.startswith("_")),
    key=lambda pair: pair[1],
)

lines: list[str] = []
lines.append("/**")
lines.append(" * GENERATED FILE — do not edit by hand.")
lines.append(" * Source: backend/authz/roles.py")
lines.append(" * Regenerate: cd backend && .venv/Scripts/python.exe \\")
lines.append(" *   ../frontend/scripts/generate-permissions.py")
lines.append(" *")
lines.append(" * This is a UI HINT ONLY (spec §62). It decides what to hide or disable so")
lines.append(" * the interface does not offer actions that will 403. It is NOT a security")
lines.append(" * boundary: authz.permissions.HasOrgPermission re-checks every request")
lines.append(" * server-side, and a 403 arriving anyway is handled as a normal outcome.")
lines.append(" *")
lines.append(" * Mirroring is safe here because the backend catalog is a closed set of code")
lines.append(" * constants that only changes on deploy — not per-organization rows. If the")
lines.append(" * backend ever gains custom roles, delete this file and read an authoritative")
lines.append(" * permission list off the session endpoint instead.")
lines.append(" */")
lines.append("")

lines.append("export const ROLES = [")
for role in Role:
    lines.append(f'  "{role.value}",')
lines.append("] as const;")
lines.append("")
lines.append("export type Role = (typeof ROLES)[number];")
lines.append("")

lines.append("export const ROLE_LABELS: Record<Role, string> = {")
for role in Role:
    lines.append(f'  "{role.value}": "{role.label}",')
lines.append("};")
lines.append("")

lines.append("export const PERMISSIONS = {")
for name, value in constants:
    lines.append(f'  {name}: "{value}",')
lines.append("} as const;")
lines.append("")
lines.append("export type Permission = (typeof PERMISSIONS)[keyof typeof PERMISSIONS];")
lines.append("")

lines.append("const ROLE_PERMISSIONS: Record<Role, ReadonlySet<Permission>> = {")
for role in Role:
    granted = sorted(ROLE_PERMISSIONS.get(role, set()))
    lines.append(f'  "{role.value}": new Set([')
    for value in granted:
        lines.append(f'    "{value}",')
    lines.append("  ]),")
lines.append("};")
lines.append("")

lines.append('''/** Does this role grant this permission? Unknown roles grant nothing. */
export function roleHasPermission(role: Role | string | null | undefined, permission: Permission): boolean {
  if (!role) return false;
  const granted = ROLE_PERMISSIONS[role as Role];
  return granted ? granted.has(permission) : false;
}

/** True only when every listed permission is granted. */
export function roleHasAll(role: Role | string | null | undefined, permissions: readonly Permission[]): boolean {
  return permissions.every((permission) => roleHasPermission(role, permission));
}

/** True when at least one is granted — for a nav section covering several pages. */
export function roleHasAny(role: Role | string | null | undefined, permissions: readonly Permission[]): boolean {
  return permissions.some((permission) => roleHasPermission(role, permission));
}

export function permissionsForRole(role: Role | string | null | undefined): Permission[] {
  if (!role) return [];
  const granted = ROLE_PERMISSIONS[role as Role];
  return granted ? [...granted].sort() : [];
}''')
lines.append("")

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(lines), encoding="utf-8")
sys.stdout.write(f"wrote {OUT} ({len(constants)} permissions, {len(Role)} roles)\n")
