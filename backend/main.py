from fastapi import FastAPI

app = FastAPI(title="WeldERP - Order Management API")

@app.get("/")
def read_root():
    return {"message": "WeldERP API is running. Database is ready for orders."}