import hashlib
import json

from rest_framework.response import Response

from core.models import IdempotencyKey


def _hash_body(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


class IdempotentCreateMixin:
    """Mixin for DRF create views/viewsets. Clients opt in with an
    `Idempotency-Key` header; omitting it behaves exactly as before."""

    def create(self, request, *args, **kwargs):
        key = request.headers.get("Idempotency-Key")
        if not key:
            return super().create(request, *args, **kwargs)

        body_hash = _hash_body(request.data)
        existing = IdempotencyKey.objects.filter(
            key=key, request_path=request.path, request_body_hash=body_hash
        ).first()
        if existing is not None:
            return Response(existing.response_body, status=existing.response_status)

        response = super().create(request, *args, **kwargs)
        IdempotencyKey.objects.create(
            organization=request.organization,
            key=key,
            request_path=request.path,
            request_body_hash=body_hash,
            response_status=response.status_code,
            response_body=response.data,
        )
        return response
