# global utils.
import logging
from functools import wraps


def log_with(message_fn):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            try:
                msg = message_fn(*args, **kwargs)
                logging.info(msg)
            except Exception as e:
                logging.warning(f"Logging failed: {e}")
            return result

        return wrapper

    return decorator
