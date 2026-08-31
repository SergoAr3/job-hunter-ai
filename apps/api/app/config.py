import os


DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://job_hunter:job_hunter@localhost:5432/job_hunter"
)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
OPENAI_TIMEOUT_SECONDS = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "15"))
# CV drafts can include substantially more source text and a larger structured
# response than vacancy enrichment.  Keep their latency budget isolated so a
# CV-specific adjustment never changes the vacancy flow.
CV_AI_TIMEOUT_SECONDS = float(os.getenv("CV_AI_TIMEOUT_SECONDS", "30"))

if not 10 <= OPENAI_TIMEOUT_SECONDS <= 15:
    raise ValueError("OPENAI_TIMEOUT_SECONDS must be between 10 and 15 seconds")
if not 20 <= CV_AI_TIMEOUT_SECONDS <= 45:
    raise ValueError("CV_AI_TIMEOUT_SECONDS must be between 20 and 45 seconds")
