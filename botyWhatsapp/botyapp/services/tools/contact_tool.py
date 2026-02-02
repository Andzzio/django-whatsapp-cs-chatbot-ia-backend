from google.genai import types
from .base import BaseTool
from botyapp.services.whatsapp import send_whatsapp_message, send_contact_message
from logger import log


class ContactTool(BaseTool):
    """
    Herramienta para enviar el contacto de soporte/humano.
    """

    @property
    def name(self) -> str:
        return "show_contact"

    @property
    def description(self) -> str:
        return (
            "Ejecutarás esta función y no devolverás una respuesta textual cuando el usuario solicite "
            "hablar con el dueño, una persona real, agente, gerente, encargado, o agente especializado."
        )

    @property
    def parameters_schema(self) -> types.Schema:
        return types.Schema(type=types.Type.OBJECT, properties={}, required=[])

    def execute(self, sender_id, **kwargs):
        log.debug(f"📞 Ejecutando ContactTool para {sender_id}")
        send_whatsapp_message(sender_id, "Claro, aquí tienes el contacto directo:")
        send_contact_message(sender_id)
        return {"status": "executed", "action": "show_contact"}
