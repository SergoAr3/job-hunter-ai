import os


DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://job_hunter:job_hunter@localhost:5432/job_hunter"
)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
OPENAI_TIMEOUT_SECONDS = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "15"))

if not 10 <= OPENAI_TIMEOUT_SECONDS <= 15:
    raise ValueError("OPENAI_TIMEOUT_SECONDS must be between 10 and 15 seconds")
