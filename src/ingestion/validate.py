from fastapi import UploadFile, HTTPException, status
from core.config import settings

def pdf_validation(file: UploadFile)->bool:

    if file.content_type in settings().FILE_TYPE:  
        return True
    return False