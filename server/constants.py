from chromadb.config import Settings
from dotenv import load_dotenv

load_dotenv()

users = ["Ken", "Jeff", "Andrew", "Pete"]

CHROMA_SETTINGS = {
    user: Settings(
        chroma_db_impl='duckdb+parquet',
        persist_directory=f"db/{user}",
        anonymized_telemetry=False
    )
    for user in users
}
