# RAG Application for Annual Financial Reports: Full Plan

## 1. Overview

Users upload annual financial reports (PDF). The system validates them, converts them to Markdown, indexes them with parent-document retrieval, and answers questions with citations.

### Decisions so far

| Area | Decision |
|---|---|
| API | FastAPI |
| Validation | Pydantic (+ pydantic-settings for config) |
| Parsing | pymupdf4llm (`==1.28.2`), PDF converted to `.md` |
| Splitting | LangChain: `MarkdownHeaderTextSplitter` (parents) and `RecursiveCharacterTextSplitter` (children) |
| Retrieval | Parent-document retrieval: parent = section, children = recursive chunks |
| Embeddings | Behind one interface, chosen by a factory: `gemini-embedding-2` (default), `gemini-embedding-001`, `nemotron-3.5-embed-8b` (NVIDIA NIM), or a Hugging Face model |
| Vector DB | Qdrant (children vectors) |
| Parent store and job metadata | Postgres |
| Background work | Redis broker + worker container |
| File storage | Docker named volume: `data/raw` for PDFs, `data/parsed` for `.md` |
| Generation | Gemini (model name in config) |
| Runtime | Docker + Docker Compose, locally and in deployment (no Modal or other serverless platform) |

### Still to decide
- Queue library: ARQ (async, small, fits FastAPI; suggested default), RQ (simplest), or Celery (most features).
- UI: Streamlit or Gradio for the first version.
- Whether a GPU is available (only needed for self-hosted Nemotron or Hugging Face embedders).

### System architecture

```
            ┌────────┐      ┌────────────┐      ┌──────────────┐
  Browser → │  UI    │ ───→ │ api        │ ───→ │ Redis broker │ ───→ worker
            └────────┘      │ (FastAPI)  │      │ (small msgs) │        │
                            └─────┬──────┘      └──────────────┘        │
                                  │ save PDF                            │ read PDF, write .md
                                  ▼                                     ▼
                      ┌─────────────────────────────────────────────────────┐
                      │ Docker volume  /data/raw/*.pdf   /data/parsed/*.md  │
                      └─────────────────────────────────────────────────────┘
                                  │                                     │
                                  ▼                                     ▼
                      ┌──────────────────────────────────────────────────┐
                      │ Postgres (parents, jobs)     Qdrant (child vectors)│
                      └──────────────────────────────────────────────────┘
                                  │
                                  ▼
                              Gemini API  (embeddings + generation)
```

### Ingestion flow

```
Upload → Validate → Save to data/raw → Commit job row → Enqueue message
       → [worker] Parse (pymupdf4llm → data/parsed/*.md)
       → Chunk (LangChain parents + children)
       → Embed (factory-chosen model) → Index (Qdrant + Postgres) → Mark indexed
```

### Query flow

```
Question (+ company / year filters) → Embed query → Hybrid search children
        → (Rerank) → Map to parents, dedupe → Gemini (grounded) → Answer + citations
```

### Phase summary

| Phase | Goal |
|---|---|
| 0 | Project setup, Docker, volume layout, settings |
| 1 | Upload endpoint, validation, file storage, job creation |
| 2 | Redis queue and worker skeleton, job state machine |
| 3 | Parsing with pymupdf4llm to Markdown |
| 4 | Parent/child chunking with LangChain |
| 5 | Embeddings (factory pattern) and vector indexing |
| 6 | Retrieval and generation |
| 7 | UI and API polish |
| 8 | Evaluation and tuning |
| 9 | Hardening and deployment |

---

## 2. Design principles

1. **The API stays fast.** It validates, saves the file, creates a job, enqueues, and returns `202` with a `job_id`. It never runs parsing or embedding inside a request.
2. **Redis carries messages, not files.** The message holds only identifiers (a claim check). The PDF lives on the volume.
3. **Postgres is the source of truth for job status.** Redis is a mailbox, and it is not the record of what happened.
4. **Every stage is idempotent and resumable.** A retry must replace data, never duplicate it, and it can restart from the saved `.md`.
5. **Swap by configuration.** Embedders, file storage, and the LLM sit behind small interfaces created by factories, so changing a provider is a config change.
6. **Never mix embedding models in one collection.** Collection names include the model and dimension.

