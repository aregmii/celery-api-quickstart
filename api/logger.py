import logging
import sys
from config import settings


def get_logger(name: str) -> logging.Logger:
    """
    Get a configured logger instance.
    
    Usage:
        from logger import get_logger
        logger = get_logger(__name__)
        logger.info("Message here")
    """
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(getattr(logging, settings.log_level.upper()))
    
    return logger