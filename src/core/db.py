"""Database connection: one engine per process, sessions per unit of work.

- engine:       holds a pool of open connections, reused by every request.
- SessionLocal: makes a Session (one unit of work / transaction) on demand.
- Base:         parent class of every table model; Base.metadata is what
                Alembic compares against the live database.
"""

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from core.config import settings

# Predictable constraint names (e.g. "ck_jobs_status", "fk_jobs_doc_id_documents").
# Without this Postgres invents names, and later migrations that drop or
# change a constraint can't refer to it reliably.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# pool_pre_ping: test a connection before using it, so a Postgres restart
# doesn't surface as "connection closed" errors on the next request.
engine = create_engine(settings().DATABASE_URL, pool_pre_ping=True)

# expire_on_commit=False: objects stay readable after commit, so the
# repository can convert a row to a Pydantic model after committing.
SessionLocal = sessionmaker(engine, expire_on_commit=False)
