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
            "Envía el catálogo visual de productos al usuario. "
            "IMPORTANTE: Debes pasar el mensaje de introducción en el parámetro 'message'. "
            "Este será el ÚNICO mensaje que verá el usuario, así que sé amable y vendedor."
        )

    @property
    def parameters_schema(self) -> types.Schema:
        return types.Schema(
            type=types.Type.OBJECT,
            properties={
                "message": types.Schema(
                    type=types.Type.STRING,
                    description="El texto que acompañará al catálogo (ej: 'Aquí tienes nuestra colección exclusiva ✨')",
                )
            },
            required=["message"],
        )

    def execute(self, sender_id, **kwargs):
        message = kwargs.get("message", "¡Aquí tienes el catálogo completo! 🛍️")
        log.debug(f"🛒 Ejecutando CatalogTool para {sender_id} con mensaje: {message}")

        # Enviar mensaje unificado (Catálogo + Texto)
        result = send_catalog_message(sender_id, body_text=message)

        if result:
            # Retornamos un indicador especial para que el Engine sepa que ya "hablamos"
            return {
                "status": "executed",
                "action": "show_catalog",
                "sent_message": message,
                "response_type": "interactive_response",  # Flag para el futuro si queremos silenciar al LLM
            }
        else:
            return {
                "status": "failed",
                "error": "Could not send catalog message via WhatsApp API. Check server logs and CATALOG_ID.",
            }
