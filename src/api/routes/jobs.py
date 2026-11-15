from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import get_job_repository
from jobs.models import Job
from jobs.repository import JobRepository

job_router = APIRouter(
    prefix="/api/jobs",
    tags=["financial-app", "jobs"],
)

@job_router.get("/{job_id}", response_model=Job)
def get_job(job_id: UUID, repo: JobRepository = Depends(get_job_repository)):

    job = repo.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job
