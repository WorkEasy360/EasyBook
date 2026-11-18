"""Mirror every existing ExternalIdentity into the workeasy360-sso connector's FederatedIdentity.

Additive only: ExternalIdentity rows are read, never changed, moved or deleted, and stay the authoritative
(issuer, sub) -> user link. The connector's back-channel session records point at FederatedIdentity, so every
person who signed in before the connector arrived gets one. Rows that already exist (a re-run, a sign-in that
got there first) are left alone; a mirror row that disagrees is corrected by the next sign-in
(sso.provisioning.sync_federated_identity), never here. Reversing it deletes nothing: the previous release
never reads the connector's tables.
"""

from django.db import migrations

BATCH = 500


def mirror_identities(apps, schema_editor):
    ExternalIdentity = apps.get_model("sso", "ExternalIdentity")
    FederatedIdentity = apps.get_model("workeasy360_sso", "FederatedIdentity")
    known = set(FederatedIdentity.objects.values_list("issuer", "subject"))
    linked = set(FederatedIdentity.objects.values_list("user_id", "issuer"))
    batch = []
    for row in ExternalIdentity.objects.select_related("user").order_by("created_at", "id").iterator(chunk_size=BATCH):
        if (row.issuer, row.subject) in known or (row.user_id, row.issuer) in linked:
            continue
        known.add((row.issuer, row.subject))
        linked.add((row.user_id, row.issuer))
        batch.append(
            FederatedIdentity(
                user_id=row.user_id,
                issuer=row.issuer,
                subject=row.subject,
                status="active",
                email_at_link=(row.user.email or "")[:254],
                linked_at=row.created_at,
                last_sign_in_at=row.last_login_at,
            )
        )
        if len(batch) >= BATCH:
            FederatedIdentity.objects.bulk_create(batch, ignore_conflicts=True)
            batch = []
    if batch:
        FederatedIdentity.objects.bulk_create(batch, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [
        ("sso", "0001_initial"),
        ("workeasy360_sso", "0003_workeasy360_branding"),
    ]

    operations = [
        migrations.RunPython(mirror_identities, migrations.RunPython.noop, elidable=False),
    ]
