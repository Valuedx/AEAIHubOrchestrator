from __future__ import annotations

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.database import tenant_id_context
from app.config import settings
from jose import jwt, JWTError


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Middleware that populates the ``tenant_id_context`` ContextVar from
    the request's tenant identification (JWT or header).
    
    This ensures that the SQLAlchemy global listener can re-apply RLS
    GUCs even if a session is reused or a commit() clears the settings.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        tenant_id = self._extract_tenant_id(request)
        
        # Set the context variable. It is local to the current Task/thread.
        token = tenant_id_context.set(tenant_id)
        try:
            return await call_next(request)
        finally:
            # Clear it after the request finishes
            tenant_id_context.reset(token)

    def _extract_tenant_id(self, request: Request) -> str | None:
        # Replicate the core logic of jwt_auth.get_tenant_id without the
        # FastAPI-specific Depends/Header injection.
        
        if settings.auth_mode == "jwt":
            auth = request.headers.get("authorization", "")
            if not auth.lower().startswith("bearer "):
                return None
            token = auth[7:].strip()
            try:
                # Use HS256 algorithm as defined in jwt_auth.py
                payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
                return payload.get("tenant_id")
            except JWTError:
                return None

        # Dev mode fallback: header or query param
        return request.headers.get("x-tenant-id") or request.query_params.get("x_tenant_id")
