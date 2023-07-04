from chromadb.config import Settings
from dotenv import load_dotenv

load_dotenv()

users = ["Ken", "Jeff", "Andrew", "Pete"]

USERNAME_MAIL = {
    'Ken': 'kenneth.lay@enron.com',
    'Jeff': 'jeff.skilling@enron.com',
    'Andrew': 'andrew.fastow@enron.com'
}

CHROMA_SETTINGS = {
    user: Settings(
        chroma_db_impl='duckdb+parquet',
        persist_directory=f"db/{user}",
        anonymized_telemetry=False
    )
    for user in users
}
