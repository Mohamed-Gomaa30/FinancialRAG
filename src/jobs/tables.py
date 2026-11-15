"""Postgres tables for documents and jobs (SQLAlchemy models).

These describe how rows are STORED. The Pydantic models in jobs/models.py
describe what the app and API work with. The repository converts between
the two, so routes never see these classes.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base
from jobs.models import JobStatus

# Built from the enum, so JobStatus stays the single source of allowed values.
# Adding a state = edit JobStatus + a migration that recreates this constraint.
_STATUS_VALUES = ", ".join(f"'{s.value}'" for s in JobStatus)


class DocumentRow(Base):
    """One row per unique PDF. doc_id = SHA-256 of the bytes."""

    __tablename__ = "documents"

    doc_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    file_key: Mapped[str] = mapped_column(String(255))  # "raw/<doc_id>.pdf"

    # Upload metadata as plain columns (not JSONB): report fields added later
    # (company, fiscal_year, ...) will be filtered on.
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(100))
    file_size: Mapped[int] = mapped_column(BigInteger)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class JobRow(Base):
    """One row per processing run of a document."""

    __tablename__ = "jobs"

    job_id: Mapped[UUID] = mapped_column(primary_key=True)  # native Postgres UUID
    # CASCADE: deleting a document (Phase 7) removes its jobs too.
    doc_id: Mapped[str] = mapped_column(
        ForeignKey("documents.doc_id", ondelete="CASCADE")
    )

    # String + CHECK instead of a native Postgres ENUM: the database still
    # rejects bad values, but adding a state later is a simple migration.
    status: Mapped[str] = mapped_column(String(20))
    stage: Mapped[str | None] = mapped_column(String(20))  # status that was running when it failed
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUS_VALUES})", name="status"),
        CheckConstraint(f"stage IS NULL OR stage IN ({_STATUS_VALUES})", name="stage"),
        # "Latest job for a document" (latest_job_for) becomes an index lookup.
        Index("ix_jobs_doc_id_created_at", "doc_id", "created_at"),
    )
