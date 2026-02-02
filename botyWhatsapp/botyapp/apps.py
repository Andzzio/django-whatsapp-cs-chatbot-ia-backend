from django.apps import AppConfig


class BotyappConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "botyapp"

    def ready(self):
        """Importar signals al iniciar app"""
        import botyapp.signals  # noqa
