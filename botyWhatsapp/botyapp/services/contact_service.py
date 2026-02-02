from django.conf import settings
from botyapp.models import Contact
from botyapp.services.whatsapp import (
    send_whatsapp_message,
    send_contact_message,
    send_image,
)
from logger import log
from datetime import datetime


class ContactService:
    """
    Servicio profesional para gestión de contactos y handover.
    Centraliza la lógica de interacción humana.
    """

    @staticmethod
    def handover_to_human(contact: Contact, image_id: str = None):
        """
        Ejecuta el protocolo de traspaso a humano.
        1. Desactiva el bot.
        2. Marca al contacto como 'Requiere Atención'.
        3. Notifica al cliente y al dueño.
        """
        try:
            # 1. Actualizar Estado del Contacto
            contact.is_bot_active = False
            contact.needs_human_attention = True
            contact.save(update_fields=["is_bot_active", "needs_human_attention"])

            log.info(f"🛑 Bot desactivado para {contact.name} (Handover solicitado)")

            # 2. Respuesta al Cliente
            response_text = (
                "¡Con gusto! ❤️\n\n"
                "He pausado mi respuestas automáticas para que un humano pueda atenderte.\n"
                "Un asesor revisará tu consulta y te escribirá pronto.\n\n"
                "✨ Gracias por tu paciencia."
            )
            send_whatsapp_message(contact.phone, response_text)

            # Enviar tarjeta de contacto del dueño (opcional, pero profesional)
            send_contact_message(contact.phone)

            # 3. Notificación al Dueño (Owner)
            owner_phone = settings.OWNER_PHONE_NUMBER
            notify_text = (
                f"🚨 *SOLICITUD DE ATENCIÓN* 🚨\n\n"
                f"👤 Cliente: {contact.name}\n"
                f"📱 Teléfono: {contact.phone}\n"
                f"🕒 Hora: {datetime.now().strftime('%H:%M')}\n\n"
                f"El cliente ha solicitado hablar con un vendedor. El bot ha sido PAUSADO para este chat."
            )
            send_whatsapp_message(owner_phone, notify_text)

            # Si hay una imagen asociada (contexto de la solicitud), reenviarla al dueño
            if image_id:
                log.debug(f"Reenviando imagen de contexto {image_id} al dueño")
                send_image(owner_phone, image_id)

            return True

        except Exception as e:
            log.error(f"Error en handover_to_human: {e}")
            return False

    @staticmethod
    def reactivate_bot(contact: Contact):
        """
        Reactiva el bot para un contacto.
        """
        try:
            contact.is_bot_active = True
            contact.needs_human_attention = False
            contact.save(update_fields=["is_bot_active", "needs_human_attention"])

            log.info(f"🤖 Bot reactivado para {contact.name}")
            return True
        except Exception as e:
            log.error(f"Error en reactivate_bot: {e}")
            return False
