import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://fitness:fitness@localhost:5432/fitness",
)
os.environ.setdefault("JWT_SECRET", "test-secret")
