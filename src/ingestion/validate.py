from fastapi import UploadFile, HTTPException, status
from core.config import settings

def pdf_validation(file: UploadFile)->bool:

    if file.content_type in settings().FILE_TYPE and file.size <= settings().MAX_FILE_SIZE and file.size != None:  
        return True
    return False