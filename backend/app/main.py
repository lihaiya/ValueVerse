from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import public_router, router
from app.auth import account_router, auth_router
from app.core.config import get_settings
from app.db.session import create_db_and_tables, seed_defaults


def create_app() -> FastAPI:
    settings = get_settings()
    settings.validate_runtime_settings()
    docs_url = "/api/docs" if settings.effective_api_docs_enabled else None
    openapi_url = "/api/openapi.json" if settings.effective_api_docs_enabled else None
    app = FastAPI(
        title="valueverse",
        version="0.1.0",
        openapi_url=openapi_url,
        docs_url=docs_url,
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(public_router, prefix="/api")
    app.include_router(auth_router, prefix="/api")
    app.include_router(account_router, prefix="/api")
    app.include_router(router, prefix="/api")

    @app.on_event("startup")
    def on_startup() -> None:
        create_db_and_tables()
        seed_defaults()

    return app


app = create_app()
