# Local shortcuts for the checks CI runs. `make check` is the gate.
PY := .venv/Scripts/python.exe

# Local dev server. 8001, not Django's default 8000: another project on this
# machine already holds 8000, the same reason Postgres/Redis are remapped to
# 5472/6390 in infrastructure/docker-compose.yml. Keep this in step with
# frontend/.env.example's API_BASE_URL.
RUN_PORT ?= 8001

.PHONY: install lint lint-fix security audit test check run

# Installs requirements.txt the way CI and the Dockerfile do. The private
# WorkEasy360 sign-in connector comes from git over SSH, so this needs your
# own GitHub SSH key with read access to WorkEasy360/workeasy360-sso
# (docs/workeasy360-sso.md, "Installing the connector").
install:
	$(PY) -m pip install --no-deps --build-constraint requirements-build.txt -r requirements.txt
	$(PY) -m pip check

lint:
	$(PY) -m ruff check .

lint-fix:
	$(PY) -m ruff check . --fix

security:
	$(PY) -m bandit -c pyproject.toml -r . -q

# As in CI: requirements.txt's pins as written, without installing them (the
# connector's git line is skipped; .github/workflows/backend-ci.yml says why).
audit:
	$(PY) -m pip_audit -r requirements.txt --no-deps --disable-pip --progress-spinner off

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
