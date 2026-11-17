from .base import *

DEBUG = True
ALLOWED_HOSTS = ["*"]

# Local development may exercise Ask Books on the deterministic fake
# providers without credentials; production must configure a real provider.
AI_ASK_BOOKS_ENABLED = env.bool("AI_ASK_BOOKS_ENABLED", default=True)
AI_ALLOW_FAKE_PROVIDERS = env.bool("AI_ALLOW_FAKE_PROVIDERS", default=True)

# Password sign-in stays available locally so development needs no running
# Workeasy360; set WORKEASY360_SSO_ONLY=1 to rehearse production behaviour.
WORKEASY360_SSO_ONLY = env.bool("WORKEASY360_SSO_ONLY", default=False)
# A local Workeasy360 usually answers on http://localhost: the connector
# accepts an http issuer and redirect URI on a loopback host only with this on.
# Production refuses it (config/settings/preflight.py).
WORKEASY360_ALLOW_INSECURE_HTTP = env.bool("WORKEASY360_ALLOW_INSECURE_HTTP", default=True)
