"""Standalone RAG API: no MySQL, sign model, or hospital startup dependencies."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.routers.knowledge import router

app = FastAPI(title="Hospital RAG demo API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8001", "http://127.0.0.1:8001"],
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)
app.include_router(router)
