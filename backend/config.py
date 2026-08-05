from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    SECRET_KEY: str = "dev-secret-key"

    DATABASE_URL: str = "sqlite:///./rfid_lab.db"

    GOOGLE_CREDENTIALS_FILE: str = "credentials.json"
    GOOGLE_TOKEN_FILE: str = "token.json"
    GOOGLE_DRIVE_ACCOUNT: str = "searlabuta@gmail.com"
    GOOGLE_DRIVE_ZEBRA_FOLDER_ID: str = ""
    GOOGLE_DRIVE_IMPINJ_FOLDER_ID: str = ""
    DRIVE_POLL_INTERVAL: int = 30

    RFID_SCANNER_PUSH_ENABLED: bool = True
    RFID_SCANNER_API_KEY: str = "rfid-scanner-secret-key"

    MQTT_ENABLED: bool = False
    MQTT_BROKER_HOST: str = "localhost"
    MQTT_BROKER_PORT: int = 1883
    MQTT_TOPIC_PREFIX: str = "lab/rfid"

    RFID_READER_ENABLED: bool = False
    IMPINJ_READER_HOST: str = "192.168.1.100"
    RSSI_THRESHOLD: int = -75

    LAB_NAME: str = "SEARLab UTA"
    SYSTEM_VERSION: str = "2025/26"
    IMPINJ_READER_ID: str = "impinj-r700-lab01"
    MC3300R_READER_ID: str = "mc3300r-lab01"

settings = Settings()
