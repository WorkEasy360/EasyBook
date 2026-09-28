"""Database-level enforcement of posted-journal immutability and balance.

The model guards (JournalEntry.save/delete, JournalLine.save/delete) only run
when code goes through a model instance. Queryset update()/delete(),
bulk_create() and raw SQL never call them — and a stale in-memory journal
once let replace_draft_lines rewrite a posted journal's lines into an
unbalanced state. These triggers make the invariants hold for every writer:

1. A line may be inserted, updated or deleted only while its journal is a
   DRAFT (checked for both the old and the new parent on update).
2. A journal that has left DRAFT may change only by POSTED -> REVERSED (plus
   `updated_at`, and the author FKs being nulled by ON DELETE SET NULL). It
   can never go back to DRAFT, be re-dated, re-numbered, re-currencied, or be
   deleted.
3. A journal can only enter POSTED/REVERSED (by insert or by update from
   DRAFT) if it has at least two lines, base debits == base credits, and a
   nonzero total — the same rule post_journal applies, now also enforced
   below the application.

Together, (1) and (3) mean every non-draft journal is balanced forever.

Both functions run as the invoking role, so they see rows through the same
RLS policy as the statement that fired them; a parent row that is not visible
(or does not exist) reads as NULL and is refused — fail closed.
"""

from django.db import migrations

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION accounting_journalline_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    parent_status text;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT status INTO parent_status FROM accounting_journalentry WHERE id = OLD.journal_entry_id;
        IF parent_status IS DISTINCT FROM 'draft' THEN
            RAISE EXCEPTION 'journal_line_immutable: lines of a % journal entry cannot be changed',
                COALESCE(parent_status, 'unknown')
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        SELECT status INTO parent_status FROM accounting_journalentry WHERE id = NEW.journal_entry_id;
        IF parent_status IS DISTINCT FROM 'draft' THEN
            RAISE EXCEPTION 'journal_line_immutable: lines of a % journal entry cannot be changed',
                COALESCE(parent_status, 'unknown')
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    RETURN OLD;
END;
$$;

CREATE TRIGGER accounting_journalline_guard
    BEFORE INSERT OR UPDATE OR DELETE ON accounting_journalline
    FOR EACH ROW EXECUTE FUNCTION accounting_journalline_guard();

CREATE OR REPLACE FUNCTION accounting_journalentry_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    line_count integer;
    total_debit numeric;
    total_credit numeric;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'draft' THEN
            RAISE EXCEPTION 'journal_immutable: a % journal entry cannot be deleted', OLD.status
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN OLD;
    END IF;

    IF TG_OP = 'UPDATE' AND OLD.status <> 'draft' THEN
        IF NOT (NEW.status = OLD.status OR (OLD.status = 'posted' AND NEW.status = 'reversed')) THEN
            RAISE EXCEPTION 'journal_immutable: a journal entry cannot move from % to %', OLD.status, NEW.status
                USING ERRCODE = 'check_violation';
        END IF;
        IF (NEW.organization_id, NEW.journal_number, NEW.reference, NEW.posting_date, NEW.memo,
            NEW.source_type, NEW.source_id, NEW.currency_id, NEW.exchange_rate, NEW.fiscal_year_id,
            NEW.posted_at, NEW.reverses_id, NEW.created_at)
           IS DISTINCT FROM
           (OLD.organization_id, OLD.journal_number, OLD.reference, OLD.posting_date, OLD.memo,
            OLD.source_type, OLD.source_id, OLD.currency_id, OLD.exchange_rate, OLD.fiscal_year_id,
            OLD.posted_at, OLD.reverses_id, OLD.created_at) THEN
            RAISE EXCEPTION 'journal_immutable: a % journal entry cannot be modified', OLD.status
                USING ERRCODE = 'check_violation';
        END IF;
        -- Only the author references may change, and only to NULL
        -- (ON DELETE SET NULL when a user is removed).
        IF (NEW.created_by_id IS DISTINCT FROM OLD.created_by_id AND NEW.created_by_id IS NOT NULL)
           OR (NEW.posted_by_id IS DISTINCT FROM OLD.posted_by_id AND NEW.posted_by_id IS NOT NULL) THEN
            RAISE EXCEPTION 'journal_immutable: a % journal entry cannot be re-attributed', OLD.status
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    -- INSERT, or UPDATE of a DRAFT: entering a ledger status requires balance.
    IF NEW.status <> 'draft' THEN
        SELECT count(*), COALESCE(sum(base_debit), 0), COALESCE(sum(base_credit), 0)
          INTO line_count, total_debit, total_credit
          FROM accounting_journalline
         WHERE journal_entry_id = NEW.id;
        IF line_count < 2 OR total_debit <> total_credit OR total_debit <= 0 THEN
            RAISE EXCEPTION 'journal_unbalanced: journal entry % cannot enter status % (lines=%, debit=%, credit=%)',
                NEW.id, NEW.status, line_count, total_debit, total_credit
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER accounting_journalentry_guard
    BEFORE INSERT OR UPDATE OR DELETE ON accounting_journalentry
    FOR EACH ROW EXECUTE FUNCTION accounting_journalentry_guard();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS accounting_journalentry_guard ON accounting_journalentry;
DROP FUNCTION IF EXISTS accounting_journalentry_guard();
DROP TRIGGER IF EXISTS accounting_journalline_guard ON accounting_journalline;
DROP FUNCTION IF EXISTS accounting_journalline_guard();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0004_enable_rls"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
