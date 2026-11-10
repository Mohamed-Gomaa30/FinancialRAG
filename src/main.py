from fastapi import FastAPI
from api.routes import documents

app = FastAPI(title="Financial RAG API")
app.include_router(documents.document_router)