### Patterns used

| Pattern | Where | Purpose |
|---|---|---|
| Strategy + Factory | `Embedder` interface, `create_embedder(settings)` | Choose Gemini / NIM / Hugging Face from config |
| Strategy + Factory | `FileStore` interface (`LocalFileStore` now, `S3FileStore` later) | Keep file access independent of the disk |
| Dependency injection | FastAPI `Depends(...)` | Build embedder, stores, and DB sessions once and inject them |
| Claim check | Redis message with `doc_id` | Keep large payloads out of the broker |
| State machine | Job status in Postgres | Clear progress, retries, and failure reasons |

---

## 3. Data layout (Docker volume)

One named volume is mounted at the same path in both `api` and `worker`.

```
/data/                      # named volume: app_data
  raw/
    <doc_id>.pdf            # original upload (doc_id = SHA-256 of the file)
  parsed/
    <doc_id>.md             # pymupdf4llm output
    <doc_id>.pages.json     # optional: page map for citations
```

In the repository, `data/` is git-ignored. Locally it maps to the same structure.

Rules:
- `doc_id` is the SHA-256 of the PDF. The same file uploaded twice maps to the same path (free deduplication).
- Messages and database rows store a relative key such as `raw/<doc_id>.pdf`. Each container joins it with `DATA_DIR` from settings. Never store absolute paths.
- The user's original filename is stored only as metadata in Postgres. It is never used on disk (path-traversal safety).
- Write atomically: save to `<doc_id>.pdf.tmp`, then rename.
- `api` and `worker` run as the same non-root user (same UID) to avoid permission errors.
- Back up the volume. `docker compose down -v` deletes it. Originals cannot be regenerated, while `parsed/` can.

---

## 4. Contracts

### Redis message (claim check)

```json
{ "job_id": "uuid", "doc_id": "sha256", "file_key": "raw/<doc_id>.pdf" }
```

### Documents vs jobs

- **Document** = the file. One per unique PDF, keyed by `doc_id` (SHA-256 of the bytes).
- **Job** = one processing run of that file, keyed by a random `job_id` (UUID). A document can have several jobs over time (a failed run, then a retry, or a re-index with a new embedder), so status lives on the job and history is kept.

### Job state machine (stored in Postgres)

```
queued → parsing → chunking → embedding → indexing → indexed
             (any non-final stage) ──────────────→ failed ──→ queued (retry)
```

- The legal moves are one table (`ALLOWED_TRANSITIONS` in `src/jobs/models.py`). Any other move raises `InvalidTransition`.
- On failure, `stage` records the status that was running and `error` holds a human-readable reason. A retry (`failed → queued`) clears both.
- `indexed` is final. Only the API creates jobs (always `queued`). Only the worker moves them forward.

### Ordering rule for the upload endpoint

1. Validate the file and metadata.
2. Save the file atomically to `data/raw`.
3. Commit the document and job rows to Postgres.
4. Push the message to Redis.
5. Return `202` with `job_id`.

Enqueuing last guarantees a worker never receives a job whose file or row does not exist.

Duplicate upload (same SHA-256): no new rows are written and nothing is enqueued. Return `200` with the existing job, the original upload's metadata, and `duplicate: true`.

### Interfaces

