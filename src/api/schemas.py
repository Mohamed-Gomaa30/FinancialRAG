from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field

class DocMetadata(BaseModel):
    file_name: str | None = None
    file_type: Literal["application/pdf"]
    file_size: int

class UploadResponse(BaseModel):
    job_id: UUID = Field(description="use GET /api/jobs/{job_id} to follow processing")
    status: str = Field(description="job status, e.g. queued")
    file_id: str = Field(min_length=64, max_length=64, description="SHA-256 of the file")
    file_path: str = Field(description="path relative to DATA_DIR, e.g. raw/<file_id>.pdf")
    duplicate: bool
    doc_metadata: DocMetadata
