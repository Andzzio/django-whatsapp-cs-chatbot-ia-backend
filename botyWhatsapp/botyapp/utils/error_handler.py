import time
import functools
from logger import log


class ErrorHandler:
    """
    Utilidad para manejo centralizado de errores y reintentos.
    """

    @staticmethod
    def retry_on_exception(max_retries=3, delay=1, backoff=2, exceptions=(Exception,)):
        """
        Decorador para reintentar operaciones falibles (como llamadas a API).
        """

        def decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                mtries, mdelay = max_retries, delay
                while mtries > 1:
                    try:
                        return func(*args, **kwargs)
                    except exceptions as e:
                        msg = f"{str(e)}, Retrying in {mdelay} seconds..."
                        log.warning(
                            f"⚠️ Retry ({max_retries - mtries + 1}/{max_retries}): {msg}"
                        )
                        time.sleep(mdelay)
                        mtries -= 1
                        mdelay *= backoff
                return func(*args, **kwargs)

            return wrapper

        return decorator

    @staticmethod
    def safe_execute(default_return=None):
        """
        Decorador para ejecutar funciones de forma segura sin romper el hilo principal.
        """

        def decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    log.error(f"❌ Critical Error in {func.__name__}: {e}")
                    return default_return

            return wrapper

        return decorator
