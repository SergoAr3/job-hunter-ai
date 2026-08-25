import os


DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://job_hunter:job_hunter@localhost:5432/job_hunter"
)
