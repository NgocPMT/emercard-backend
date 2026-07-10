"""Run the API with ``python -m emercard``."""

import uvicorn

from emercard.core.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run("emercard.main:app", host=settings.host, port=settings.port, reload=settings.debug)
