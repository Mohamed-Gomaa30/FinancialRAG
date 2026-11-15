"""Where documents and jobs are stored.

Routes depend on the JobRepository interface, not on a concrete store.
InMemoryJobRepository is a development stand-in: it is lost on restart and
each process has its own copy (the worker can't see it). PostgresJobRepository
replaces it later with the same methods.
"""

from threading import Lock
from typing import Protocol
from uuid import UUID

from jobs.models import ALLOWED_TRANSITIONS, Document, InvalidTransition, Job, JobStatus, utc_now


class JobRepository(Protocol):
    def get_document(self, doc_id: str) -> Document | None: ...
    def get_job(self, job_id: UUID) -> Job | None: ...
    def latest_job_for(self, doc_id: str) -> Job | None: ...
    def create_document_with_job(self, document: Document) -> tuple[Job, bool]: ...
    def set_status(self, job_id: UUID, new_status: JobStatus) -> Job: ...
    def mark_failed(self, job_id: UUID, error: str) -> Job: ...


class InMemoryJobRepository:
    def __init__(self) -> None:
        self._documents: dict[str, Document] = {}
        self._jobs: dict[UUID, Job] = {}
        self._jobs_by_doc: dict[str, list[UUID]] = {}  # doc_id -> job ids, oldest first
        # Makes "check, then write" one step, so two concurrent uploads of the
        # same file can't both create a document.
        self._lock = Lock()

    # reads 
    def get_document(self, doc_id: str) -> Document | None:
        return self._documents.get(doc_id)

    def get_job(self, job_id: UUID) -> Job | None:
        return self._jobs.get(job_id)

    def latest_job_for(self, doc_id: str) -> Job | None:
        job_ids = self._jobs_by_doc.get(doc_id)
        return self._jobs[job_ids[-1]] if job_ids else None

    # --- writes --------------------------------------------------------
    def create_document_with_job(self, document: Document) -> tuple[Job, bool]:
        """Store a document and its first job together.

        Returns (job, created). If the document already exists, nothing is
        written and its latest job comes back with created=False.
        In Postgres this becomes one transaction: both rows or neither.
        """
        with self._lock:
            existing = self.latest_job_for(document.doc_id)
            if existing is not None:
                return existing, False

            job = Job(doc_id=document.doc_id)
            self._documents[document.doc_id] = document
            self._jobs[job.job_id] = job
            self._jobs_by_doc.setdefault(document.doc_id, []).append(job.job_id)
            return job, True

    def set_status(self, job_id: UUID, new_status: JobStatus) -> Job:
        """Move a job to its next state, refusing illegal moves."""
        with self._lock:
            job = self._require(job_id)
            if new_status not in ALLOWED_TRANSITIONS[job.status]:
                raise InvalidTransition(f"{job.status.value} -> {new_status.value} is not allowed")
            changes = {"status": new_status, "updated_at": utc_now()}
            if new_status == JobStatus.QUEUED:  # retry clears the old failure
                changes |= {"stage": None, "error": None}
            updated = job.model_copy(update=changes)
            self._jobs[job_id] = updated
            return updated

    def mark_failed(self, job_id: UUID, error: str) -> Job:
        """Fail a job, remembering which stage broke and why."""
        with self._lock:
            job = self._require(job_id)
            if JobStatus.FAILED not in ALLOWED_TRANSITIONS[job.status]:
                raise InvalidTransition(f"cannot fail a job that is {job.status.value}")
            updated = job.model_copy(update={
                "status": JobStatus.FAILED,
                "stage": job.status,
                "error": error,
                "updated_at": utc_now(),
            })
            self._jobs[job_id] = updated
            return updated

    def _require(self, job_id: UUID) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"job {job_id} not found")
        return job
