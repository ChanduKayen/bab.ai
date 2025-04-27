# test_api.py

from fastapi import FastAPI, Request

app = FastAPI()

@app.get("/")
def root():
    return {"msg": "server is alive"}

@app.post("/test")
async def test_post(request: Request):
    return {"msg": "✅ POST /test route hit"}
