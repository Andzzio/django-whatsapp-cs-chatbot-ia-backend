import json
import requests
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from google import genai
from google.genai import types
from django.core.cache import cache
from datetime import datetime
import pytz
from logger import log
import time
import random
import threading
from .models import Contact, Message
from django.db import IntegrityError
from PIL import Image
import io
import difflib # ✨ Fuzzy Matching para búsqueda certera


# COMENTARIO PARA HACER UN TEST COMMIT

IA_KEY = settings.IA_TOKEN

client = genai.Client(api_key=IA_KEY)

# Diccionario global para gestionar timers de re-enganche
user_timers = {}


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
        # Verificar si el último mensaje fue hace más de 5 min (doble check opcional)
        # Por simplicidad, enviamos el mensaje directo
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


def health_check(request):
    """Responde a la verificación de estado de Render."""
    # Código 200 OK
    return HttpResponse("Bot is running", status=200)


def button_tool():
    """Herramienta para mostrar el catálogo de productos"""
    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="show_catalog",
                description="Muestra el catálogo de productos cuando el usuario solicita ver productos, el catálogo, o quiere comprar algo",
                parameters=types.Schema(
                    type=types.Type.OBJECT, properties={}, required=[]
                ),
            ),
            types.FunctionDeclaration(
                name="show_contact",
                description="Ejecutarás esta función y no devolverás una respuesta textual cuando el usuario solicite hablar con el dueño, una persona real, agente, gerente, encargado, agente especializado, Nunca pasarás números inventados ni contactos inventados.",
                parameters=types.Schema(
                    type=types.Type.OBJECT, properties={}, required=[]
                ),
            ),
            types.FunctionDeclaration(
                name="recommend_products",
                description="Usa esta función cuando el usuario busque un tipo de producto específico (ej: 'pantalones', 'vestidos', 'ofertas'). Filtra y muestra productos relevantes con imagen.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "search_term": types.Schema(
                            type=types.Type.STRING,
                            description="Término de búsqueda o categoría (ej: 'pantalón', 'falda', 'azul')",
                        )
                    },
                    required=["search_term"],
                ),
            ),
        ]
    )


def sync_catalog_products(catalog_id):
    """
    Sincroniza todos los productos del catálogo y los guarda en caché
    """
    url = f"https://graph.facebook.com/v21.0/{catalog_id}/products"
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
    }
    params = {
        "fields": "id,name,description,price,sale_price,retailer_id,image_url",
        "limit": 100,
    }

    products_dict = {}

    try:
        while url:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()

            # Guardar productos en el diccionario usando retailer_id como clave
            for product in data.get("data", []):
                retailer_id = product.get("retailer_id")
                if retailer_id:
                    products_dict[retailer_id] = {
                        "name": product.get("name", "Sin nombre"),
                        "price": product.get("price", 0),
                        "sale_price": product.get("sale_price"),
                        "description": product.get("description", ""),
                        "retailer_id": retailer_id,
                        "image_url": product.get("image_url", ""),
                    }

            # Verificar si hay más páginas
            url = data.get("paging", {}).get("next")
            if url:
                params = {}  # Los parámetros ya vienen en la URL de next

        # Guardar en caché por 1 hora
        cache.set(f"catalog_products_{catalog_id}", products_dict, timeout=3600)
        log.debug(
            f"✅ {len(products_dict)} productos sincronizados y guardados en caché"
        )
        return products_dict

    except requests.exceptions.RequestException as e:
        log.error(f"❌ Error sincronizando catálogo: {e}")
        if hasattr(e, "response") and e.response:
            log.error(f"Detalles: {e.response.text}")
        return {}


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


def get_user_name(phone_number):
    cache_key = f"Client_{phone_number}"
    client_data = cache.get(cache_key)
    if client_data and client_data.get("client_name"):
        return client_data["client_name"]
    else:
        return "Cliente"


def get_context(phone_number):
    cache_key = f"Client_{phone_number}"
    client_data = cache.get(cache_key)
    if client_data and client_data.get("context"):
        # Limpiar prefijo antiguo si existe en caché
        return client_data["context"].replace("CONTEXTO:", "").strip()
    else:
        return ""


def get_image_id(phone_number):
    cache_key = f"Client_{phone_number}"
    client_data = cache.get(cache_key)
    if client_data and client_data.get("image_id"):
        return client_data["image_id"]
    else:
        return None


