"""Tests for src.ingestion.validate: one test per rule.

Test PDFs are generated in memory with pymupdf, so no binary files are
committed and every case is explicit about what it builds.
"""

import hashlib

import pymupdf
import pytest

from src.ingestion.exceptions import (
    CorruptPDF,
    EmptyFile,
    EncryptedPDF,
    FileTooLarge,
    NotAPDF,
    PDFValidationError,
    TooManyPages,
)
from src.ingestion.validate_ import validate_pdf

LIMITS = {"max_size_mb": 5, "max_pages": 10}


def make_pdf(pages: int = 1, **save_kwargs) -> bytes:
    """Build a small PDF with `pages` pages, each holding a line of text."""
    with pymupdf.open() as doc:
        for i in range(pages):
            doc.new_page().insert_text((72, 72), f"Page {i + 1}")
        return doc.tobytes(**save_kwargs)


def test_valid_pdf_returns_facts():
    data = make_pdf(pages=3)

    result = validate_pdf(data, **LIMITS)

    assert result.page_count == 3
    assert result.size_bytes == len(data)
    assert result.doc_id == hashlib.sha256(data).hexdigest()


def test_same_bytes_give_same_doc_id():
    data = make_pdf()

    assert validate_pdf(data, **LIMITS).doc_id == validate_pdf(data, **LIMITS).doc_id


def test_empty_file_rejected():
    with pytest.raises(EmptyFile):
        validate_pdf(b"", **LIMITS)


def test_oversized_file_rejected():
    data = b"%PDF-" + b"0" * (1024 * 1024 + 1)  # just over 1 MB

    with pytest.raises(FileTooLarge, match="limit is 1 MB"):
        validate_pdf(data, max_size_mb=1, max_pages=10)


def test_non_pdf_bytes_rejected():
    with pytest.raises(NotAPDF):
        validate_pdf(b"PK\x03\x04 this is a zip file", **LIMITS)


def test_pdf_signature_with_junk_rejected():
    with pytest.raises(CorruptPDF):
        validate_pdf(b"%PDF-1.7\nthis is not really a pdf", **LIMITS)


def test_password_protected_pdf_rejected():
    data = make_pdf(
        encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner"
    )

    with pytest.raises(EncryptedPDF):
        validate_pdf(data, **LIMITS)


def test_owner_password_only_pdf_accepted():
    # Encrypted for permissions only: opens without a password, so it's fine.
    data = make_pdf(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner")

    assert validate_pdf(data, **LIMITS).page_count == 1


def test_too_many_pages_rejected():
    data = make_pdf(pages=11)

    with pytest.raises(TooManyPages, match="11 pages; the limit is 10"):
        validate_pdf(data, **LIMITS)


def test_all_errors_share_a_base_class():
    # The API can catch every validation failure with one except clause.
    with pytest.raises(PDFValidationError):
        validate_pdf(b"not a pdf", **LIMITS)
