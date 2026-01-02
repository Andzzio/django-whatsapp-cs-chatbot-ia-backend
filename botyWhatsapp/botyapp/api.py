from django.http import JsonResponse, FileResponse
from django.views.decorators.csrf import csrf_exempt
from .models import Contact, Message, Order, OrderItem
from django.conf import settings
import pytz
import json
import requests
import os
from PIL import Image
import io
from django.utils import timezone
from .views import send_whatsapp_message
from django.core.cache import cache
from .services.catalog import sync_catalog_products
from logger import log


@csrf_exempt
def get_dashboard_stats(request):
    """
    Retorna las 10 métricas clave para el Dashboard de Ventas.
    """
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    # Cache Key per Token or Global? Global is fine for single tenant.
    cache_key = f"dashboard_stats_{token[-5:]}"
    cached_stats = cache.get(cache_key)
    if cached_stats:
        return JsonResponse(cached_stats)

    try:
        from django.db.models import Sum, Count

        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # 1. & 5. Ventas de Hoy & Ticket Promedio (Optimized Aggregation)
        today_metrics = (
            Order.objects.filter(created_at__gte=today_start)
            .exclude(status="CANCELLED")
            .aggregate(total_sales=Sum("total_amount"), total_count=Count("id"))
        )
        sales_today = today_metrics["total_sales"] or 0.0
        orders_today_count = today_metrics["total_count"] or 0

        avg_ticket = 0.0
        if orders_today_count > 0:
            avg_ticket = sales_today / orders_today_count

        # 2. Pedidos Pendientes
        pending_orders = Order.objects.filter(status="PENDING").count()

        # 3. & 4. Mensajes sin Leer y Chats Activos (Optimized)
        # Usamos una sola query para analizar mensajes de hoy si es posible,
        # pero Contact filters son distintos. Mantenemos count() que son eficientes.

        unread_chats = (
            Contact.objects.filter(messages__is_read=False, messages__is_bot=False)
            .distinct()
            .count()
        )

        active_chats_today = (
            Contact.objects.filter(messages__timestamp__gte=today_start)
            .distinct()
            .count()
        )

        conversion_rate = 0.0
        if active_chats_today > 0:
            conversion_rate = (orders_today_count / active_chats_today) * 100

        # 6. Producto Top (Ya estaba optimizado con queries, mantenemos)
        top_product_qs = (
            OrderItem.objects.filter(created_at__gte=today_start)
            .values("product_name")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        top_product = (
            top_product_qs[0]["product_name"] if top_product_qs.exists() else "N/A"
        )

        # 7. Clientes Nuevos
        new_clients = Contact.objects.filter(created_at__gte=today_start).count()

        # 8. Ventas Mes (Optimized Aggregation)
        month_metrics = (
            Order.objects.filter(created_at__gte=month_start)
            .exclude(status="CANCELLED")
            .aggregate(total_sales=Sum("total_amount"))
        )
        sales_month = month_metrics["total_sales"] or 0.0

        # 9. Ahorro Tiempo IA (Optimized)
        bot_msgs_today = Message.objects.filter(
            timestamp__gte=today_start, is_bot=True
        ).count()
        hours_saved = (bot_msgs_today * 1.5) / 60

        # 10. Estado Catálogo
        catalog_synced = (
            cache.get(f"catalog_products_{settings.CATALOG_ID}") is not None
        )
        catalog_status = "Online" if catalog_synced else "Sync Needed"

        stats = {
            "sales_today": float(sales_today),
            "pending_orders": pending_orders,
            "unread_chats": unread_chats,
            "conversion_rate": round(conversion_rate, 1),
            "avg_ticket": float(round(avg_ticket, 2)),
            "top_product": top_product,
            "new_clients": new_clients,
            "sales_month": float(sales_month),
            "hours_saved": round(hours_saved, 1),
            "catalog_status": catalog_status,
        }

        # Cache for 60 seconds
        cache.set(cache_key, stats, timeout=60)

        return JsonResponse(stats)

    except Exception as e:
        log.error(f"Error calculating stats: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def get_chat_history(request):
    """
    Endpoint for lazy loading chat history.
    """
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    phone_number = request.GET.get("phone")
    if not phone_number:
        return JsonResponse({"error": "Missing phone"}, status=400)

    before_id = request.GET.get("before_id")
    limit = int(request.GET.get("limit", 50))

    try:
        contact = Contact.objects.get(phone=phone_number)

        # Base query
        qs = contact.messages.all().order_by("-timestamp")

        if before_id:
            try:
                qs = qs.filter(id__lt=int(before_id))
            except ValueError:
                pass

        messages_slice = qs[:limit]

        msgs = []
        # Revertir para orden cronológico (viejo -> nuevo)
        for m in reversed(messages_slice):
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

        return JsonResponse({"messages": msgs})

    except Contact.DoesNotExist:
        return JsonResponse({"error": "Contact not found"}, status=404)
    except Exception as e:
        print(f"Error fetching history: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def sync_data(request):
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)
    if request.method == "GET":
        from django.db.models import Prefetch, Max

        try:
            limit = int(request.GET.get("limit", 20))
            offset = int(request.GET.get("offset", 0))
        except ValueError:
            limit = 20
            offset = 0

        # Anotar última actividad para ordenamiento eficiente en DB
        contacts_qs = Contact.objects.annotate(
            last_msg_time=Max("messages__timestamp")
        ).order_by("-last_msg_time")

        # Paginación a nivel de base de datos
        contacts_page = contacts_qs[offset : offset + limit]

        # Fetch IDs to prefetch correctly for just this page
        contact_ids = [c.id for c in contacts_page]

        # Re-query with prefetch only for the paged contacts to avoid memory explosion
        contacts_final = (
            Contact.objects.filter(id__in=contact_ids)
            .prefetch_related(
                Prefetch(
                    "messages",
                    queryset=Message.objects.order_by("-timestamp"),
                    to_attr="prefetched_messages",
                )
            )
            .annotate(last_msg_time=Max("messages__timestamp"))
            .order_by("-last_msg_time")
        )

        response_data = []

        for contact in contacts_final:
            msgs = []

            # Usar los mensajes pre-cargados en memoria
            recent_messages = contact.prefetched_messages[:50]

            for m in reversed(recent_messages):
                reply_info = None
                if m.reply_to:
                    # Nota: reply_to puede generar N+1 si no se hace select_related/prefetch.
                    # Para simplicidad ahora, asumimos que es poco frecuente o aceptable,
                    # pero idealmente se optimizaría también.
                    try:
                        reply_obj = m.reply_to
                        reply_info = {
                            "id": reply_obj.id,
                            "text": reply_obj.text,
                            "type": reply_obj.message_type,
                            "media_id": reply_obj.media_id,
                            "sender_name": (
                                reply_obj.contact.name
                                if not reply_obj.is_bot
                                else "Bot"
                            ),
                        }
                    except Exception:
                        pass  # Handle deleted messages

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

            # Unread count optimization needed?
            # contact.messages.filter(...) hits DB. Better to count in python from prefetch if mostly recent?
            # Or use annotation. Let's stick to simple efficient queries for now or annotation.
            # Annotation is cleaner. Let's do a subquery count or just filter efficiently.
            # For now keeping it simple: The N+1 here is for unread_count.
            # Let's count in python from the list if possible, but unread might be older.
            # We will accept N queries for unread_count for now or optimize later.
            # Actually, `annotate(unread_count=Count('messages', filter=Q(messages__is_read=False, messages__is_bot=False)))`

            unread_count = contact.messages.filter(is_read=False, is_bot=False).count()

            response_data.append(
                {
                    "name": contact.name,
                    "phone": contact.phone,
                    "is_bot_active": contact.is_bot_active,
                    "unread_count": unread_count,
                    "needs_human_attention": contact.needs_human_attention,
                    "history": msgs,
                    "last_activity": (
                        contact.last_msg_time.isoformat()
                        if contact.last_msg_time
                        else contact.created_at.isoformat()
                    ),
                    "tags": contact.tags,
                }
            )

        return JsonResponse({"contacts": response_data}, safe=False)

    return JsonResponse({"error": "Método no permitido"}, status=405)


@csrf_exempt
def get_media(request, media_id):
    """
    Obtiene medios optimizados:
    1. Busca en disco local (media/whatsapp_cache/...).
    2. Si no está, descarga de Meta con STREAMING (piping).
    3. Guarda en disco en segundo plano (o mientras streamea) y sirve al response.
    """
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    try:
        # Asegurar directorio de caché
        cache_dir = os.path.join(settings.BASE_DIR, "media", "whatsapp_cache")
        os.makedirs(cache_dir, exist_ok=True)
        file_path = os.path.join(
            cache_dir, f"{media_id}.jpg"
        )  # Asumimos JPG para img por defecto

        # 1. HIT DE CACHÉ LOCAL
        if os.path.exists(file_path):
            return FileResponse(open(file_path, "rb"), content_type="image/jpeg")

        # 2. MISS DE CACHÉ - Descargar de Meta
        url = f"https://graph.facebook.com/v21.0/{media_id}"
        headers = {"Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}"}
        resp_url = requests.get(url, headers=headers, timeout=10)
        resp_url.raise_for_status()
        media_url = resp_url.json().get("url")

        # Descarga con Streaming
        media_response = requests.get(
            media_url, headers=headers, stream=True, timeout=20
        )
        media_response.raise_for_status()

        # Optimización + Guardado (Usamos memoria intermedia para PIL)
        # Nota: Para streaming puro directo al cliente sin guardar, se usa StreamingHttpResponse(media_response.raw).
        # Pero queremos comprimir y guardar. Esto requiere leerlo todo en RAM o por chunks.
        # Para "compresión imperceptible" y "guardado", la mejor estrategia balanceada es:
        # Leer -> PIL Compress -> Guardar Disco -> Servir archivo.
        # (El verdadero streaming pass-through no permite compresión al vuelo fácil sin steps complejos).

        # Leemos el contenido (si es muy grande, esto consume RAM, pero las imgs de WA suelen ser <5MB)
        # Si queremos streaming REAL de bajada -> subida sin tocar RAM:
        # return StreamingHttpResponse(media_response.raw, content_type=media_response.headers.get("Content-Type"))
        # Pero usuario pidió COMPRESIÓN y CACHÉ.

        image = Image.open(io.BytesIO(media_response.content))

        # Redimensionar seguro (Max 1920px)
        if image.width > 1920 or image.height > 1920:
            image.thumbnail((1920, 1920))

        # Convertir a RGB si es necesario
        if image.mode in ("RGBA", "P"):
            image = image.convert("RGB")

        # Guardar en disco comprimido
        image.save(file_path, "JPEG", quality=85, optimize=True)

        # Servir desde el archivo recién creado
        return FileResponse(open(file_path, "rb"), content_type="image/jpeg")

    except Exception as e:
        log.error(f"Error serving media {media_id}: {e}")
        # Fallback a error json
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

                # Limpieza automática de alerta
                if contact.needs_human_attention:
                    contact.needs_human_attention = False
                    contact.save()

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

            # Limpieza automática de alerta de ayuda
            try:
                contact = Contact.objects.get(phone=phone)
                if contact.needs_human_attention:
                    contact.needs_human_attention = False
                    contact.save()
            except Contact.DoesNotExist:
                pass

            return JsonResponse({"status": "success", "message": "sent"})
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def get_products_list(request):
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method == "GET":
        force_sync = request.GET.get("force_sync", "false").lower() == "true"
        cache_key = f"catalog_products_{settings.CATALOG_ID}"
        products_dict = cache.get(cache_key)

        # LAZY ASYNC REFRESH
        # Si no hay caché o se fuerza, intentamos sincronizar.
        # Si se fuerza, bloqueamos (el usuario pidió sync explícito).
        # Si no se fuerza y está vacío, bloqueamos (necesitamos data inicial).
        # MEJORA: Podríamos tener un "stale cache" (cache vieja) y actualizar en background.

        if force_sync:
            try:
                products_dict = sync_catalog_products(settings.CATALOG_ID)
            except Exception as e:
                return JsonResponse({"error": str(e)}, status=500)
        elif not products_dict:
            # Vacío -> Sincronizar (Bloqueante primera vez)
            try:
                products_dict = sync_catalog_products(settings.CATALOG_ID)
            except Exception as e:
                log.error(f"Sync fail: {e}")
                products_dict = {}

        # Si tenemos data pero podría estar vieja (ej: lógica futura de TTL),
        # aquí podríamos disparar un hilo background. Por ahora confiamos en el cache TTL de Django.

        products_list = []
        if products_dict:
            for retailer_id, product_data in products_dict.items():
                products_list.append(product_data)

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

            # Limpieza automática de alerta
            if contact.needs_human_attention:
                contact.needs_human_attention = False
                contact.save()

            # Enviar mensaje de producto usando servicio existente
            from botyapp.services.whatsapp import send_product_message

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


@csrf_exempt
def send_catalog_to_contact(request, phone):
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method == "POST":
        try:
            contact = Contact.objects.get(phone=phone)

            # Limpieza automática de alerta
            if contact.needs_human_attention:
                contact.needs_human_attention = False
                contact.save()

            # Enviar catálogo completo usando servicio existente
            from botyapp.services.whatsapp import send_catalog_message

            send_catalog_message(phone, "¡Aquí está nuestro catálogo completo! 🛍️✨")

            # Registrar en BD como mensaje del bot
            Message.objects.create(
                contact=contact,
                text="📂 Catálogo enviado",
                is_bot=True,
                message_type="catalog",
            )

            return JsonResponse({"status": "success"})

        except Contact.DoesNotExist:
            return JsonResponse({"error": "Contact not found"}, status=404)
        except Exception as e:
            log.error(f"Error sending catalog: {e}")
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Method not allowed"}, status=405)


@csrf_exempt
def generate_embeddings_endpoint(request):
    """
    Endpoint para generar embeddings de productos bajo demanda.
    Solo ejecutar una vez después del deploy inicial.
    
    Uso:
    curl -X POST https://tu-app.onrender.com/api/generate-embeddings/ \
      -H "Authorization: Bearer TU_DASH_TOKEN"
    """
    # Validar autenticación
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        from botyapp.services.intelligence.semantic_search import semantic_search
        from botyapp.models import ProductEmbedding

        # Sincronizar productos desde Meta Commerce
        log.info("🚀 Iniciando vectorización de catálogo...")
        products_dict = sync_catalog_products(settings.CATALOG_ID)

        if not products_dict:
            return JsonResponse(
                {
                    "status": "error",
                    "message": "No se pudieron obtener productos del catálogo",
                },
                status=500,
            )

        vectorized = 0
        skipped = 0
        errors = 0

        for retailer_id, product_data in products_dict.items():
            try:
                # Verificar si ya existe (evitar duplicados)
                if ProductEmbedding.objects.filter(retailer_id=retailer_id).exists():
                    skipped += 1
                    continue

                # Preparar texto para embedding
                name = product_data.get("name", "")
                description = product_data.get("description", "")
                category = product_data.get("category", "")
                search_text = f"{name} {description} {category}".strip().lower()

                # Parsear precio (viene como "S/40,00" o dict con 'amount')
                price_value = None
                price_raw = product_data.get("price")
                if isinstance(price_raw, dict):
                    # Formato dict: {'amount': '40.00', 'currency': 'PEN'}
                    price_value = float(price_raw.get("amount", 0))
                elif isinstance(price_raw, str):
                    # Formato string: "S/40,00" -> 40.00
                    try:
                        price_clean = (
                            price_raw.replace("S/", "").replace(",", ".").strip()
                        )
                        price_value = float(price_clean)
                    except Exception:
                        price_value = None
                elif isinstance(price_raw, (int, float)):
                    price_value = float(price_raw)

                # Generar embedding con Gemini
                embedding = semantic_search.generate_embedding(search_text)

                if not embedding:
                    log.warning(f"No se pudo generar embedding para: {name[:50]}")
                    errors += 1
                    continue

                # Crear en base de datos
                ProductEmbedding.objects.create(
                    retailer_id=retailer_id,
                    product_name=name,
                    description=description,
                    price=price_value,
                    category=category,
                    image_url=product_data.get(
                        "image_url"
                    ),  # NUEVO: Guardar URL de imagen
                    embedding_vector=embedding,
                    search_text=search_text,
                    is_available=True,
                    stock_quantity=10,
                    last_synced=timezone.now(),
                )

                vectorized += 1

                if vectorized % 10 == 0:
                    log.info(f"Vectorizados: {vectorized}...")

            except Exception as e:
                log.error(f"Error procesando {retailer_id}: {e}")
                errors += 1
                continue

        total_in_db = ProductEmbedding.objects.count()

        log.info(f"✅ Vectorización completa: {vectorized} nuevos")

        return JsonResponse(
            {
                "status": "success",
                "products_vectorized": vectorized,
                "products_skipped": skipped,
                "errors": errors,
                "total_in_database": total_in_db,
            }
        )

    except Exception as e:
        log.error(f"Error en generate_embeddings_endpoint: {e}")
        return JsonResponse({"status": "error", "message": str(e)}, status=500)
