"""Document loaders for Excel and PDF bank statements."""

import logging
from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]
from langchain_community.document_loaders import PyPDFLoader
from langchain_core.documents import Document

logger = logging.getLogger(__name__)


def load_excel_documents(paths: list[str]) -> list[Document]:
    """Load Excel files (paths only) as LangChain Documents using pandas."""
    documents = []
    for path in paths:
        p = Path(path)
        if p.suffix.lower() not in (".xlsx", ".xls"):
            continue
        try:
            logger.info("Loading Excel file: %s", p)
            df = pd.read_excel(p)
            if df.empty:
                logger.warning("Empty Excel file: %s", p)
                continue
            content = df.to_markdown(index=False)
            doc = Document(
                page_content=content,
                metadata={
                    "source": str(p),
                    "filename": p.name,
                    "row_count": len(df),
                    "columns": df.columns.tolist(),
                },
            )
            documents.append(doc)
        except Exception as e:
            logger.error("Failed to load %s: %s", p.name, e)
    logger.info("Successfully loaded %d Excel files", len(documents))
    return documents


def load_documents(paths: list[str]) -> list[Document]:
    """Load PDF and Excel documents from a list of file paths."""
    pdf_paths = []
    xlsx_paths = []
    for path in paths:
        p = Path(path)
        suffix = p.suffix.lower()
        if suffix == ".pdf":
            pdf_paths.append(str(p))
        elif suffix in (".xlsx", ".xls"):
            xlsx_paths.append(str(p))

    documents = []
    for path in pdf_paths:
        documents.extend(PyPDFLoader(path).load())
    documents.extend(load_excel_documents(xlsx_paths))

    if not documents:
        raise FileNotFoundError(
            "No documents found in the given paths. Add PDF or Excel files to run the analyzer."
        )
    return documents
