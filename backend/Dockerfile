# syntax=docker/dockerfile:1
#
# One image, every backend ECS service (phase 12 section 11): api, worker-critical,
# worker-default, worker-heavy, worker-ai, beat. This file's CMD is the "api"
# service's command; each other service overrides `command` in its own ECS
# task definition to run `celery -A config worker ...` / `celery -A config beat ...`
# against the same image instead. See infrastructure/CLAUDE.md.
#
# Base image pinned by digest, not just tag — a tag can be repointed, which is
# a supply-chain hole in an image that runs a financial codebase (same reason
# .github/workflows/backend-ci.yml pins actions to commit SHAs). Verified
# 2026-09-16 against the official python Docker Hub image
# (python:3.10.21-slim-bookworm, linux/amd64).
ARG PYTHON_IMAGE=python:3.10.21-slim-bookworm@sha256:68d914ec641a0b69267ce65184d000a2bc3a9ee2590ab702b82250ab2385735a

FROM ${PYTHON_IMAGE} AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# requirements.txt only — requirements-dev.txt (ruff/bandit/pip-audit) never
# ships in a production image, so a runtime container never carries a linter
# or the tools that scan for one (backend/CLAUDE.md "QUALITY GATE").
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
    && /opt/venv/bin/pip install -r requirements.txt

FROM ${PYTHON_IMAGE} AS runtime

# OS security patches (found by actually running `trivy image` against this
# Dockerfile, not by inspection: the base image ships an unpatched libpcre2
# with two known HIGH CVEs) plus the base image's own system-wide pip/
# setuptools/wheel, which the running app never imports (PATH below always
# resolves `python` to /opt/venv, not this one) but which trivy still flags
# as present-and-vulnerable on disk — cheaper to patch than to argue with a
# scanner about dead weight. ensurepip's bundled offline-bootstrap wheels
# (an old pip that in turn vendors an old, HIGH-CVE msgpack) are removed
# outright rather than patched: `python -m venv` already consumed them to
# seed /opt/venv at the layer above, nothing in this image ever runs
# `ensurepip` again, and the files cannot be upgraded in place since they are
# frozen inside CPython's own stdlib distribution.
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && rm -rf /usr/local/lib/python3.10/ensurepip/_bundled

# Non-root runtime user (phase 12 section 12) — a fixed high UID/GID rather
# than a dynamically allocated one, so ECS/Fargate task file ownership stays
# reproducible across builds. --create-home (not --no-create-home): gunicorn
# 26's control server writes state under $HOME and errors without one —
# found by actually running the built image, not by inspection.
RUN groupadd --gid 10001 easybook \
    && useradd --uid 10001 --gid easybook --create-home --shell /usr/sbin/nologin easybook

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY . .

# config.settings.dev only for this build-time step, deliberately never
# config.settings.production: collectstatic touches STATIC_ROOT/STATICFILES
# only, never a real secret, and production's own settings module fails
# closed (config/settings/preflight.py) without real prod values that must
# never be baked into an image layer. The runtime process below always
# launches under config.settings.production regardless (DJANGO_SETTINGS_MODULE
# above), so this choice affects nothing about what actually serves traffic.
RUN DJANGO_SETTINGS_MODULE=config.settings.dev python manage.py collectstatic --noinput \
    && chown -R easybook:easybook /app

USER easybook

EXPOSE 8000

# Liveness-only, dependency-free by construction (core/views.py:LivenessCheckView,
# core/urls.py's non_atomic_requests wrapping) — a database or Redis blip must
# never fail this and trigger an unnecessary container replacement (phase 12
# section 46).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live/', timeout=3).status == 200 else 1)"

# ASGI per root CLAUDE.md's stack, via Django's own currently-documented
# production recipe (docs.djangoproject.com/en/5.2/howto/deployment/asgi/uvicorn/,
# verified 2026-09-16): gunicorn as the process manager (monitoring + restarts)
# running the uvicorn_worker.UvicornWorker worker class — not the older
# uvicorn.workers.UvicornWorker, which moved out of uvicorn core into the
# separate uvicorn-worker package this image installs.
CMD ["gunicorn", "config.asgi:application", \
     "-k", "uvicorn_worker.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
