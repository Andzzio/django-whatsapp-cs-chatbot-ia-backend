from google.genai import types
from .base import BaseTool
from botyapp.services.whatsapp import send_catalog_message
from logger import log


class CatalogTool(BaseTool):
    """
    Herramienta para mostrar el catálogo de productos.
    """

    @property
    def name(self) -> str:
        return "show_catalog"

    @property
    def description(self) -> str:
        return (
            "Muestra el catálogo visual de productos al usuario. "
            "ÚSALA INMEDIATAMENTE si el usuario pide ver productos, catálogo, ropa o comprar."
        )

    @property
    def parameters_schema(self) -> types.Schema:
        return types.Schema(type=types.Type.OBJECT, properties={}, required=[])

    def execute(self, sender_id, **kwargs):
        log.debug(f"🛒 Ejecutando CatalogTool para {sender_id}")
        send_catalog_message(sender_id, "¡Aquí tienes el catálogo completo!")
        return {"status": "executed", "action": "show_catalog"}
