import uuid
import aiofiles
import time 
from pathlib import Path
from hashlib import sha256
from core.config import settings, Configs
from ingestion.validate import pdf_validation
from fastapi import UploadFile, APIRouter, status, HTTPException, Depends

document_router = APIRouter( 
    prefix= "/api/data",
    tags=["financial-app", "data"] 
)

@document_router.post("/uploud")
async def upload_file(file: UploadFile, settings: Configs = Depends(settings)): 
    """
        1. upload file: done 
        2. validate file: done 
        3. store file into data/raw: ---
    """

    # 1. validate 
    if not pdf_validation(file): 
        raise HTTPException(status_code=status.HTTP_406_NOT_ACCEPTABLE,
                                    detail=f"this file type doesn't exist in the files type : {settings().FILE_TYPE}]")

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

    final_path = raw_dir / f"{file_sha256}.pdf"

    if final_path.exists():
        tmp_path.unlink(missing_ok=True)   
    else:
        tmp_path.replace(final_path)

    return {
        "file_id": file_sha256, 
        "file_path": final_path,
        "file_type": file.content_type,  
        "file_size": file.size, 
    }

    








