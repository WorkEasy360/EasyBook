# Local shortcuts for the checks CI runs. `make check` is the gate.
PY := .venv/Scripts/python.exe

# Local dev server. 8001, not Django's default 8000: another project on this
# machine already holds 8000, the same reason Postgres/Redis are remapped to
# 5472/6390 in infrastructure/docker-compose.yml. Keep this in step with
# frontend/.env.example's API_BASE_URL.
RUN_PORT ?= 8001

.PHONY: lint lint-fix security audit test check run

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

# Postgres/Redis must be up first: `docker compose -f
# ../infrastructure/docker-compose.yml up -d`. They restart with Docker, so
# this is normally a one-time step per machine, not per boot.
run:
	$(PY) manage.py runserver 127.0.0.1:$(RUN_PORT)
