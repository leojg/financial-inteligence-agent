from pathlib import Path
from langchain_community.document_loaders import DirectoryLoader, PyPDFLoader
import pandas as pd

from langchain_core.documents import Document
import logging

logger = logging.getLogger(__name__)

def load_excel_documents(folder_path: Path) -> list[Document]:
    """Load Excel files as LangChain Documents using pandas."""
    documents = []
    excel_files = list(folder_path.glob("*.xlsx")) + list(folder_path.glob("*.xls"))
    logger.info(f"Found {len(excel_files)} Excel files in {folder_path}")

    for file_path in excel_files:
        try:
            logger.info(f"Loading Excel file: {file_path}")
            df = pd.read_excel(file_path)
            if df.empty:
                logger.warning(f"Empty Excel file: {file_path}")
                continue
            content = df.to_markdown(index=False)
            doc = Document(
                page_content=content,
                metadata={
                    "source": str(file_path),
                    "filename": file_path.name,
                    "row_count": len(df),
                    "columns": df.columns.tolist(),
                },
            )
            documents.append(doc)
        except Exception as e:
            logger.error(f"Failed to load {file_path.name}: {e}")
            continue

    logger.info(f"Successfully loaded {len(documents)} Excel files")
    return documents


def load_documents(data_dir: Path) -> list[Document]:
    """Load all PDF and Excel documents from the data directory."""
    if not data_dir.is_dir():
        raise FileNotFoundError(
            f"Data directory not found: {data_dir}. Create it and add PDF/XLSX documents."
        )

    pdf_loader = DirectoryLoader(
        path=str(data_dir),
        glob="**/*.pdf",
        loader_cls=PyPDFLoader,
    )
    pdf_docs = pdf_loader.load()
    excel_docs = load_excel_documents(data_dir)
    documents = pdf_docs + excel_docs

    if not documents:
        raise FileNotFoundError(
            f"No documents found in {data_dir}. Add PDF or Excel files to run the analyzer."
        )
    return documents