def notify_owner(order_data, sender_id, total_price, currency):
    """
    Notifica al dueño sobre una nueva orden recibida

    Args:
        order_data: Datos de la orden
        sender_id: Número de WhatsApp del cliente
        total_price: Precio total de la orden
        currency: Moneda
    """
    catalog_id = order_data.get("catalog_id")
    product_items = order_data.get("product_items", [])
    customer_note = order_data.get("text", "")

    # Construir mensaje para el dueño
    owner_message = "🔔 *NUEVA ORDEN RECIBIDA*\n\n"
    owner_message += f"👤 *Cliente:* {get_user_name(sender_id)}\n"
    owner_message += f"📱 *Número:* {sender_id}\n"
    owner_message += f"📋 *Catalog ID:* {catalog_id}\n\n"
    owner_message += "📦 *PRODUCTOS:*\n\n"

    for idx, item in enumerate(product_items, 1):
        product_sku = item.get("product_retailer_id")
        quantity = item.get("quantity", 1)
        item_price = float(item.get("item_price", 0))
        subtotal = item_price * quantity

        # Intentar obtener nombre del producto
        product_info = get_product_info(catalog_id, product_sku)
        product_name = product_info.get("name", f"Producto {product_sku}")

        owner_message += f"{idx}. *{product_name}*\n"
        owner_message += f"   SKU: `{product_sku}`\n"
        owner_message += f"   Cantidad: {quantity}\n"
        owner_message += f"   Precio unit: {currency} {item_price:.2f}\n"
        owner_message += f"   Subtotal: {currency} {subtotal:.2f}\n\n"

    owner_message += f"{'─' * 30}\n"
    owner_message += f"💰 *TOTAL: {currency} {total_price:.2f}*\n"

    if customer_note:
        owner_message += f"\n💬 *Nota del cliente:*\n_{customer_note}_\n"

    owner_message += f"\n⏰ Hora: {get_current_time()}"

    # Enviar al número del dueño
    owner_phone = settings.OWNER_PHONE_NUMBER
    send_whatsapp_message(owner_phone, owner_message)
    log.debug(f"✅ Notificación enviada al dueño: {owner_phone}")


def get_current_time():
    """Obtiene la hora actual formateada"""
    from datetime import datetime

    # Zona horaria de Perú
    peru_tz = pytz.timezone("America/Lima")
    now = datetime.now(peru_tz)
    return now.strftime("%d/%m/%Y %I:%M %p")


def get_product_info(catalog_id, product_retailer_id):
    """
    Obtiene información de un producto desde el caché o sincroniza si es necesario
    """
    # Intentar obtener del caché
    products_dict = cache.get(f"catalog_products_{catalog_id}")

    # Si no está en caché, sincronizar
    if products_dict is None:
        log.debug(f"📥 Sincronizando productos del catálogo {catalog_id}...")
        products_dict = sync_catalog_products(catalog_id)

    # Buscar el producto
    product_info = products_dict.get(product_retailer_id)

    if product_info:
        log.debug(f"✅ Producto encontrado: {product_info['name']}")
        return product_info
    else:
        log.error(f"⚠️ Producto no encontrado: {product_retailer_id}")
        return {
            "name": f"Producto {product_retailer_id}",
            "retailer_id": product_retailer_id,
        }


def send_catalog_message(receptor_wsp_id, body_text="¡Mira nuestro catálogo! 🛍️"):
    """
    Envía el catálogo completo de WhatsApp Business
    """
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }

    data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": receptor_wsp_id,
        "type": "interactive",
        "interactive": {
            "type": "catalog_message",
            "body": {"text": body_text},
            "action": {"name": "catalog_message"},
        },
    }

    try:
        response = requests.post(
            settings.WHATSAPP_URL, headers=headers, data=json.dumps(data)
        )
        response.raise_for_status()
        log.debug(f"✅ Catálogo enviado exitosamente: {response.json()}")
        try:
            contact_obj = Contact.objects.get(phone=receptor_wsp_id)
            Message.objects.create(
                contact=contact_obj, text="*Bot envió el catálogo*", is_bot=True
            )
        except Contact.DoesNotExist:
            pass
        return response.json()
    except requests.exceptions.RequestException as e:
        log.error(f"❌ Error al enviar catálogo: {e}")
        if hasattr(e.response, "text") and e.response:
            log.error(f"Detalles del error: {e.response.text}")
        return None


