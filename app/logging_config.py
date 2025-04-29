# app/logging_config.py

import logging
from datetime import datetime
import os

os.makedirs("logs", exist_ok=True)

log_file = f"logs/babai_conversations_{datetime.now().strftime('%Y-%m-%d')}.log"

logger = logging.getLogger("babai-logger")
logger.setLevel(logging.INFO)

file_handler = logging.FileHandler(log_file)
file_handler.setLevel(logging.INFO)

formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
file_handler.setFormatter(formatter)

# Avoid duplicate logs if re-run
if not logger.handlers:
    logger.addHandler(file_handler)


logger.propagate = False  # prevent Uvicorn from hijacking