```python
# src/embeddings/base.py
class Embedder(ABC):
    name: str
    dim: int
    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    @abstractmethod
    def embed_query(self, text: str) -> list[float]: ...

# src/embeddings/factory.py
_REGISTRY = {"gemini": GeminiEmbedder, "nim": NimEmbedder, "hf": HuggingFaceEmbedder}

def create_embedder(settings) -> Embedder:
    cls = _REGISTRY[settings.embedding_provider]   # raise a clear error if unknown
    return cls(model=settings.embedding_model, dim=settings.embedding_dim)

# src/jobs/repository.py  (Protocol: implementations match by shape, no inheritance;
#                          a type checker (mypy/pyright) verifies the match)
class JobRepository(Protocol):
    def get_document(self, doc_id: str) -> Document | None: ...
    def get_job(self, job_id: UUID) -> Job | None: ...
    def latest_job_for(self, doc_id: str) -> Job | None: ...
    def create_document_with_job(self, document: Document) -> tuple[Job, bool]: ...  # (job, created)
    def set_status(self, job_id: UUID, new_status: JobStatus) -> Job: ...           # enforces transitions
    def mark_failed(self, job_id: UUID, error: str) -> Job: ...                     # records stage + error

# src/storage/base.py
class FileStore(Protocol):
    def save(self, relative_key: str, data: BinaryIO) -> str: ...
    def open(self, relative_key: str) -> BinaryIO: ...
    def delete_doc(self, doc_id: str) -> None: ...
```

Because LangChain is used, `Embedder` can subclass `langchain_core.embeddings.Embeddings` (same two methods) so embedders plug into LangChain vector stores. Check that integrations support the newest models, or use a thin custom adapter.

---

## Phase 0: Project setup, Docker, and settings

**Goal:** A running skeleton where every service starts and is healthy.

**Tasks**
- Repository structure:
  ```
  src/
    api/            # routes, schemas, dependencies
    core/           # config.py (pydantic-settings), logging, db session
    storage/        # base.py, local.py (FileStore)
    jobs/           # models.py (Document, Job, state machine), repository.py (JobRepository)
    ingestion/      # validate.py, parse.py, chunk.py, pipeline.py
    embeddings/     # base.py, factory.py, gemini.py, nim.py, hf.py
    retrieval/      # vectorstore.py, parent_store.py, rerank.py
    generation/     # prompts.py, gemini_llm.py
    worker/         # tasks.py, settings (queue entrypoint)
  data/             # git-ignored (raw/, parsed/)
  eval/
  docker/
  docker-compose.yml
  ```
- Dockerfiles: `Dockerfile.api` (light) and `Dockerfile.worker` (adds pymupdf4llm and the embedding/parsing dependencies). One shared Python package, two entrypoints.
- `docker-compose.yml` services: `api`, `worker`, `redis`, `qdrant`, `postgres`, `ui`. Add healthchecks and `depends_on: condition: service_healthy`.
- Volumes: `app_data` (mounted at `/data` in `api` and `worker`), `model_cache`, `qdrant_data`, `pg_data`, `redis_data`.
- Redis persistence enabled (AOF) on `redis_data` so queued jobs survive restarts.
- Settings via pydantic-settings: API keys, `DATA_DIR`, `RAW_DIR`, `PARSED_DIR`, DB URLs, embedding provider/model/dimension, chunk sizes, LLM model name, concurrency limits.
- `.env.example`, `Makefile` (`up`, `down`, `logs`, `test`), linting, pytest.

**Compose skeleton**

```yaml
services:
  api:
    build: { context: ., dockerfile: docker/Dockerfile.api }
    env_file: .env
    volumes: [ "app_data:/data" ]
    depends_on: { postgres: {condition: service_healthy}, redis: {condition: service_healthy}, qdrant: {condition: service_started} }
  worker:
    build: { context: ., dockerfile: docker/Dockerfile.worker }
    env_file: .env
    volumes: [ "app_data:/data", "model_cache:/root/.cache" ]
    depends_on: { postgres: {condition: service_healthy}, redis: {condition: service_healthy}, qdrant: {condition: service_started} }
  redis:    { image: redis:7, command: ["redis-server", "--appendonly", "yes"], volumes: ["redis_data:/data"] }
  qdrant:   { image: qdrant/qdrant, volumes: ["qdrant_data:/qdrant/storage"] }
  postgres: { image: postgres:16, volumes: ["pg_data:/var/lib/postgresql/data"] }
volumes: { app_data: {}, model_cache: {}, qdrant_data: {}, pg_data: {}, redis_data: {} }
```