def process_order(order_data, sender_id):
    """
    Procesa una orden recibida del catálogo

    Args:
        order_data: Datos de la orden desde el webhook
        sender_id: ID del cliente
    """
    try:
        # Extraer información de la orden
        catalog_id = order_data.get("catalog_id")
        product_items = order_data.get("product_items", [])
        text = order_data.get("text", "")  # Nota o comentario del cliente

        log.debug("🛒 NUEVA ORDEN RECIBIDA")
        log.debug(f"📋 Catalog ID: {catalog_id}")
        log.debug(f"👤 Cliente: {sender_id}")
        log.debug(f"💬 Nota del cliente: {text}")
        log.debug("📦 Productos:")

        total_items = 0
        total_price = 0
        order_summary = "📦 *Resumen de tu pedido:*\n\n"

        for idx, item in enumerate(product_items, 1):
            product_retailer_id = item.get("product_retailer_id")
            quantity = item.get("quantity", 1)
            item_price = float(item.get("item_price", 0))
            currency = item.get("currency", "PEN")

            # 🔍 Obtener información del producto desde la API
            log.debug(
                f"🔍 Consultando producto {idx}/{len(product_items)}: {product_retailer_id}"
            )
            product_info = get_product_info(catalog_id, product_retailer_id)
            product_name = product_info["name"]

            subtotal = item_price * quantity
            total_items += quantity
            total_price += subtotal

            log.debug(f"  ✓ Nombre: {product_name}")
            log.debug(f"  ✓ Cantidad: {quantity}")
            log.debug(f"  ✓ Precio unitario: {currency} {item_price:.2f}")
            log.debug(f"  ✓ Subtotal: {currency} {subtotal:.2f}\n")

            # Formatear el resumen para WhatsApp
            order_summary += f"{idx}. *{product_name}*\n"
            order_summary += f"   📦 Cantidad: {quantity} {'unidad' if quantity == 1 else 'unidades'}\n"
            order_summary += f"   💵 Precio unitario: {currency} {item_price:.2f}\n"
            order_summary += f"   💰 Subtotal: {currency} {subtotal:.2f}\n\n"

        order_summary += f"{'─' * 30}\n"
        order_summary += f"📊 *Total de productos:* {total_items}\n"
        order_summary += f"💰 *TOTAL A PAGAR:* {currency} {total_price:.2f}\n"

        if text:
            order_summary += f"\n💬 *Tu nota:* _{text}_"

        # Enviar confirmación al cliente
        confirmation_message = (
            f"✅ *¡Pedido recibido exitosamente!*\n\n"
            f"{order_summary}\n\n"
            f"Estamos procesando tu pedido. 📋\n"
            f"En breve nos pondremos en contacto contigo para coordinar la entrega. 🚚\n\n"
            f"_Gracias por tu compra_ 🙌✨"
        )

        send_whatsapp_message(sender_id, confirmation_message)

        log.debug("✅ Orden procesada exitosamente")
        log.debug(f"💰 Total: {currency} {total_price:.2f}")
        log.debug(f"{'=' * 50}\n")

        # Aquí puedes guardar la orden en tu base de datos
        # save_order_to_database(sender_id, order_data, product_info_list, total_price)
        notify_owner(order_data, sender_id, total_price, currency)
        # Opcional: Notificar al equipo de ventas
        # notify_sales_team(order_data, sender_id, total_price)

        return True
    except Exception as e:
        log.error(f"❌ Error procesando orden: {e}")
        import traceback

        traceback.print_exc()

        # Enviar mensaje de error al cliente
        send_whatsapp_message(
            sender_id,
            "😔 Lo sentimos, hubo un error al procesar tu pedido. "
            "Por favor, inténtalo nuevamente o contáctanos directamente.",
        )
        return False


def send_product_message(
    receptor_wsp_id, catalog_id, product_retailer_id, body_text="¡Mira este producto! 🛍️"
):
    """
    Envía un producto específico del catálogo

    Args:
        receptor_wsp_id: ID de WhatsApp del receptor
        catalog_id: ID del catálogo (obtenerlo de tu WhatsApp Business Manager)
        product_retailer_id: SKU o ID del producto en tu catálogo
        body_text: Texto del mensaje
    """
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }

    data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": receptor_wsp_id,
        "type": "interactive",
        "interactive": {
            "type": "product",
            "body": {"text": body_text},
            "action": {
                "catalog_id": catalog_id,
                "product_retailer_id": product_retailer_id,
            },
        },
    }

    try:
        response = requests.post(
            settings.WHATSAPP_URL, headers=headers, data=json.dumps(data)
        )
        response.raise_for_status()
        log.debug(f"✅ Producto enviado exitosamente: {response.json()}")
        try:
            contact_obj = Contact.objects.get(phone=receptor_wsp_id)
            Message.objects.create(
                contact=contact_obj, text="*Bot envió productos*", is_bot=True
            )
        except Contact.DoesNotExist:
            pass
        return response.json()
    except requests.exceptions.RequestException as e:
        log.error(f"❌ Error al enviar producto: {e}")
        if hasattr(e.response, "text") and e.response:
            log.error(f"Detalles del error: {e.response.text}")
        return None


def send_product_list_message(
    receptor_wsp_id,
    catalog_id,
    sections,
    header_text="Nuestros Productos",
    body_text="Elige lo que más te guste",
):
    """
    Envía una lista de productos del catálogo organizados por secciones

    Args:
        receptor_wsp_id: ID de WhatsApp del receptor
        catalog_id: ID del catálogo
        sections: Lista de secciones con productos. Ejemplo:
            [
                {
                    "title": "Ofertas",
                    "product_items": [
                        {"product_retailer_id": "SKU001"},
                        {"product_retailer_id": "SKU002"}
                    ]
                }
            ]
        header_text: Título del mensaje
        body_text: Cuerpo del mensaje
    """
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }

    data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": receptor_wsp_id,
        "type": "interactive",
        "interactive": {
            "type": "product_list",
            "header": {"type": "text", "text": header_text},
            "body": {"text": body_text},
            "action": {"catalog_id": catalog_id, "sections": sections},
        },
    }

    try:
        response = requests.post(
            settings.WHATSAPP_URL, headers=headers, data=json.dumps(data)
        )
        response.raise_for_status()
        log.debug(f"✅ Lista de productos enviada exitosamente: {response.json()}")
        try:
            contact_obj = Contact.objects.get(phone=receptor_wsp_id)
            Message.objects.create(
                contact=contact_obj,
                text="*Bot envió una lista de productos*",
                is_bot=True,
            )
        except Contact.DoesNotExist:
            pass
        return response.json()
    except requests.exceptions.RequestException as e:
        log.error(f"❌ Error al enviar lista de productos: {e}")
        if hasattr(e.response, "text") and e.response:
            log.error(f"Detalles del error: {e.response.text}")
        return None


