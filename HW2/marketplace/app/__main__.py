import uvicorn

from app.config import get_settings
from app.logging_config import logging_config

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host="0.0.0.0",
        port=8000,
        log_config=logging_config(settings.log_level),
        access_log=False,
    )
