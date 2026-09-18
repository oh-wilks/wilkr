from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes.activities import router as activities_router
from app.api.routes.health import router as health_router
from app.api.routes.segments import router as segments_router
from app.web.routes import router as web_router

app = FastAPI(title="wilkr")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(health_router)
app.include_router(activities_router)
app.include_router(segments_router)
app.include_router(web_router)