def send_contact_message(sender_id):
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {
        "messaging_product": "whatsapp",
        "to": sender_id,
        "type": "contacts",
        "contacts": [
            {
                "name": {
                    "formatted_name": "Freddy Sanval",
                    "first_name": "Freddy",
                    "last_name": "Sanval",
                },
                "phones": [
                    {
                        "phone": f"+{settings.OWNER_PHONE_NUMBER}",
                        "type": "Mobile",
                        "wa_id": settings.OWNER_PHONE_NUMBER,
                    }
                ],
            }
        ],
    }
    try:
        response = requests.post(
            settings.WHATSAPP_URL, headers=headers, data=json.dumps(data)
        )
        response.raise_for_status()
        log.debug(f"Contacto enviado exitosamente: {response.json()}")
        try:
            contact_obj = Contact.objects.get(phone=sender_id)
            Message.objects.create(
                contact=contact_obj,
                text="*Bot envió el contacto registrado*",
                is_bot=True,
            )
        except Contact.DoesNotExist:
            pass
        return response.json()
    except requests.exceptions.RequestException as e:
        log.error(f"Error al enviar contacto de Whatsapp {e}")
        return None


def send_button_catalog_agent(sender_id, message):
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": sender_id,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": message,
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": "button1",
                            "title": "Contactar Agente",
                        },
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "button2",
                            "title": "Ver Catalogo",
                        },
                    },
                ]
            },
        },
    }

    try:
        response = requests.post(settings.WHATSAPP_URL, headers=headers, json=data)
        response.raise_for_status()
        log.debug("MENSAJE DE BOTONES ENVIADO CON ÉXITO")
        try:
            contact_obj = Contact.objects.get(phone=sender_id)
            Message.objects.create(
                contact=contact_obj,
                text="*Bot envió los botones para elegir entre contactar agente y ver catálogo*",
                is_bot=True,
            )
        except Contact.DoesNotExist:
            pass
        return response.json()
    except requests.exceptions.RequestException as e:
        log.error(f"Ocurrió un error al enviar el mensaje {e}")
        return None


def send_image(sender_id, image_id):
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": sender_id,
        "type": "image",
        "image": {
            "id": image_id,
        },
    }
    try:
        response = requests.post(
            settings.WHATSAPP_URL, headers=headers, data=json.dumps(data)
        )
        response.raise_for_status()
        log.debug(f"Imagen enviada exitosamente: {response.json()}")
        try:
            contact_obj = Contact.objects.get(phone=sender_id)
            Message.objects.create(
                contact=contact_obj, text="*Bot envió una imagen*", is_bot=True
            )
        except Contact.DoesNotExist:
            pass
        return response.json()
    except requests.exceptions.RequestException as e:
        log.error(f"Error al enviar imagen de Whatsapp {e}")
        log.error(
            f"❌ Respuesta del servidor: {e.response.text if e.response else 'Sin respuesta'}"
        )
        return None


def send_whatsapp_message(receptor_wsp_id, text_answer):
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {
        "messaging_product": "whatsapp",
        "to": receptor_wsp_id,
        "type": "text",
        "text": {
            "body": text_answer,
        },
    }
    try:
        response = requests.post(
            settings.WHATSAPP_URL, headers=headers, data=json.dumps(data)
        )
        response.raise_for_status()
        log.debug(f"Mensaje enviado exitosamente: {response.json()}")
        try:
            contact_obj = Contact.objects.get(phone=receptor_wsp_id)
            Message.objects.create(contact=contact_obj, text=text_answer, is_bot=True)
        except Contact.DoesNotExist:
            pass
        return response.json()
    except requests.exceptions.RequestException as e:
        log.error(f"Error al enviar mensaje de Whatsapp {e}")
        log.error(
            f"❌ Respuesta del servidor: {e.response.text if e.response else 'Sin respuesta'}"
        )
        return None


def get_catalog_context(catalog_id):
    """
    Obtiene los productos del catálogo y los formatea como texto para el LLM.
    Devuelve un string con la lista de productos y sus detalles clave.
    """
    try:
        # Intentar obtener del caché primero
        products_dict = cache.get(f"catalog_products_{catalog_id}")

        # Si no está en caché, sincronizar
        if products_dict is None:
            products_dict = sync_catalog_products(catalog_id)

        if not products_dict:
            return "No hay información del catálogo disponible actualmente."

        catalog_text = "📦 **CATÁLOGO DE PRODUCTOS DISPONIBLES**:\n"
        catalog_text += "Usa esta información para responder preguntas sobre productos, precios y disponibilidad.\n\n"

        # Limitar a los primeros 50 productos para no saturar el contexto si es muy grande
        # O idealmente seleccionar los más relevantes, pero por ahora listamos todos (son pocos usualmente)
        count = 0
        for retailer_id, product in products_dict.items():
            if count >= 80:  # Límite de seguridad
                catalog_text += (
                    "... (más productos disponibles en el catálogo completo)\n"
                )
                break

            name = product.get("name", "Producto")
            price = product.get("price", "Consultar")
            sale_price = product.get("sale_price")
            # [MEJORA] No recortar la descripción para permitir búsqueda semántica profunda
            description = product.get("description", "") 
            category = product.get("category", "")

            catalog_text += f"- {name} (ID: {retailer_id})\n"
            if category:
                catalog_text += f"  Categoría: {category}\n"
            if sale_price:
                catalog_text += f"  Precio Oferta: {sale_price} (Antes: {price})\n"
            else:
                catalog_text += f"  Precio: {price}\n"
            if description:
                catalog_text += f"  Detalles: {description}\n"

            count += 1

        return catalog_text
    except Exception as e:
        log.error(f"Error generando contexto del catálogo: {e}")
        return ""