**Deliverables:** `docker compose up` starts everything; `GET /health` checks Postgres, Redis, and Qdrant; no secrets in images.

**Done when:** All containers are healthy and `api` and `worker` can both see the same `/data` volume.

---

## Phase 1: Upload, validation, and file storage

**Goal:** Only clean, well-described PDFs enter the system, and the API returns immediately.

**Tasks**
- Pydantic models:
  - `UploadedReport`: `company`, `fiscal_year`, `report_type` (Literal: 10-K, annual report, etc.), `currency`, `language`.
  - `FileValidationResult`: `valid`, `errors[]`, `sha256`, `page_count`, `size_bytes`.
  - `Document`: `doc_id`, `file_key`, `metadata`, `created_at`.
  - `Job`: `job_id`, `doc_id`, `status` (`JobStatus` enum), `stage`, `error`, `created_at`, `updated_at`.
- File checks: magic bytes (`%PDF`), size limit, page limit, not encrypted, not corrupt, duplicate check by SHA-256.
- `LocalFileStore` saves to `data/raw/<doc_id>.pdf` using atomic write (temp file, then rename).
- Job creation (see section 4, "Documents vs jobs" and the state machine):
  - `JobRepository` Protocol, injected with `Depends(get_job_repository)` (built once per process, swappable in tests through `dependency_overrides`).
  - `InMemoryJobRepository` as a development stand-in. It is lost on restart and the worker can't see it, so it can't be used from Phase 2 on.
  - `PostgresJobRepository` with the same methods. `create_document_with_job` runs as one transaction with `INSERT ... ON CONFLICT (doc_id) DO NOTHING`, so concurrent uploads of the same file can't create two documents.
- `POST /documents`: follows the ordering rule in section 4. New file → `202` with a `queued` job. Duplicate → `200` with the existing job and `duplicate: true`.
- Re-running a duplicate whose latest job is `failed`: decide between `?reprocess=true` on upload or a `POST /jobs/{job_id}/retry` endpoint (`failed → queued`, then enqueue).
- `GET /jobs/{job_id}` returns status, stage, and error (`404` if unknown, `422` if not a UUID).
- Postgres tables: `documents` (PK `doc_id`) and `jobs` (PK `job_id`, FK `doc_id`, index on `(doc_id, created_at)` for "latest job"). Use migrations (Alembic).

**Status**
- Done: `Document`/`Job` models, state machine, `JobRepository` Protocol, `InMemoryJobRepository`, upload endpoint with dedup (202/200), `GET /jobs/{job_id}`.
- Open: `PostgresJobRepository` + Alembic, `UploadedReport` metadata, remaining file checks (page limit, encrypted, corrupt), `LocalFileStore` refactor, retry for failed duplicates, tests.
- Cleanup: move `DocMetadata` out of `api/schemas.py` so `jobs/` doesn't depend on the API layer. Use the byte count from streaming as `file_size` (`UploadFile.size` can be `None`).

**Deliverables:** Upload endpoint, validators, store, and tests for good and bad files (empty, renamed `.txt`, encrypted, oversized, duplicate, path-traversal filename).

**Done when:** Invalid files are rejected with specific reasons, valid files land in `data/raw`, and a `queued` job row exists.

---

## Phase 2: Redis queue and worker skeleton

**Goal:** The worker receives jobs and walks the state machine, even before real processing exists.

