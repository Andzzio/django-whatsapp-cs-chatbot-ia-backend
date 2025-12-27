import threading
from django.core.cache import cache
from logger import log
from .whatsapp import send_whatsapp_message

# Diccionario global para gestionar timers de re-enganche
user_timers = {}


def get_user_name(phone_number):
    cache_key = f"Client_{phone_number}"
    client_data = cache.get(cache_key)
    if client_data and client_data.get("client_name"):
        return client_data["client_name"]
    else:
        return "Cliente"


def save_user_data(phone_number, client_name=None, context=None, image_id=None):
    cache_key = f"Client_{phone_number}"
    client_data = cache.get(cache_key, {})
    if client_name:
        client_data["client_name"] = client_name
    if context:
        client_data["context"] = context
    if image_id:
        client_data["image_id"] = image_id
    client_data["phone_number"] = phone_number

    cache.set(cache_key, client_data, timeout=60 * 60 * 24 * 30)
    log.debug(f"Cliente Guardado {client_data}")
    return client_data


def get_context(phone_number):
    cache_key = f"Client_{phone_number}"
    client_data = cache.get(cache_key)
    if client_data:
        ctx = client_data.get("context")
        # Si es una lista (nuevo formato), la devolvemos
        if isinstance(ctx, list):
            return ctx
        # Si es string (formato antiguo) o predeterminado, devolvemos lista vacía para resetear
        return []
    else:
        return []


def get_image_id(phone_number):
    cache_key = f"Client_{phone_number}"
    client_data = cache.get(cache_key)
    if client_data and client_data.get("image_id"):
        return client_data["image_id"]
    else:
        return None


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
