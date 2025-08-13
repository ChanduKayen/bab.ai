from fastapi import FastAPI
from whatsapp.webhook import router as whatsapp_router
from whatsapp.apis import router as apis
from app.logging_config import logger
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

logger.info(" main.py loaded")

app.include_router(whatsapp_router, prefix="/whatsapp")
app.include_router(apis)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Or specify: ["http://localhost:3000"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"status": "backend is live"}

@app.get("/routes")
def show_routes():  
    return [route.path for route in app.routes]


for route in app.routes:
    print(" Registered route:", route.path)
    logger.info(f" Registered route: {route.path}")
