import json
import threading
from datetime import datetime
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from logger import log
from .models import Contact, Message
from django.db import IntegrityError

# Importar Servicios
from .services.users import save_user_data, get_user_name, get_image_id
from .services.whatsapp import (
    mark_whatsapp_read,
    send_whatsapp_message,
    send_contact_message,
    send_catalog_message,
    send_image,
)
from .services.orders import process_order
from .services.message_handler import MessageHandler


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
            if "object" in data and "entry" in data:
                for entry in data["entry"]:
                    for change in entry.get("changes", []):
                        if change.get("field") == "messages":
                            value = change.get("value", {})
                            for contact in value.get("contacts", []):
                                client_number = contact.get("wa_id")
                                profile = contact.get("profile", {})
                                client_name = profile.get("name")
                                contact_obj, created = Contact.objects.get_or_create(
                                    phone=client_number, defaults={"name": client_name}
                                )
                                if not created and contact_obj.name != client_name:
                                    contact_obj.name = client_name
                                    contact_obj.save()
                                save_user_data(
                                    phone_number=client_number, client_name=client_name
                                )
                            if "messages" in value:
                                log.debug(
                                    f"📨 Datos del mensaje: {json.dumps(value, indent=2)}"
                                )
                                for message_event in value.get("messages", []):
                                    message_type = message_event.get("type")
                                    sender_id = message_event["from"]
                                    wamid = message_event.get("id")

                                    log.debug(
                                        f"RECUPERANDO EL NOMBRE DEL CLIENTE {get_user_name(sender_id)}"
                                    )

                                    # Lógica de Respuesta (Context / Reply)
                                    reply_to_msg = None
                                    context = message_event.get("context")
                                    if context and "id" in context:
                                        original_wamid = context["id"]
                                        try:
                                            reply_to_msg = Message.objects.filter(
                                                message_id=original_wamid
                                            ).first()
                                            if reply_to_msg:
                                                log.debug(
                                                    f"🔗 Mensaje es respuesta a: {original_wamid}"
                                                )
                                        except Exception as e:
                                            log.warning(
                                                f"Error buscando mensaje original: {e}"
                                            )

                                    if message_type == "order":
                                        contact_obj = Contact.objects.get(
                                            phone=sender_id
                                        )
                                        Message.objects.create(
                                            contact=contact_obj,
                                            text="*Cliente envió una orden de pedido*",
                                            is_bot=False,
                                            reply_to=reply_to_msg,
                                        )
                                        log.debug("🛒 ORDEN DETECTADA")
                                        order_data = message_event.get("order", {})
                                        process_order(order_data, sender_id)

                                    elif message_type == "image":
                                        contact_obj = Contact.objects.get(
                                            phone=sender_id
                                        )
                                        image = message_event.get("image")
                                        image_id = image.get("id")
                                        caption = image.get("caption", "")

                                        # Guardar mensaje del usuario SIEMPRE
                                        try:
                                            Message.objects.create(
                                                contact=contact_obj,
                                                text="*Imagen*",
                                                is_bot=False,
                                                message_type="image",
                                                reply_to=reply_to_msg,
                                                media_id=image_id,
                                                caption=caption,
                                                message_id=wamid,
                                            )
                                        except Exception as e:
                                            log.warning(
                                                f"Mensaje duplicado o error al guardar imagen: {e}"
                                            )

                                        if not contact_obj.is_bot_active:
                                            return JsonResponse(
                                                {"status": "bot_disabled"}, status=200
                                            )
                                        if contact_obj.bot_disabled_at:
                                            # Logic extracted for cleanliness? Or keep inline? Keep inline for now.
                                            message_timestamp = datetime.fromtimestamp(
                                                message_event.get("timestamp")
                                            )
                                            if (
                                                message_timestamp
                                                < contact_obj.bot_disabled_at
                                            ):
                                                return JsonResponse(
                                                    {"status": "old_message_ignored"},
                                                    status=200,
                                                )
                                        log.debug("🌄 IMAGEN DETECTADA")
                                        threading.Thread(
                                            target=MessageHandler.process_incoming,
                                            args=(
                                                sender_id,
                                                "",  # Texto vacío inicial
                                                message_event.get("timestamp"),
                                                wamid,  # message_id
                                                image_id,  # media_id
                                                "image",  # media_type
                                            ),
                                        ).start()

                                        # Responder al usuario que estamos pensando
                                        send_whatsapp_message(
                                            sender_id,
                                            "🔎 *Un segundo, estoy viendo tu foto...* 🧐",
                                        )

                                    elif message_type == "audio":
                                        contact_obj = Contact.objects.get(
                                            phone=sender_id
                                        )
                                        audio = message_event.get("audio")
                                        audio_id = audio.get("id")

                                        try:
                                            Message.objects.create(
                                                contact=contact_obj,
                                                text="*Nota de voz*",
                                                is_bot=False,
                                                message_id=wamid,
                                                reply_to=reply_to_msg,
                                                message_type="audio",
                                                media_id=audio_id,
                                            )
                                        except IntegrityError:
                                            pass

                                        log.debug("🎙️ AUDIO DETECTADO")

                                        threading.Thread(
                                            target=MessageHandler.process_incoming,
                                            args=(
                                                sender_id,
                                                "",  # Texto vacío
                                                message_event.get("timestamp"),
                                                wamid,
                                                audio_id,
                                                "audio",
                                            ),
                                        ).start()

                                    elif message_type == "interactive":
                                        contact_obj = Contact.objects.get(
                                            phone=sender_id
                                        )
                                        interactive = message_event.get("interactive")
                                        button_reply = interactive.get("button_reply")
                                        button_id = button_reply.get("id")

                                        # MARCAR LEIDO
                                        mark_whatsapp_read(wamid)

                                        if button_id == "button1":
                                            Message.objects.create(
                                                contact=contact_obj,
                                                text="*Cliente eligió contactar a un agente*",
                                                is_bot=False,
                                            )
                                        elif button_id == "button2":
                                            Message.objects.create(
                                                contact=contact_obj,
                                                text="*Cliente eligió acceder al catálogo*",
                                                is_bot=False,
                                            )

                                        if not contact_obj.is_bot_active:
                                            return JsonResponse(
                                                {"status": "bot_disabled"}, status=200
                                            )
                                        if contact_obj.bot_disabled_at:
                                            message_timestamp = datetime.fromtimestamp(
                                                message_event.get("timestamp")
                                            )
                                            if (
                                                message_timestamp
                                                < contact_obj.bot_disabled_at
                                            ):
                                                return JsonResponse(
                                                    {"status": "old_message_ignored"},
                                                    status=200,
                                                )

                                        if button_id == "button1":
                                            log.debug("✅ ACCEDIENDO AL CONTACTO")
                                            send_whatsapp_message(
                                                sender_id,
                                                "¡Con gusto linda!❤️\n\nEntiendo que deseas hablar directamente con nuestro encargado.\nÉl estará encantado de ayudarte con lo que necesites, ya sea una consulta especial o asesoría personalizada.\n\n✨Gracias por confiar en nosotros. Tu estilo merece atención directa\nCon cariño,\nTu equipo de moda femenina 💃",
                                            )
                                            send_contact_message(sender_id)
                                            notify = f"*NOTIFICACIÓN DE SOLICITUD DE AYUDA POR CLIENTE*\n- NUMERO DEL CLIENTE: {sender_id}\n- NOMBRE DEL CLIENTE: {get_user_name(sender_id)}"
                                            send_whatsapp_message(
                                                settings.OWNER_PHONE_NUMBER, notify
                                            )
                                            log.debug(
                                                f"MENSAJE ENVIADO EXITOSAMENTE: {notify}"
                                            )

                                            send_image(
                                                settings.OWNER_PHONE_NUMBER,
                                                get_image_id(sender_id),
                                            )
                                            log.debug("IMAGEN ENVIADA EXITOSAMENTE")
                                        if button_id == "button2":
                                            log.debug("✅ ACCEDIENDO AL CATALOGO")
                                            send_catalog_message(
                                                sender_id,
                                                "¡Aquí está nuestro catálogo completo! 🛍️✨ Explora todos nuestros productos.",
                                            )

                                    elif message_type == "text":
                                        message_id = message_event.get("id")
                                        # Deduplicación cache
                                        from django.core.cache import cache

                                        if not cache.add(
                                            f"wamid_{message_id}",
                                            "processed",
                                            timeout=3600,
                                        ):
                                            log.debug(
                                                f"🔁 Mensaje duplicado ignorado (cache hit): {message_id}"
                                            )
                                            continue

                                        raw_text = message_event["text"]["body"]
                                        timestamp = message_event.get("timestamp")

                                        threading.Thread(
                                            target=MessageHandler.process_incoming,
                                            args=(
                                                sender_id,
                                                raw_text,
                                                timestamp,
                                                message_id,
                                            ),
                                        ).start()
                                        continue

            return JsonResponse({"status": "received"}, status=200)

        except Exception as e:
            log.error(f"Error procesando webhook: {e}")
            return JsonResponse({"status": "error"}, status=500)

    return HttpResponse("Method not allowed", status=405)
