from django.shortcuts import render
import json
import requests
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from google import genai
from google.genai import types
from django.core.cache import cache
from datetime import timedelta
import pytz

IA_KEY = settings.IA_TOKEN

client = genai.Client(api_key=IA_KEY)

def button_tool():
    """Herramienta para mostrar el catálogo de productos"""
    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="show_catalog",
                description="Muestra el catálogo de productos cuando el usuario solicita ver productos, el catálogo, o quiere comprar algo",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={},
                    required=[]
                )
            )
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
        "fields": "id,name,description,price,retailer_id,image_url",
        "limit": 100
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
                        "description": product.get("description", ""),
                        "retailer_id": retailer_id,
                        "image_url": product.get("image_url", "")
                    }
            
            # Verificar si hay más páginas
            url = data.get("paging", {}).get("next")
            if url:
                params = {}  # Los parámetros ya vienen en la URL de next
        
        # Guardar en caché por 1 hora
        cache.set(f"catalog_products_{catalog_id}", products_dict, timeout=3600)
        print(f"✅ {len(products_dict)} productos sincronizados y guardados en caché")
        return products_dict
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Error sincronizando catálogo: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"Detalles: {e.response.text}")
        return {}

def save_user_name(phone_number, client_name = None):
    cache_key = f"Client_{phone_number}"
    client_data = cache.get(cache_key, {})
    if client_name:
        client_data['client_name'] = client_name
    client_data['phone_number'] = phone_number
    
    cache.set(cache_key, client_data, timeout=60*60*24*30)
    print(f"Cliente Guardado {client_data}")
    return client_data
    
def get_user_name(phone_number):
    cache_key = f"Client_{phone_number}"
    client_data = cache.get(cache_key)
    if client_data and client_data.get('client_name'):
        return client_data['client_name']
    else:
        return "Cliente"

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
    owner_message += f"📋 *Catalog ID:* {catalog_id}\n\n"
    owner_message += f"📦 *PRODUCTOS:*\n\n"
    
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
    print(f"✅ Notificación enviada al dueño: {owner_phone}")

def get_current_time():
    """Obtiene la hora actual formateada"""
    from datetime import datetime
    import pytz
    
    # Zona horaria de Perú
    peru_tz = pytz.timezone('America/Lima')
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
        print(f"📥 Sincronizando productos del catálogo {catalog_id}...")
        products_dict = sync_catalog_products(catalog_id)
    
    # Buscar el producto
    product_info = products_dict.get(product_retailer_id)
    
    if product_info:
        print(f"✅ Producto encontrado: {product_info['name']}")
        return product_info
    else:
        print(f"⚠️ Producto no encontrado: {product_retailer_id}")
        return {
            "name": f"Producto {product_retailer_id}",
            "retailer_id": product_retailer_id
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
            "body": {
                "text": body_text
            },
            "action": {
                "name": "catalog_message"
            }
        }
    }
    
    try:
        response = requests.post(settings.WHATSAPP_URL, headers=headers, data=json.dumps(data))
        response.raise_for_status()
        print(f"✅ Catálogo enviado exitosamente: {response.json()}")
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"❌ Error al enviar catálogo: {e}")
        if hasattr(e.response, 'text'):
            print(f"Detalles del error: {e.response.text}")
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
        
        print(f"🛒 NUEVA ORDEN RECIBIDA")
        print(f"📋 Catalog ID: {catalog_id}")
        print(f"👤 Cliente: {sender_id}")
        print(f"💬 Nota del cliente: {text}")
        print(f"📦 Productos:")
        
        total_items = 0
        total_price = 0
        order_summary = "📦 *Resumen de tu pedido:*\n\n"
        
        for idx, item in enumerate(product_items, 1):
            product_retailer_id = item.get("product_retailer_id")
            quantity = item.get("quantity", 1)
            item_price = float(item.get("item_price", 0))
            currency = item.get("currency", "PEN")
            
            # 🔍 Obtener información del producto desde la API
            print(f"🔍 Consultando producto {idx}/{len(product_items)}: {product_retailer_id}")
            product_info = get_product_info(catalog_id, product_retailer_id)
            product_name = product_info["name"]
            
            subtotal = item_price * quantity
            total_items += quantity
            total_price += subtotal
            
            print(f"  ✓ Nombre: {product_name}")
            print(f"  ✓ Cantidad: {quantity}")
            print(f"  ✓ Precio unitario: {currency} {item_price:.2f}")
            print(f"  ✓ Subtotal: {currency} {subtotal:.2f}\n")
            
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
        
        print(f"✅ Orden procesada exitosamente")
        print(f"💰 Total: {currency} {total_price:.2f}")
        print(f"{'='*50}\n")
        
        # Aquí puedes guardar la orden en tu base de datos
        # save_order_to_database(sender_id, order_data, product_info_list, total_price)
        notify_owner(order_data, sender_id, total_price, currency)
        # Opcional: Notificar al equipo de ventas
        # notify_sales_team(order_data, sender_id, total_price)
        
        return True
    except Exception as e:
        print(f"❌ Error procesando orden: {e}")
        import traceback
        traceback.print_exc()
        
        # Enviar mensaje de error al cliente
        send_whatsapp_message(
            sender_id, 
            "😔 Lo sentimos, hubo un error al procesar tu pedido. "
            "Por favor, inténtalo nuevamente o contáctanos directamente."
        )
        return False