def search_and_send_products(sender_id, search_term):
    """
    Filtra productos usando DIFUSIÓN (Fuzzy Matching) para máxima precisión.
    Ya no se basa en puntos simples, sino en la similitud de la frase completa.
    """
    try:
        products_dict = cache.get(f"catalog_products_{settings.CATALOG_ID}")
        if not products_dict:
            products_dict = sync_catalog_products(settings.CATALOG_ID)
            
        scored_products = []
        term_clean = search_term.lower().strip()
        tokens = term_clean.split()
        
        # 1. Filtro Candidatos: Seleccionar solo productos que tengan AL MENOS UNA coincidencia
        candidates = []
        for pid, prod in products_dict.items():
            name = prod.get("name", "").lower()
            if any(token in name for token in tokens):
                candidates.append(prod)
                
        # 2. Ranking Difuso (Fuzzy): Comparar similitud entre 'Search Term' y 'Nombre Producto'
        for prod in candidates:
            name = prod.get("name", "").lower()
            # SequenceMatcher calcula qué tanto se parecen las frases (0.0 a 1.0)
            # Esto penaliza si el producto tiene palabras que NO están en la búsqueda (ej: 'Hojas' vs 'Tribal')
            similarity = difflib.SequenceMatcher(None, term_clean, name).ratio()
            
            # Bonus por contención exacta: Si la palabra clave rara (ej: Tribal) está, subir score.
            # Pero difflib ya maneja esto bastante bien.
            
            scored_products.append((similarity, prod))
            
        if not scored_products:
            # Fallback a búsqueda laxa si no hay nada
            send_whatsapp_message(sender_id, f"Mmm, busqué '{search_term}' pero no vi nada exacto. 🤔 ¡Pero mira todo lo que tenemos! 👇")
            send_catalog_message(sender_id)
            return

        # Ordenar por similitud (Mayor a menor)
        scored_products.sort(key=lambda x: x[0], reverse=True)
        
        # Filtrar basura: Si la similitud es muy baja (< 0.2), quizás no deberíamos mandarlo como 'match exacto'.
        # Pero mejor mandamos el mejor que tengamos.
        
        matches = [p[1] for p in scored_products]
        
        # 1. Enviar el GANADOR
        top_match = matches[0]
        send_product_message(
            sender_id, 
            settings.CATALOG_ID, 
            top_match["retailer_id"],
            body_text=f"¡Lo encontré! 😍 Es el {top_match['name']}."
        )
        
        # 2. Lista de alternativas
        remaining_matches = matches[1:10]
        if remaining_matches:
            product_items = []
            for prod in remaining_matches:
                product_items.append({
                    "product_retailer_id": prod["retailer_id"]
                })
                
            sections = [
                {
                    "title": "Otras opciones",
                    "product_items": product_items
                }
            ]
            
            send_product_list_message(
                sender_id, 
                settings.CATALOG_ID, 
                sections, 
                header_text=f"Más opciones similares", 
                body_text="Aquí tienes otros modelos parecidos. 👇"
            )
            
        log.debug(f"✅ Productos enviados a {sender_id} (Top: {top_match['name']} - Score: {scored_products[0][0]:.2f})")
        
    except Exception as e:
        log.error(f"Error recomendando productos: {e}")
        send_catalog_message(sender_id) # Fallback


def get_whatsapp_media_url(media_id):
    """Obtiene la URL de descarga de un archivo multimedia de WhatsApp"""
    url = f"https://graph.facebook.com/v21.0/{media_id}"
    headers = {"Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}"}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json().get("url")
    except Exception as e:
        log.error(f"Error obteniendo URL de media: {e}")
        return None


def download_and_optimize_image(url):
    """Descarga y optimiza la imagen para Gemini"""
    headers = {"Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}"}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()

        # Procesar con PIL
        image = Image.open(io.BytesIO(response.content))

        # Redimensionar si es muy grande (ej: > 1024px)
        max_size = (1024, 1024)
        image.thumbnail(max_size)

        # Convertir a JPEG optimizado
        buffer = io.BytesIO()
        if image.mode in ("RGBA", "P"):
            image = image.convert("RGB")
        image.save(buffer, format="JPEG", quality=85)
        return buffer.getvalue()
    except Exception as e:
        log.error(f"Error descargando imagen: {e}")
        return None


