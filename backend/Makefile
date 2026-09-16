# Local shortcuts for the checks CI runs. `make check` is the gate.
PY := .venv/Scripts/python.exe

.PHONY: lint lint-fix security audit test check

lint:
	$(PY) -m ruff check .

lint-fix:
	$(PY) -m ruff check . --fix

security:
	$(PY) -m bandit -c pyproject.toml -r . -q

audit:
	$(PY) -m pip_audit -r requirements.txt --progress-spinner off

test:
	$(PY) manage.py test --settings=config.settings.test

# Migration drift is a gate too: a model change without a migration passes
# every other check and then fails on deploy.
migrations:
	$(PY) manage.py makemigrations --check --dry-run --settings=config.settings.dev

check: lint security audit migrations test
