"""FastAPI app factory."""

from fastapi import FastAPI

from bankofai.x402_gateway.server.admin import router as admin_router
from bankofai.x402_gateway.server.proxy import router as proxy_router
from bankofai.x402_gateway.server.registry import ProviderRegistry


def create_app(registry: ProviderRegistry | None = None) -> FastAPI:
    app = FastAPI(title="x402-gateway")
    app.state.provider_registry = registry or ProviderRegistry()
    app.include_router(admin_router)
    app.include_router(proxy_router)
    return app
