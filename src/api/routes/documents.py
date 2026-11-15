import uuid
import aiofiles
from pathlib import Path
from hashlib import sha256
from core.config import settings, Configs
from ingestion.validate import pdf_validation
from api.schemas import DocMetadata, UploadResponse
from api.dependencies import get_job_repository
from jobs.models import Document
from jobs.repository import JobRepository
from fastapi import UploadFile, APIRouter, status, HTTPException, Depends, Response

document_router = APIRouter( 
    prefix= "/api/data",
    tags=["financial-app", "data"] 
)

@document_router.post("/uploud", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_file(
    file: UploadFile,
    response: Response,
    settings: Configs = Depends(settings),
    repo: JobRepository = Depends(get_job_repository),
):
    """
        1. upload file: done
        2. validate file: done
        3. store file into data/raw: done
        4. create document + job: done
        5. enqueue job to Redis: Phase 2
    """

    # 1. validate 
    if not pdf_validation(file): 
        raise HTTPException(status_code=status.HTTP_406_NOT_ACCEPTABLE,
                                    detail=f"this file type doesn't exist in the files type : {settings.FILE_TYPE}")
    
    raw_dir = Path(settings.DATA_DIR) / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    tmp_path = raw_dir / f"{uuid.uuid4().hex}.tmp"   
    hasher = sha256()

    try:
        async with aiofiles.open(tmp_path, mode="wb") as out:
            while chunk := await file.read(settings.CHUNK_SIZE):
                hasher.update(chunk)        
                await out.write(chunk)     
    except Exception:
        tmp_path.unlink(missing_ok=True)  
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save file",
        )

    file_sha256 = hasher.hexdigest()

    file_key = f"raw/{file_sha256}.pdf"  
    final_path = settings.DATA_DIR / file_key

    metadata = DocMetadata(
        file_name= file.filename,
        file_type= file.content_type,
        file_size = file.size
    )

    if final_path.exists():
        tmp_path.unlink(missing_ok=True)
    else:
        tmp_path.replace(final_path)

    # 4. Create the document and its job together. File first, rows second:
    #    a job must never point to a file that doesn't exist yet.
    document = Document(doc_id=file_sha256, file_key=file_key, metadata=metadata)
    job, created = repo.create_document_with_job(document)

    # 202 = "accepted, processing happens later". A duplicate isn't new work,
    # so it gets 200 plus the EXISTING job and the original upload's metadata.
    if not created:
        response.status_code = status.HTTP_200_OK
        document = repo.get_document(file_sha256)

    # 5. Phase 2: enqueue {"job_id", "doc_id", "file_key"} to Redis here.
    return UploadResponse(job_id=job.job_id,
                          status=job.status.value,
                          file_id=file_sha256,
                          file_path=file_key,
                          duplicate=not created,
                          doc_metadata=document.metadata)
    








