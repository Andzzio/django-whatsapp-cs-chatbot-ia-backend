from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from .models import Contact, Message
from django.conf import settings
import pytz
import json
import requests
from django.utils import timezone
from .views import send_whatsapp_message
from django.core.cache import cache
from .services.catalog import sync_catalog_products
from .services.whatsapp import send_product_message
from logger import log


@csrf_exempt
def sync_data(request):
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)
    if request.method == "GET":
        response_data = []
        contacts = Contact.objects.all()

        for contact in contacts:
            msgs = []

            for m in contact.messages.all().order_by("timestamp"):
                reply_info = None
                if m.reply_to:
                    reply_info = {
                        "id": m.reply_to.id,
                        "text": m.reply_to.text,
                        "type": m.reply_to.message_type,
                        "media_id": m.reply_to.media_id,
                        "sender_name": (
                            m.reply_to.contact.name if not m.reply_to.is_bot else "Bot"
                        ),
                    }

                msgs.append(
                    {
                        "id": m.id,
                        "user": "BOTY" if m.is_bot else contact.name,
                        "text": m.text,
                        "time": m.timestamp.astimezone(
                            pytz.timezone("America/Lima")
                        ).strftime("%H:%M"),
                        "is_bot": m.is_bot,
                        "type": m.message_type,
                        "media_id": m.media_id,
                        "caption": m.caption,
                        "is_read": m.is_read,
                        "reply_to": reply_info,
                    }
                )
            response_data.append(
                {
                    "name": contact.name,
                    "phone": contact.phone,
                    "is_bot_active": contact.is_bot_active,
                    "unread_count": contact.messages.filter(
                        is_read=False, is_bot=False
                    ).count(),
                    "history": msgs,
                }
            )
        return JsonResponse({"contacts": response_data}, safe=False)

    return JsonResponse({"error": "Método no permitido"}, status=405)


@csrf_exempt
def get_media(request, media_id):
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    try:
        # 1. Obtener URL de descarga
        url = f"https://graph.facebook.com/v21.0/{media_id}"
        headers = {"Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}"}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        media_url = response.json().get("url")

        # 2. Descargar binario
        media_response = requests.get(media_url, headers=headers, stream=True)
        media_response.raise_for_status()

        content_type = media_response.headers.get("Content-Type")
        return HttpResponse(media_response.content, content_type=content_type)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def send_media_message(request, phone):
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method == "POST" and request.FILES.get("file"):
        try:
            file = request.FILES["file"]
            media_type = request.POST.get("type", "image")  # image, video, audio
            caption = request.POST.get("caption", "")

            # 1. Subir a WhatsApp
            url_upload = f"https://graph.facebook.com/v21.0/{settings.ID_NUMERO}/media"
            headers = {"Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}"}
            files = {"file": (file.name, file, file.content_type)}
            data_upload = {"messaging_product": "whatsapp"}

            response_upload = requests.post(
                url_upload, headers=headers, files=files, data=data_upload
            )
            response_upload.raise_for_status()
            media_id = response_upload.json().get("id")

            # 2. Enviar Mensaje
            url_msg = settings.WHATSAPP_URL
            headers_msg = {
                "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
                "Content-Type": "application/json",
            }

            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": media_type,
                media_type: {
                    "id": media_id,
                    "caption": caption if media_type != "audio" else None,
                },
            }

            response_msg = requests.post(url_msg, headers=headers_msg, json=payload)
            response_msg.raise_for_status()

            # 3. Guardar en DB
            try:
                contact = Contact.objects.get(phone=phone)
                Message.objects.create(
                    contact=contact,
                    text=f"*{media_type.capitalize()} enviado*",
                    is_bot=True,
                    message_type=media_type,
                    media_id=media_id,
                    caption=caption,
                )
            except Contact.DoesNotExist:
                pass

            return JsonResponse({"status": "success", "media_id": media_id})

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def toggle_bot_status(request, phone):
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)
    if request.method == "POST":
        try:
            contact = Contact.objects.get(phone=phone)
        except Contact.DoesNotExist:
            return JsonResponse({"error": "Contact not found"}, status=404)
        data = json.loads(request.body)
        is_active = data.get("is_active")
        contact.is_bot_active = is_active
        if is_active is False:
            contact.bot_disabled_at = timezone.now()
        else:
            contact.bot_disabled_at = None
        contact.save()
        return JsonResponse(
            {"status": "success", "is_bot_active": contact.is_bot_active}
        )


