from .base import *

DEBUG = True
ALLOWED_HOSTS = ["*"]

# Local development may exercise Ask Books on the deterministic fake
# providers without credentials; production must configure a real provider.
AI_ASK_BOOKS_ENABLED = env.bool("AI_ASK_BOOKS_ENABLED", default=True)
AI_ALLOW_FAKE_PROVIDERS = env.bool("AI_ALLOW_FAKE_PROVIDERS", default=True)
