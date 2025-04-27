from fastapi import FastAPI
from whatsapp.webhook import router as whatsapp_router
from app.logging_config import logger

app = FastAPI()

logger.info(" main.py loaded")

app.include_router(whatsapp_router, prefix="/whatsapp")

@app.get("/")
def read_root():
    return {"status": "backend is live"}

@app.get("/routes")
def show_routes():  
    return [route.path for route in app.routes]

for route in app.routes:
    print(" Registered route:", route.path)
    logger.info(f" Registered route: {route.path}")