def send_product_message(receptor_wsp_id, catalog_id, product_retailer_id, body_text="¡Mira este producto! 🛍️"):
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
            "body": {
                "text": body_text
            },
            "action": {
                "catalog_id": catalog_id,
                "product_retailer_id": product_retailer_id
            }
        }
    }
    
    try:
        response = requests.post(settings.WHATSAPP_URL, headers=headers, data=json.dumps(data))
        response.raise_for_status()
        print(f"✅ Producto enviado exitosamente: {response.json()}")
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"❌ Error al enviar producto: {e}")
        if hasattr(e.response, 'text'):
            print(f"Detalles del error: {e.response.text}")
        return None

def send_product_list_message(receptor_wsp_id, catalog_id, sections, header_text="Nuestros Productos", body_text="Elige lo que más te guste"):
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
            "header": {
                "type": "text",
                "text": header_text
            },
            "body": {
                "text": body_text
            },
            "action": {
                "catalog_id": catalog_id,
                "sections": sections
            }
        }
    }
    
    try:
        response = requests.post(settings.WHATSAPP_URL, headers=headers, data=json.dumps(data))
        response.raise_for_status()
        print(f"✅ Lista de productos enviada exitosamente: {response.json()}")
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"❌ Error al enviar lista de productos: {e}")
        if hasattr(e.response, 'text'):
            print(f"Detalles del error: {e.response.text}")
        return None

def send_whatsapp_message(receptor_wsp_id, text_answer):
    headers={
        "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }
    data={
        "messaging_product": "whatsapp",
        "to": receptor_wsp_id,
        "type": "text",
        "text": {
            "body" : text_answer,
        }
    }
    try:
        response = requests.post(settings.WHATSAPP_URL, headers=headers, data=json.dumps(data))
        response.raise_for_status()
        print(f"Mensaje enviado exitosamente: {response.json()}")
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error al enviar mensaje de Whatsapp {e}")
        return None
@csrf_exempt
def whatsapp_webhook(request):
    if request.method == "GET":
        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")
        
        if mode and token:
            if mode == "subscribe" and token == settings.VERIFY_TOKEN:
                print("WEBHOOK_verified")
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
                                #print(f"🗣️🗣️NOMBRE DEL CLIENTE: {client_name}")
                                #print(f"🗣️🗣️NUMERO DEL CLIENTE: {client_number}")
                                save_user_name(client_number, client_name)
                            if "messages" in value:
                                print(f"📨 Datos del mensaje: {json.dumps(value, indent=2)}")
                                for message_event in value.get("messages", []):
                                    message_type = message_event.get("type")
                                    sender_id = message_event["from"]
                                    print(f"RECUPERANDO EL NOMBRE DEL CLIENTE {get_user_name(sender_id)}")
                                    if message_type == "order":
                                        print("🛒 ORDEN DETECTADA")
                                        order_data = message_event.get("order", {})
                                    elif message_type == "text":
                                        text_body = message_event["text"]["body"].lower().strip()
                                        response = client.models.generate_content(
                                        model="models/gemini-2.0-flash-lite",
                                        contents=text_body,
                                        config={
                                            "system_instruction": settings.SYSTEM_PROMPT,
                                            "tools": [button_tool()]
                                        }
                                        )
                                        has_function_call = False
                                        if response.candidates:
                                            for part in response.candidates[0].content.parts:
                                                if hasattr(part, 'function_call') and part.function_call:
                                                    has_function_call = True
                                                    function_name = part.function_call.name
                                                    print(f"🔧 Function call detectado: {function_name}")
                                                        
                                                    if function_name == "show_catalog":
                                                        print("✅ ACCEDIENDO AL CATALOGO")
                                                        send_catalog_message(
                                                            sender_id, 
                                                            "¡Aquí está nuestro catálogo completo! 🛍️✨ Explora todos nuestros productos."
                                                        )
                                                        break
                                            
                                        # Si no hay function call, envía la respuesta de texto normal
                                        if not has_function_call:
                                            print("📝 NO ACCEDIENDO AL CATALOGO - Respuesta normal")
                                            # Obtén el texto de manera segura
                                            response_text = ""
                                            if response.candidates:
                                                for part in response.candidates[0].content.parts:
                                                    if hasattr(part, 'text'):
                                                        response_text += part.text
                                                
                                            if response_text:
                                                send_whatsapp_message(sender_id, response_text)
                                                print(f"Mensaje enviado: {response_text}")
                                            else:
                                                print("⚠️ No se pudo extraer texto de la respuesta")
                        else:
                            print(f"Campo recibido: {change.get('field')}")
            else:
                print(f" Estructura inesperada: {data}")
            return JsonResponse({"status": "ok"}, status=200)
        except json.JSONDecodeError:
            print(f"Error JSON: {e}")
            return HttpResponse("Invalid JSON", status=400)
        except Exception as e:
            print(f"Error procesando el POST:{e}")
            return HttpResponse("Internal Server Error", status=500)
        
    return HttpResponse("Not Found..", status=404)
                            
                                
                                       