**Tasks**
- Choose the queue library (ARQ recommended; RQ or Celery are alternatives) and enqueue from the API after the DB commit.
- Worker entrypoint with a task `ingest_document(job_id, doc_id, file_key)`.
- Update job status in Postgres at each stage, with the stage name on failure.
- Retry policy: retry transient errors (timeouts, rate limits) with exponential backoff; fail permanent errors (corrupt PDF) immediately with a clear message.
- Acknowledge a job only after it finishes, so a crashed worker's job returns to the queue (use the library's job timeout / late-ack equivalent).
- Concurrency limit per worker (1–2 jobs) to protect memory; configurable.
- Placeholder stages that only sleep, to prove the flow end to end.

**Deliverables:** Working queue, worker, and job state transitions visible through `GET /jobs/{id}`.

**Done when:** An upload moves `queued → ... → indexed` with placeholder stages, a deliberately failing stage ends as `failed` with the reason, and killing the worker mid-job lets another run pick it up.

---

## Phase 3: Parsing with pymupdf4llm

**Goal:** Turn each PDF into a clean Markdown file with headings and tables intact.

**Why pymupdf4llm instead of Docling:** Docling loads layout, table, and OCR models into memory and was OOM-killed on the 13 GB dev laptop. pymupdf4llm uses no ML models, runs fast on CPU with low memory, and produces Markdown with headings and tables.

**Tasks**
- `parse.py` wraps `pymupdf4llm.to_markdown()`.
- Pin `pymupdf4llm==1.28.2`. The 2.x releases on PyPI are empty stub wheels (no code), so an unpinned install breaks the import.
- Call with `page_chunks=True` to get one Markdown chunk per page with metadata. Join them for `data/parsed/<doc_id>.md` (atomic write) and save the page map to `data/parsed/<doc_id>.pages.json` (character offsets → page number) so citations can show pages.
- Scanned PDFs: pymupdf4llm extracts only the embedded text layer. Detect pages with little or no text and either reject the upload with a clear message or flag it for OCR (PyMuPDF + Tesseract) as a later enhancement.
- Skip parsing if `parsed/<doc_id>.md` already exists and the pipeline version matches (resumable), unless a forced re-parse is requested.
- Log parse quality: pages, headings found, tables found, empty/scanned pages, duration.

**Deliverables:** `parse_pdf(file_key) -> parsed_key` and stored `.md` artifacts.

**Done when:** Sample reports (text-heavy and table-heavy) produce Markdown with correct heading hierarchy and readable tables, checked by eye, and a scanned PDF is detected and handled with a clear message.

---

## Phase 4: Parent/child chunking with LangChain

**Goal:** Section-level parents for context, small children for precise retrieval.

**Tasks**
- **Parents:** `MarkdownHeaderTextSplitter` on `#`, `##`, `###` produces one parent per section, with the header path in metadata (e.g. `Item 7 > Liquidity`). If a section exceeds a cap (about 4–6k tokens), split it at the next heading level or into consecutive parents.
- **Children:** `RecursiveCharacterTextSplitter` on each parent: about 256–512 tokens with small overlap (paragraph, then sentence).
- **Tables:** `RecursiveCharacterTextSplitter` can cut through a Markdown table, so extract tables first. Each table becomes its own child (`is_table=true`). If too large, split by row groups and repeat the header row in each piece.
- **Contextual header:** prepend `Company | FY | Section path` to each child's text before embedding.
- Metadata on every child: `child_id`, `parent_id`, `doc_id`, `page`, `section_path`, `company`, `fiscal_year`, `is_table`.
- Store parents in Postgres (`parent_id`, `doc_id`, `section_path`, `text`, `page_start`, `page_end`).
- All sizes are configurable. Write unit tests for edge cases: empty sections, no headings, a giant table, a very long paragraph.
- Re-chunking replaces the document's existing parents and children (idempotent).

**Deliverables:** `chunk(markdown, doc_meta) -> (parents, children)` and the parents table filled.

**Done when:** Every child maps to an existing parent, no child exceeds the size limit, and no table is split mid-row.

---

## Phase 5: Embeddings (factory pattern) and vector indexing

**Goal:** Index children in Qdrant through a swappable embedding interface.

