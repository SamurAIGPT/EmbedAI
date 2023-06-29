import os
from dotenv import load_dotenv
from chromadb.config import Settings

load_dotenv()

CHROMA_SETTINGS = {
    "Ken": Settings(
        chroma_db_impl='duckdb+parquet',
        persist_directory="db/Ken",
        anonymized_telemetry=False
    ),
    "Jeff": Settings(
        chroma_db_impl='duckdb+parquet',
        persist_directory="db/Jeff",
        anonymized_telemetry=False
    ),
    "Andrew": Settings(
        chroma_db_impl='duckdb+parquet',
        persist_directory="db/Andrew",
        anonymized_telemetry=False
    ),
    "Pete": Settings(
        chroma_db_impl='duckdb+parquet',
        persist_directory="db/Pete",
        anonymized_telemetry=False
    ),
}
