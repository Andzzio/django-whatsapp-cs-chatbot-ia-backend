import json
import threading

from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from logger import log
from .models import Message

# Importar Servicios
from .services.message_handler import handle_incoming_message


def health_check(request):
    """Responde a la verificación de estado de Render."""
    return HttpResponse("Bot is running", status=200)


@csrf_exempt
def whatsapp_webhook(request):
    if request.method == "GET":
        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")

        if mode and token:
            if mode == "subscribe" and token == settings.VERIFY_TOKEN:
                log.debug("WEBHOOK_verified")
                return HttpResponse(challenge, status=200)
            else:
                return HttpResponse("Verification token mismatch", status=403)
    elif request.method == "POST":
        try:
            data = json.loads(request.body.decode("utf-8"))
            # Validar estructura mínima
            if "object" in data and "entry" in data:
                # Disparar el handler en background para no bloquear el webhook de Meta
                threading.Thread(target=handle_incoming_message, args=(data,)).start()

            return JsonResponse({"status": "received"}, status=200)

        except Exception as e:
            log.error(f"Error procesando webhook: {e}")
            return JsonResponse({"status": "error"}, status=500)

    return HttpResponse("Method not allowed", status=405)


@csrf_exempt
def delete_message(request, msg_id):
    """
    Endpoint para borrar un mensaje por su ID interno.
    Requiere token de autenticación del dashboard.
    """
    if request.method != "DELETE":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    try:
        message = Message.objects.get(id=msg_id)
        message.delete()
        return JsonResponse({"status": "deleted"}, status=200)
    except Message.DoesNotExist:
        return JsonResponse({"error": "Message not found"}, status=404)
    except Exception as e:
        log.error(f"Error deleting message: {e}")
        return JsonResponse({"error": str(e)}, status=500)