@csrf_exempt
def mark_messages_read(request, phone):
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method == "POST":
        try:
            contact = Contact.objects.get(phone=phone)
            # Marcar como leídos solo los mensajes recibidos (is_bot=False)
            contact.messages.filter(is_bot=False, is_read=False).update(is_read=True)
            return JsonResponse({"status": "success"})
        except Contact.DoesNotExist:
            return JsonResponse({"error": "Contact not found"}, status=404)
    return JsonResponse({"error": "Method not allowed"}, status=405)


@csrf_exempt
def send_message_to_contact(request, phone):
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)
    if request.method == "POST":
        try:
            # Check if contact exists first
            if not Contact.objects.filter(phone=phone).exists():
                return JsonResponse({"error": "Contact not found"}, status=404)
            # contact = Contact.objects.get(phone=phone) # Unused variable removed

            data = json.loads(request.body)
            text = data.get("text")
            reply_to_db_id = data.get("reply_to_id")  # ID numérico de la DB (Django)

            if not text or text.strip() == "":
                return JsonResponse({"error": "Text Empty"}, status=400)

            wamid_context = None
            if reply_to_db_id:
                try:
                    # Buscamos el mensaje en la DB para obtener el WAMID real (necesario para la API)
                    original_msg = Message.objects.get(id=reply_to_db_id)
                    wamid_context = original_msg.message_id
                    log.debug(
                        f"Replying to DB_ID:{reply_to_db_id} -> WAMID:{wamid_context}"
                    )
                except Message.DoesNotExist:
                    log.warning(f"Reply ID {reply_to_db_id} not found in DB.")

            send_whatsapp_message(phone, text, reply_to_message_id=wamid_context)
            return JsonResponse({"status": "success", "message": "sent"})
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def get_products_list(request):
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method == "GET":
        cache_key = f"catalog_products_{settings.CATALOG_ID}"
        products_dict = cache.get(cache_key)

        # Si no hay productos en caché, intentar sincronizar
        if not products_dict:
            try:
                products_dict = sync_catalog_products(settings.CATALOG_ID)
            except Exception as e:
                return JsonResponse(
                    {"error": f"Error syncing catalog: {str(e)}"}, status=500
                )

        # Convertir dict a list para el frontend
        products_list = []
        if products_dict:
            for retailer_id, product_data in products_dict.items():
                products_list.append(product_data)

        # DEBUG: Si está vacía, puede ser que el sync falló silenciosamente o no hay productos.
        # Podríamos agregar un log aquí si tuviéramos acceso a consola, pero por ahora confiamos en el fix.

        return JsonResponse({"products": products_list})

    return JsonResponse({"error": "Method not allowed"}, status=405)


@csrf_exempt
def send_product_to_contact(request, phone):
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method == "POST":
        try:
            contact = Contact.objects.get(phone=phone)
            data = json.loads(request.body)
            retailer_id = data.get("retailer_id")

            if not retailer_id:
                return JsonResponse({"error": "retailer_id required"}, status=400)

            # Recuperar datos del producto del caché para enriquecer el mensaje
            cache_key = f"catalog_products_{settings.CATALOG_ID}"
            products_dict = cache.get(cache_key) or {}
            product_data = products_dict.get(retailer_id)

            # Enviar mensaje de producto usando servicio existente
            send_product_message(
                phone, settings.CATALOG_ID, retailer_id, product_data=product_data
            )

            # Registrar en BD como mensaje del bot
            # Intentar obtener info del producto para el caption/texto local
            products_dict = cache.get("products_dict") or {}
            product_info = products_dict.get(retailer_id, {})
            product_name = product_info.get("name", retailer_id)

            Message.objects.create(
                contact=contact,
                text=f"📦 Producto enviado: {product_name}",
                is_bot=True,
                message_type="product",  # Nuevo tipo, frontend lo manejará como text o custom
                media_id=retailer_id,  # Usamos media_id para guardar el product ID
            )

            return JsonResponse({"status": "success"})

        except Contact.DoesNotExist:
            return JsonResponse({"error": "Contact not found"}, status=404)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Method not allowed"}, status=405)