**Tasks**
- Implement `Embedder` providers and `create_embedder(settings)` (see section 4):
  - **`gemini-embedding-2`** (default): multimodal, 8,192-token input, default output of 3072 dimensions, flexible output size from 128 to 3072 (768, 1536 and 3072 recommended). Start with 1536 or 768 to save storage.
  - **`gemini-embedding-001`**: older text-only baseline or fallback.
  - **`nemotron-3.5-embed-8b`** (NVIDIA NIM): multimodal text and document-image model with a hosted endpoint. Dimensions need care: the hosted reference lists 4096, while the self-hosted NIM support matrix lists 2048 for the 8B BF16 build. Always set `dim` explicitly and verify it against a real test response at startup.
  - **Hugging Face option**, for example NVIDIA's text-only `Nemotron-3-Embed-8B-BF16`: open weights, 32K context, GPU needed, uses `query:` / `passage:` prefixes and L2-normalized outputs.
- Handle each model's query/document convention (Gemini task types, `query:` / `passage:` prefixes, NIM `input_type`). Using the wrong one quietly hurts recall.
- Batch requests, retry with backoff, and respect rate limits. A shared limiter keeps several workers within quota.
- Qdrant collection per `(embedder name, dim)`, for example `children__gemini-embedding-2__1536`. Create payload indexes on `company`, `fiscal_year`, `doc_id`, `is_table`.
- Add a sparse / BM25 vector for hybrid search.
- Indexing is idempotent: delete the document's points by `doc_id`, then upsert.
- Re-index script: `python -m src.scripts.reindex --embedder ...` rebuilds a collection from stored parents and children without re-parsing.

**Deliverables:** Embedder implementations, factory, indexing function, and re-index script.

**Done when:** A report is fully indexed, a manual similarity query returns relevant children, and switching `EMBEDDING_PROVIDER` in `.env` plus re-index works with no code change.

---

## Phase 6: Retrieval and generation

**Goal:** Grounded answers with citations.

**Query pipeline**
1. Embed the query using the same embedder. Apply filters from the UI, or extract company/year from the question with a small LLM call.
2. Hybrid search over children (dense + sparse), top 20–30.
3. Optional rerank down to the top 5–8.
4. Map to `parent_id`s, dedupe, and keep them ordered by best child score.
5. Fetch parents from Postgres and trim to a token budget.

**Generation (Gemini)**
- Prompt: answer only from the supplied sections, cite `[doc, section, page]`, say "not found" otherwise, quote figures with units and currency.
- Structured output as a Pydantic model: `answer`, `citations[]`, `confidence`.
- Model name in config: a faster model by default, a stronger one for complex analysis.

**API**
- `POST /query` with optional `company`, `fiscal_year`, `doc_id` filters.
- Log each query's retrieved children, parents, and final prompt for debugging.

**Done when:** Sample questions return correct, cited answers, and out-of-scope questions return "not found".

---

## Phase 7: UI and API polish

**Goal:** A usable end-to-end product.

**Tasks**
- UI (Streamlit or Gradio): upload form with metadata, job progress, document list, chat with citations, and a source viewer showing the cited section and page.
- `GET /documents`, `DELETE /documents/{id}`. Deleting removes the PDF in `raw/`, the `.md` in `parsed/`, parent rows, vectors in Qdrant, and the job record.
- Retry button for failed jobs.
- Pagination and basic error messages.

**Done when:** A user can upload a report, watch it index, ask questions, and open the cited source, all through `docker compose up`.

---

## Phase 8: Evaluation and tuning

**Goal:** Choose settings with measurements.

**Tasks**
- Golden set of 30–50 questions per sample report: text lookups, table lookups, multi-year comparisons, and unanswerable questions.
- Retrieval metrics: recall@k and MRR. Generation metrics: faithfulness, answer correctness, and numeric accuracy (for example with Ragas plus exact-match checks on figures).
- Experiments:
  - Embedders: `gemini-embedding-2` (768 / 1536 / 3072) vs. `gemini-embedding-001` vs. Nemotron vs. a Hugging Face model.
  - Child size (256 vs. 512), with and without contextual headers.
  - Dense only vs. hybrid, with and without reranking.
