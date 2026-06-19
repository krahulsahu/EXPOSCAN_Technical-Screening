import contextvars
import re
from fastapi import Request, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# db context var
tenant_db_var: contextvars.ContextVar[AsyncIOMotorDatabase] = contextvars.ContextVar("tenant_db")

# tenant validation regex
TENANT_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{3,50}$")

_mongo_client: AsyncIOMotorClient = None

def init_db_client(client: AsyncIOMotorClient):
    global _mongo_client
    _mongo_client = client

# get db from context
def get_current_tenant_db() -> AsyncIOMotorDatabase:
    try:
        return tenant_db_var.get()
    except LookupError:
        raise RuntimeError("db context error")

# tenant db dependency
async def get_tenant_db(request: Request) -> AsyncIOMotorDatabase:
    if _mongo_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="db not ready"
        )
    
    tenant_id = request.headers.get("X-Tenant-ID")
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing header"
        )
    
    if not TENANT_ID_REGEX.match(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid tenant"
        )
    
    db_name = f"exposcan_tenant_{tenant_id}"
    db = _mongo_client[db_name]
    
    token = tenant_db_var.set(db)
    request.state.tenant_db_token = token
    return db

# tenant isolation middleware
class TenantIsolationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in ["/health", "/docs", "/openapi.json", "/auth/login"]:
            return await call_next(request)
        
        tenant_id = request.headers.get("X-Tenant-ID")
        if not tenant_id:
            return Response(
                content='{"detail": "missing header"}',
                status_code=401,
                media_type="application/json"
            )
            
        if not TENANT_ID_REGEX.match(tenant_id):
            return Response(
                content='{"detail": "invalid tenant"}',
                status_code=400,
                media_type="application/json"
            )
            
        db_name = f"exposcan_tenant_{tenant_id}"
        db = _mongo_client[db_name]
        
        token = tenant_db_var.set(db)
        try:
            response = await call_next(request)
            return response
        finally:
            tenant_db_var.reset(token)
