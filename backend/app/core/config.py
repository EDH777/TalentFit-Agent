from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "TalentFit Agent"
    database_url: str = f"sqlite:///{(BASE_DIR / 'data' / 'talentfit.db').as_posix()}"
    upload_dir: str = str(BASE_DIR / "data" / "uploads")
    jwt_secret: str = "local-development-only-replace-with-random-secret"
    jwt_expire_minutes: int = 720
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    openai_api_key: str = ""
    openai_base_url: str = ""
    model_name: str = "qwen-plus"
    admin_username: str = "admin"
    admin_password: str = ""
    hr_username: str = "hr"
    hr_password: str = ""
    max_upload_size_mb: int = 15
    max_pdf_pages: int = 20
    llm_timeout_seconds: int = 90
    max_batch_files: int = 3
    embedding_enabled: bool = True
    embedding_model: str = "text-embedding-v4"
    embedding_dimensions: int = 1024
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    qwen_enable_thinking: bool = False
    staffing_checkpoint_path: str = str(BASE_DIR / "data" / "staffing-checkpoints.sqlite")
    staffing_run_seconds: int = 180
    staffing_tool_limit: int = 20
    staffing_lease_seconds: int = 30
    staffing_poll_seconds: float = 1.0
    staffing_runtime_dir: str = str(BASE_DIR.parent / 'data' / 'runtime')

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8-sig", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


settings = Settings()
Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
(BASE_DIR / "data").mkdir(parents=True, exist_ok=True)