- Record the results and set the winning configuration as the default.

**Done when:** The default configuration is backed by numbers and a regression eval can run in CI.

---

## Phase 9: Hardening and deployment

**Goal:** Safe and maintainable to run.

**Tasks**
- Authentication and per-user or per-tenant isolation (filters or separate collections), if needed.
- Secrets through Docker secrets or a secret manager. Rate limiting and request size limits.
- Structured logging, metrics (ingestion time per stage, query latency, token usage and cost), and alerts for failed jobs.
- Backups for `app_data`, `pg_data`, and `qdrant_data` (for example a scheduled `tar` from a throwaway container).
- Production compose file: restart policies, resource limits (memory limit on `worker`), pinned image versions, non-root users.
- Optional GPU profile with a self-hosted Nemotron NIM container.
- If workers must run on other machines, implement `S3FileStore` (or MinIO) behind the existing `FileStore` interface.
- README: setup, configuration, re-indexing, backups, and troubleshooting.

**Done when:** A fresh machine can deploy the stack from the README, and a restart loses no data or queued jobs.

---

## 5. Configuration reference

| Setting | Example | Notes |
|---|---|---|
| `DATA_DIR` | `/data` | Volume mount path |
| `RAW_DIR` / `PARSED_DIR` | `/data/raw`, `/data/parsed` | Under `DATA_DIR` |
| `EMBEDDING_PROVIDER` | `gemini` / `nim` / `hf` | Selects the class in the factory |
| `EMBEDDING_MODEL` | `gemini-embedding-2` | Model name for that provider |
| `EMBEDDING_DIM` | `1536` | Must match the collection |
| `LLM_MODEL` | a Gemini model name | Config value, not hard-coded |
| `PARENT_MAX_TOKENS` | `5000` | Cap for large sections |
| `CHILD_CHUNK_TOKENS` / `CHILD_OVERLAP` | `400` / `50` | Tune in Phase 8 |
| `WORKER_CONCURRENCY` | `2` | pymupdf4llm is light; raise if embedding rate limits allow |
| `RETRIEVE_TOP_K` / `RERANK_TOP_K` | `30` / `6` | Tune in Phase 8 |

---

## 6. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Poor table parsing in complex layouts | Parse-quality logging, manual review, and keeping tables atomic |
| Page numbers lost in Markdown export | `page_chunks=True` and a saved page map, tested in Phase 3 |
| Scanned PDFs have no text layer | Detect low-text pages; reject or route to OCR |
| pymupdf4llm 2.x stub wheels break imports | Pin `pymupdf4llm==1.28.2` in requirements |
| Splitter cuts through a Markdown table | Extract tables before recursive splitting |
| Wrong embedding conventions (task type or prefix) | Per-provider tests on known queries |
| Dimension mismatch (hosted vs. self-hosted Nemotron) | Explicit `dim` and a startup check against a test embedding |
| Switching embedding models later | Collection per `(model, dim)` and a re-index script |
| Job enqueued before file or row exists | Fixed order: save file, commit DB, then enqueue |
| Lost jobs when Redis restarts or a worker dies | Redis AOF, late acknowledgement, and job status in Postgres |
| Duplicate vectors after retries | Idempotent indexing keyed by `doc_id` |
| Rate limits from the embedding API | Batching, backoff, shared limiter, worker concurrency cap |
| Out-of-memory in worker | Concurrency 1–2 and a container memory limit |
| Data loss from `docker compose down -v` | Scheduled volume backups |
| Hallucinated figures | Grounded prompt, citations, and numeric accuracy in the eval set |
| Large parents overflowing context | Section cap and a token budget at generation time |