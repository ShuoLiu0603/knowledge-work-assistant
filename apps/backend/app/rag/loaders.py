from __future__ import annotations

import os
import warnings
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory

from langchain_core.documents import Document

from app.core.config import get_settings

os.environ.setdefault("TRANSFORMERS_VERBOSITY", get_settings().transformers_verbosity)

SUPPORTED_EXTENSIONS = {"pdf", "docx", "txt", "md", "csv"}
MARKDOWN_HEADERS = [
    ("#", "h1"),
    ("##", "h2"),
    ("###", "h3"),
    ("####", "h4"),
    ("#####", "h5"),
    ("######", "h6"),
]


def load_documents(file_bytes: bytes, file_name: str, file_ext: str) -> list[Document]:
    ext = normalize_extension(file_ext)
    with TemporaryDirectory() as temp_dir:
        file_path = Path(temp_dir) / temporary_file_name(file_name, ext)
        file_path.write_bytes(file_bytes)
        documents = build_loader(file_path, ext).load()

    if ext == "md":
        documents = split_markdown_documents(documents)

    return [normalize_document(document, file_name, ext) for document in documents if document.page_content.strip()]


def build_loader(file_path: Path, ext: str):
    if ext == "pdf":
        PyPDFLoader = import_community_loader("langchain_community.document_loaders.pdf", "PyPDFLoader")
        return PyPDFLoader(str(file_path), mode="page")
    if ext == "docx":
        Docx2txtLoader = import_community_loader("langchain_community.document_loaders.word_document", "Docx2txtLoader")
        return Docx2txtLoader(str(file_path))
    if ext == "csv":
        CSVLoader = import_community_loader("langchain_community.document_loaders.csv_loader", "CSVLoader")
        return CSVLoader(str(file_path), autodetect_encoding=True)
    if ext in {"txt", "md"}:
        TextLoader = import_community_loader("langchain_community.document_loaders.text", "TextLoader")
        return TextLoader(str(file_path), autodetect_encoding=True)
    raise ValueError(f"Unsupported file extension: {ext}")


def import_community_loader(module_path: str, class_name: str):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        module = import_module(module_path)
    return getattr(module, class_name)


def split_markdown_documents(documents: list[Document]) -> list[Document]:
    from langchain_text_splitters.markdown import MarkdownHeaderTextSplitter

    splitter = MarkdownHeaderTextSplitter(headers_to_split_on=MARKDOWN_HEADERS, strip_headers=False)
    split_documents: list[Document] = []
    for document in documents:
        for section in splitter.split_text(document.page_content):
            split_documents.append(
                Document(
                    page_content=section.page_content,
                    metadata={**document.metadata, **section.metadata},
                )
            )
    return split_documents or documents


def normalize_document(document: Document, file_name: str, ext: str) -> Document:
    metadata = normalize_metadata(document.metadata, file_name, ext)
    return Document(page_content=document.page_content.strip(), metadata=metadata)


def normalize_metadata(metadata: dict, file_name: str, ext: str) -> dict:
    normalized = {key: value for key, value in metadata.items() if key != "source"}
    normalized["file_name"] = file_name
    normalized["source_ext"] = f".{ext}"
    normalized["block_type"] = block_type_for(ext)

    page = normalized.get("page")
    if isinstance(page, int):
        normalized["page_number"] = page + 1

    row = normalized.get("row")
    if ext == "csv" and isinstance(row, int):
        normalized["row_number"] = row + 1

    title_parts = [normalized[key] for _, key in MARKDOWN_HEADERS if normalized.get(key)]
    if title_parts:
        normalized["title_path"] = " / ".join(title_parts)
        normalized["section_name"] = title_parts[-1]

    return normalized


def block_type_for(ext: str) -> str:
    if ext == "pdf":
        return "page"
    if ext == "csv":
        return "table"
    if ext == "md":
        return "section"
    return "paragraph"


def normalize_extension(file_ext: str) -> str:
    ext = file_ext.lower().lstrip(".")
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file extension: {file_ext}")
    return ext


def temporary_file_name(file_name: str, ext: str) -> str:
    raw_name = Path(file_name or f"upload.{ext}").name.strip() or f"upload.{ext}"
    if Path(raw_name).suffix.lower().lstrip(".") == ext:
        return raw_name
    stem = Path(raw_name).stem or "upload"
    return f"{stem}.{ext}"
