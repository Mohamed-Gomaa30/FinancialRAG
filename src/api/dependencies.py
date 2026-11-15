"""Shared objects injected into routes with Depends.

Built once per process. Tests can swap them via app.dependency_overrides.
"""

from functools import lru_cache

from jobs.repository import InMemoryJobRepository, JobRepository


@lru_cache
def get_job_repository() -> JobRepository:
    return InMemoryJobRepository()  # Phase 2: PostgresJobRepository(...)
