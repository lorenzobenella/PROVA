from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    strava_client_id: str = ""
    strava_client_secret: str = ""
    strava_redirect_uri: str = "http://localhost:8000/api/auth/strava/callback"

    garmin_email: str = ""
    garmin_password: str = ""

    secret_key: str = "change-me"
    database_url: str = "sqlite:///./data/app.db"


settings = Settings()
