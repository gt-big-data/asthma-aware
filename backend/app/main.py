# Main entry point for FastAPI application

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.health import router as health_router
from app.routes.map import router as map_router
from app.routes.cells import router as cells_router

app = FastAPI(
    title="AsthmaAware Backend API",
    description="Backend API for AsthmaAware heat map and cell risk data.",
    version="0.1.0",
)

# Allow frontend to connect during development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this later if needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, prefix="/health", tags=["Health"])
app.include_router(map_router, prefix="/map", tags=["Map"])
app.include_router(cells_router, prefix="/cells", tags=["Cells"])


@app.get("/")
def root():
    return {"message": "Welcome to the AsthmaAware Backend API"}