def mark_whatsapp_read(message_id):
    """Marca un mensaje como leído (Blue Check)"""
    try:
        url = settings.WHATSAPP_URL
        headers = {
            "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
            "Content-Type": "application/json",
        }
        data = {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
        requests.post(url, headers=headers, json=data)
    except Exception as e:
        log.error(f"Error marcando leído: {e}")

def send_typing_indicator(recipient_id):
    """Muestra el estado 'Escribiendo...' al usuario"""
    try:
        url = settings.WHATSAPP_URL
        headers = {
            "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
            "Content-Type": "application/json",
        }
        data = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_id,
            "type": "interactive", # Bug común: Para status suele usarse un endpoint distinto o type='action' dependiendo de la versión, pero 'fulfillment' standard es así:
            # CORRECCIÓN: El endpoint correcto para status de writing es este:
        } 
        # WhatsApp status es un mensaje normal con type='text' vacío? No, es un comando especial.
        # Implementación correcta según Meta Graph API:
        # POST /v13.0/{phone-number-id}/messages
        # { "messaging_product": "whatsapp", "recipient_type": "individual", "to": "...", "type": "text", "text": {...} } NO.
        
        # Real Typing Indicator Payload:
        # { "messaging_product": "whatsapp", "to": "...", "type": "action", "action": {"name": "sending_flow_event", "parameters": {"flow_message_version": "3", "flow_token": "unused", "mode": "draft", "flow_id": "..."} } } NO.
        
        # OK, la API oficial para "typing" no está siempre disponible en todas las versiones lite.
        # Pero intentaremos no "bloquear" con esto. 
        # En la API Cloud Standard, el 'status' no es tan directo como en la On-Premise.
        # Sin embargo, lo más cercano es simplemente responder rápido.
        
        # Si no hay endpoint oficial fácil en esta versión, lo omitimos para no causar errores 400.
        pass 
    except Exception:
        pass

# NOTA: En la Cloud API, enviar el estado "typing" no está 100% documentado igual que en On-Premise.
# Investigando... Ah, sí, se puede enviar un mensaje vacío o simplemente procesar rápido.
# Para evitar bugs, vamos a centrarnos en el Audio.

def download_audio(url):
    """Descarga el audio para Gemini, retorna bytes"""
    headers = {"Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}"}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.content
    except Exception as e:
        log.error(f"Error descargando audio: {e}")
        return None

