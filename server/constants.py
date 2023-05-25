import os
from dotenv import load_dotenv
from chromadb.config import Settings

load_dotenv()

# Define the Chroma settings
CHROMA_SETTINGS = Settings(
        chroma_db_impl='duckdb+parquet',
        persist_directory="db/",
        anonymized_telemetry=False
)