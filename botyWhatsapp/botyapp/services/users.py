from .whatsapp import send_whatsapp_message
from botyapp.models import Contact
from botyapp.core.session import SessionManager
import threading
from logger import log

# Diccionario global para gestionar timers de re-enganche (legacy, se mantendrá en memoria por ahora)
user_timers = {}


def get_user_name(phone_number):
    return SessionManager(phone_number).get_name()


def save_user_data(phone_number, client_name=None, context=None, image_id=None):
    session = SessionManager(phone_number)
    if client_name:
        session.set_name(client_name)
    if context:
        session.update_history(context)
    if image_id:
        session.set_context_image_id(image_id)

    # Retornamos dict simulado por compatibilidad si alguien lo usa
    return session._get_data()


def get_context(phone_number):
    return SessionManager(phone_number).get_history()


def get_image_id(phone_number):
    return SessionManager(phone_number).get_context_image_id()


def cancel_timer(sender_id):
    """Cancela cualquier timer activo para este usuario"""
    if sender_id in user_timers:
        try:
            user_timers[sender_id].cancel()
            del user_timers[sender_id]
            log.debug(f"⏱️ Timer cancelado para {sender_id}")
        except Exception as e:
            log.error(f"Error cancelando timer: {e}")


def send_reengagement_message(sender_id):
    """Envía un mensaje de recuperación si el usuario está inactivo"""
    try:
        # Verificar si el bot sigue activo para este usuario
        try:
            contact = Contact.objects.get(phone=sender_id)
            if not contact.is_bot_active:
                log.debug(
                    f"🛑 Re-enganche cancelado para {sender_id}: Bot desactivado."
                )
                if sender_id in user_timers:
                    del user_timers[sender_id]
                return
        except Contact.DoesNotExist:
            log.warning(f"⚠️ Contacto no encontrado para re-enganche: {sender_id}")
            return

        log.debug(f"⏰ Ejecutando re-enganche para {sender_id}")

        message = (
            "¿Sigues ahí? 👀\n\n"
            "No quería que te perdieras estos modelos que están volando. 🚀\n"
            "Si tienes alguna duda sobre tallas o precios, ¡estoy aquí para ayudarte! 💖"
        )
        send_whatsapp_message(sender_id, message)

        # Limpiar timer del diccionario
        if sender_id in user_timers:
            del user_timers[sender_id]

    except Exception as e:
        log.error(f"Error en re-enganche: {e}")


def start_timer(sender_id):
    """Inicia un nuevo timer de 5 minutos"""
    cancel_timer(sender_id)
    # 300 segundos = 5 minutos
    timer = threading.Timer(300, send_reengagement_message, [sender_id])
    user_timers[sender_id] = timer
    timer.start()
    log.debug(f"⏱️ Nuevo timer iniciado para {sender_id} (5 min)")
