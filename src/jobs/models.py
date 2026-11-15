"""Documents and ingestion jobs.

- Document: the FILE. One per unique PDF (doc_id = SHA-256 of its bytes).
- Job:      one PROCESSING RUN of that file. A document can have several jobs
            over time (e.g. a failed one, then a retry).

Job states (one direction only, any stage can fail):

  queued -> parsing -> chunking -> embedding -> indexing -> indexed
                                 (any stage) ------------> failed
"""

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4
from pydantic import BaseModel, Field
from api.schemas import DocMetadata


class JobStatus(str, Enum):
    QUEUED = "queued"        # created by the API, waiting for a worker
    PARSING = "parsing"      # PDF -> Markdown               (Phase 3)
    CHUNKING = "chunking"    # Markdown -> parents/children  (Phase 4)
    EMBEDDING = "embedding"  # children -> vectors           (Phase 5)
    INDEXING = "indexing"    # vectors -> Qdrant             (Phase 5)
    INDEXED = "indexed"      # done, ready for questions
    FAILED = "failed"        # see job.stage and job.error


# Legal moves. Anything else is a bug and gets refused.
ALLOWED_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.QUEUED:    {JobStatus.PARSING, JobStatus.FAILED},
    JobStatus.PARSING:   {JobStatus.CHUNKING, JobStatus.FAILED},
    JobStatus.CHUNKING:  {JobStatus.EMBEDDING, JobStatus.FAILED},
    JobStatus.EMBEDDING: {JobStatus.INDEXING, JobStatus.FAILED},
    JobStatus.INDEXING:  {JobStatus.INDEXED, JobStatus.FAILED},
    JobStatus.INDEXED:   set(),
    JobStatus.FAILED:    {JobStatus.QUEUED},  # retry
}


class InvalidTransition(Exception):
    """Raised for an illegal move, e.g. indexed -> parsing."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Document(BaseModel):
    doc_id: str                 # SHA-256 hex of the file
    file_key: str               # "raw/<doc_id>.pdf", relative to DATA_DIR
    metadata: DocMetadata       # original filename, type, size
    created_at: datetime = Field(default_factory=utc_now)


class Job(BaseModel):
    # default_factory runs once PER OBJECT, so each job gets its own id/time.
    job_id: UUID = Field(default_factory=uuid4)
    doc_id: str
    status: JobStatus = JobStatus.QUEUED
    stage: JobStatus | None = None  # the stage that was running when it failed
    error: str | None = None        # human-readable reason, when failed
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
