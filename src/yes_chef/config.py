import os


class Settings:
    database_url: str

    def __init__(self) -> None:
        self.database_url = os.environ.get(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/yeschef",
        )


settings = Settings()