def process_gemini_message(sender_id, raw_text, timestamp, message_id, media_id=None, media_type="image"):
    try:
        log.debug(f"🧵 Procesando mensaje en background para: {sender_id} (Media: {media_id} - {media_type})")
        
        # Marcar como leído
        mark_whatsapp_read(message_id)
        
        # 1. Cancelar timer anterior al recibir mensaje nuevo
        cancel_timer(sender_id)

        try:
            contact_obj = Contact.objects.get(phone=sender_id)
        except Contact.DoesNotExist:
            log.error(f"❌ Contacto no encontrado en hilo: {sender_id}")
            return

        # Guardar mensaje del usuario con deduplicación DB
        try:
            Message.objects.create(
                contact=contact_obj,
                text=raw_text.strip(),
                is_bot=False,
                message_id=message_id,
            )
        except IntegrityError:
            # Si es una imagen (media_id), es normal que ya esté guardado por el webhook
            if media_id:
                log.debug(
                    "📸 Mensaje de imagen ya guardado en DB (continuando proceso...)"
                )
            else:
                log.warning(
                    f"🛑 Mensaje duplicado detectado en DB (IntegrityError): {message_id}"
                )
                return
        except Exception as e:
            # Si falla por integridad (duplicado), abortamos solo si no es imagen
            if (
                "UNIQUE constraint failed" in str(e)
                or "unique constraint" in str(e).lower()
            ):
                if media_id:
                    log.debug(
                        "📸 Mensaje de imagen ya guardado (race condition), continuando..."
                    )
                else:
                    log.warning(
                        f"🛑 Mensaje duplicado detectado en DB (race condition): {message_id}"
                    )
                    return
            else:
                log.error(f"⚠️ Error al guardar mensaje: {e}")
                return

        if not contact_obj.is_bot_active:
            log.debug("🤖 Bot desactivado para este usuario.")
            return

        if contact_obj.bot_disabled_at:
            message_timestamp = datetime.fromtimestamp(timestamp)
            if message_timestamp < contact_obj.bot_disabled_at:
                log.debug("⏳ Mensaje antiguo ignorado.")
                return

        text_body = raw_text.lower().strip()
        max_reintentos = 4

        gemini_contents = []

        if media_id:
            media_url = get_whatsapp_media_url(media_id)
            
            if media_type == "image":
                # Flujo de Imagen: Descargar y preparar para Gemini
                log.debug("📸 Procesando imagen para Gemini Vision...")
                if media_url:
                    image_bytes = download_and_optimize_image(media_url)
                    if image_bytes:
                        # Instrucción de análisis visual (SIEMPRE se agrega, haya texto o no)
                        analysis_instruction = (
                            "\n\n[INSTRUCCIÓN DE VISIÓN CRÍTICA]: La imagen adjunta es lo que el cliente quiere VERIFICAR. "
                            "Analiza el ESTAMPADO (Keywords: Tribal, Hojas, Floral, Liso, etc). "
                            "NO uses nombres genéricos. Busca en tu SYSTEM PROMPT (Catálogo) el producto que tenga ese estampado y color EXACTO. "
                            "EJECUTA 'recommend_products' con el 'NOMBRE DEL ESTAMPADO' identificado (ej: 'Palazzo Tribal')."
                        )
                        
                        if text_body:
                            text_body += analysis_instruction
                        else:
                            text_body = analysis_instruction.strip()
                            
            elif media_type == "audio":
                # Flujo de Audio: Oído sónico
                log.debug("🎙️ Procesando audio para Gemini...")
                if media_url:
                    audio_bytes = download_audio(media_url)
                    if audio_bytes:
                        gemini_contents.append(
                            types.Part.from_bytes(data=audio_bytes, mime_type="audio/ogg")
                        )
                        if not text_body:
                            text_body = "Escucha el audio, identifica qué busca el cliente y EJECUTA 'recommend_products' o la función necesaria. NO pidas confirmación, actúa." # Prompt implícito para audio
        
        # Agregar el texto (ya sea del usuario o el implícito)
        gemini_contents.append(text_body)

        # Obtener contexto del catálogo
        catalog_context = get_catalog_context(settings.CATALOG_ID)

        # Construir Instrucción del Sistema Dinámica
        dynamic_system_instruction = (
            f"{settings.SYSTEM_PROMPT}\n\n"
            f"--- CONOCIMIENTO DEL NEGOCIO ---\n{catalog_context}\n\n"
            f"--- HISTORIAL DE CONVERSACIÓN ---\n{get_context(sender_id)}"
        )

        for intento in range(max_reintentos):
            try:
                response = client.models.generate_content(
                    model="models/gemini-flash-lite-latest",
                    contents=gemini_contents,  # Lista con texto e imagen (si hay)
                    config={
                        "system_instruction": dynamic_system_instruction,
                        "tools": [button_tool()],
                    },
                )
                break
            except Exception as e:
                error_texto = str(e).lower()
                if "429" in error_texto:
                    log.warning("⚠️ Error de Límite de Tasa (429) detectado en hilo.")
                    if intento + 1 == max_reintentos:
                        log.error("❌ Fallo definitivo por Rate Limit (429).")
                        send_whatsapp_message(
                            sender_id,
                            "😔 Disculpa, estamos recibiendo muchas consultas. Por favor, intenta nuevamente en un momento.",
                        )
                        return

                    espera = (4 * (intento + 1)) + random.uniform(0, 2)
                    log.warning(f"⏳ Esperando {espera:.2f} segundos (Rate Limit)...")
                    time.sleep(espera)
                elif "resource_exhausted" in error_texto:
                    log.warning(
                        "⚠️ Error de Recurso Agotado (Quota Exceeded) detectado."
                    )
                    if intento + 1 == max_reintentos:
                        log.error("❌ Fallo definitivo por Quota Exceeded.")
                        send_whatsapp_message(
                            sender_id,
                            "😔 El sistema está saturado temporalmente. Intenta más tarde.",
                        )
                        return

                    espera = (10 * (intento + 1)) + random.uniform(
                        0, 5
                    )  # Espera más larga para quota
                    log.warning(f"⏳ Esperando {espera:.2f} segundos (Quota)...")
                    time.sleep(espera)
                else:
                    log.error(f"❌ Error inesperado en Gemini: {e}")
                    send_whatsapp_message(
                        sender_id,
                        "😔 Hubo un error interno. Por favor, intenta de nuevo.",
                    )
                    return

        if not response:
            log.error("❌ La respuesta de Gemini está vacía después de los reintentos.")
            return

        has_function_call = False
        if (
            response.candidates
            and response.candidates[0].content
            and response.candidates[0].content.parts
        ):
            for part in response.candidates[0].content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    has_function_call = True
                    function_name = part.function_call.name
                    log.debug(f"🔧 Function call detectado: {function_name}")

                    if function_name == "show_catalog":
                        log.debug("✅ ACCEDIENDO AL CATALOGO")
                        send_catalog_message(
                            sender_id,
                            "¡Aquí está nuestro catálogo completo! 🛍️✨ Explora todos nuestros productos.",
                        )
                        break
                    elif function_name == "recommend_products":
                        log.debug("✅ RECOMENDANDO PRODUCTOS")
                        # Obtener argumentos
                        args = part.function_call.args
                        term = args.get("search_term", "ropa")
                        search_and_send_products(sender_id, term)
                        break
                    elif function_name == "show_contact":
                        log.debug("✅ ACCEDIENDO AL CONTACTO")
                        send_whatsapp_message(
                            sender_id,
                            "¡Con gusto linda!❤️\n\nEntiendo que deseas hablar directamente con nuestro encargado.\nÉl estará encantado de ayudarte con lo que necesites, ya sea una consulta especial o asesoría personalizada.\n\n✨Gracias por confiar en nosotros. Tu estilo merece atención directa\nCon cariño,\nTu equipo de moda femenina 💃",
                        )
                        send_contact_message(sender_id)
                        notify = f"*NOTIFICACIÓN DE SOLICITUD DE AYUDA POR CLIENTE*\n- NUMERO DEL CLIENTE: {sender_id}\n- NOMBRE DEL CLIENTE: {get_user_name(sender_id)}"
                        send_whatsapp_message(settings.OWNER_PHONE_NUMBER, notify)
                        log.debug(f"MENSAJE ENVIADO EXITOSAMENTE: {notify}")

                        send_image(settings.OWNER_PHONE_NUMBER, get_image_id(sender_id))
                        log.debug("IMAGEN ENVIADA EXITOSAMENTE")

            if not has_function_call:
                log.debug("✅ TEXTO GENERADO")
                if response.text:
                    # Limpieza de respuesta: eliminar alucinación de contexto
                    final_text = response.text.replace("CONTEXTO:", "").strip()
                    send_whatsapp_message(sender_id, final_text)

                    # Actualizar memoria (Contexto)
                    try:
                        cache_key = f"Client_{sender_id}"
                        client_data = cache.get(cache_key, {})
                        current_context = client_data.get("context", "")

                        # Limitar historial para no exceder tokens (aprox últimos 10 mensajes)
                        if len(current_context) > 2000:
                            current_context = current_context[-2000:]

                        new_interaction = f"\nUsuario: {raw_text}\nBot: {response.text}"
                        updated_context = current_context + new_interaction

                        save_user_data(phone_number=sender_id, context=updated_context)
                        log.debug(f"🧠 Memoria actualizada para {sender_id}")
                    except Exception as e:
                        log.error(f"⚠️ Error actualizando memoria: {e}")

        # 2. Iniciar nuevo timer al terminar de responder (fuera del bloque de texto/función)
        start_timer(sender_id)
    except Exception as e:
        log.error(f"❌ Error fatal en hilo de procesamiento: {e}")


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
                                    wamid = message_event.get("id") # Define wamid here for all message types
                                    log.debug(
                                        f"RECUPERANDO EL NOMBRE DEL CLIENTE {get_user_name(sender_id)}"
                                    )
                                    if message_type == "order":
                                        contact_obj = Contact.objects.get(
                                            phone=sender_id
                                        )
                                        Message.objects.create(
                                            contact=contact_obj,
                                            text="*Cliente envió una orden de pedido*",
                                            is_bot=False,
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
                                        # wamid = message_event.get("id") # Already defined above

                                        # Guardar mensaje del usuario SIEMPRE
                                        try:
                                            Message.objects.create(
                                                contact=contact_obj,
                                                text="*Imagen*",
                                                is_bot=False,
                                                message_type="image",
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
                                            target=process_gemini_message,
                                            args=(
                                                sender_id,
                                                "",  # Texto vacío inicial
                                                message_event.get("timestamp"),
                                                wamid,  # message_id
                                                image_id,  # media_id
                                                "image" # media_type
                                            ),
                                        ).start()

                                        # Responder al usuario que estamos pensando
                                        # (Opcional, a veces es mejor responder directo cuando termine)
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
                                        
                                        # Guardar mensaje de audio (placeholder)
                                        try:
                                            Message.objects.create(
                                                contact=contact_obj,
                                                text="*Nota de voz*",
                                                is_bot=False,
                                                message_id=wamid,
                                                message_type="audio",
                                                media_id=audio_id,
                                            )
                                        except IntegrityError:
                                            pass

                                        log.debug("🎙️ AUDIO DETECTADO")
                                        
                                        threading.Thread(
                                            target=process_gemini_message,
                                            args=(
                                                sender_id,
                                                "", # Texto vacío
                                                message_event.get("timestamp"),
                                                wamid,
                                                audio_id,
                                                "audio"
                                            ),
                                        ).start()

                                    elif message_type == "interactive":
                                        contact_obj = Contact.objects.get(
                                            phone=sender_id
                                        )
                                        interactive = message_event.get("interactive")
                                        button_reply = interactive.get("button_reply")
                                        button_id = button_reply.get("id")

                                        # Guardar acción del usuario SIEMPRE
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
                                            break

                                    elif message_type == "audio":
                                        contact_obj = Contact.objects.get(
                                            phone=sender_id
                                        )
                                        audio = message_event.get("audio")
                                        audio_id = audio.get("id")
                                        wamid = message_event.get("id")

                                        try:
                                            Message.objects.create(
                                                contact=contact_obj,
                                                text="*Audio*",
                                                is_bot=False,
                                                message_type="audio",
                                                media_id=audio_id,
                                                message_id=wamid,
                                            )
                                            log.debug("🎤 AUDIO DETECTADO")
                                        except Exception as e:
                                            log.warning(f"Error guardando audio: {e}")

                                    elif message_type == "video":
                                        contact_obj = Contact.objects.get(
                                            phone=sender_id
                                        )
                                        video = message_event.get("video")
                                        video_id = video.get("id")
                                        caption = video.get("caption", "")
                                        wamid = message_event.get("id")

                                        try:
                                            Message.objects.create(
                                                contact=contact_obj,
                                                text="*Video*",
                                                is_bot=False,
                                                message_type="video",
                                                media_id=video_id,
                                                caption=caption,
                                                message_id=wamid,
                                            )
                                            log.debug("🎥 VIDEO DETECTADO")
                                        except Exception as e:
                                            log.warning(f"Error guardando video: {e}")

                                    elif message_type == "text":
                                        message_id = message_event.get("id")
                                        # Deduplicación ATÓMICA: cache.add retorna True si lo agregó, False si ya existía
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

                                        # Lanzamos el hilo y respondemos 200 OK inmediatamente al webhook
                                        threading.Thread(
                                            target=process_gemini_message,
                                            args=(
                                                sender_id,
                                                raw_text,
                                                timestamp,
                                                message_id,
                                            ),
                                        ).start()

                                        # No hacemos nada más aquí, el hilo se encarga
                                        continue
                        else:
                            log.debug(f"Campo recibido: {change.get('field')}")
            else:
                log.error(f" Estructura inesperada: {data}")
            return JsonResponse({"status": "ok"}, status=200)
        except json.JSONDecodeError as e:
            log.error(f"Error JSON: {e}")
            return HttpResponse("Invalid JSON", status=400)
        except Exception as e:
            log.error(f"Error procesando el POST:{e}")
            return HttpResponse("Internal Server Error", status=500)

    return HttpResponse("Not Found..", status=404)
