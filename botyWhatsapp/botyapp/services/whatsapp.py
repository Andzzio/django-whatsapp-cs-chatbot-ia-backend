import json
import requests
import io
from PIL import Image
from django.conf import settings
from logger import log
from botyapp.models import Contact, Message
import copy


def send_whatsapp_message(
    receptor_wsp_id, text_answer, reply_to_message_id=None, create_db_record=True
):
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {
        "messaging_product": "whatsapp",
        "to": str(receptor_wsp_id).replace("+", "").replace(" ", "").strip(),
        "type": "text",
        "text": {
            "body": text_answer,
        },
    }

    if reply_to_message_id:
        data["context"] = {"message_id": reply_to_message_id}

    try:
        response = requests.post(
            settings.WHATSAPP_URL, headers=headers, data=json.dumps(data), timeout=10
        )
        response.raise_for_status()
        res_json = response.json()
        log.debug(f"Mensaje enviado exitosamente: {res_json}")

        if create_db_record:
            try:
                contact_obj = Contact.objects.get(phone=receptor_wsp_id)
                wamid = (
                    res_json["messages"][0]["id"] if "messages" in res_json else None
                )

                # Buscamos el objeto mensaje padre si existe
                reply_to_obj = None
                if reply_to_message_id:
                    reply_to_obj = Message.objects.filter(
                        message_id=reply_to_message_id
                    ).first()

                Message.objects.create(
                    contact=contact_obj,
                    text=text_answer,
                    is_bot=True,
                    message_id=wamid,
                    reply_to=reply_to_obj,
                )
            except Exception as e:
                log.warning(f"⚠️ Error guardando mensaje en BD: {e}")
        return res_json
        return res_json
    except requests.exceptions.RequestException as e:
        log.error(f"Error al enviar mensaje de Whatsapp {e}")
        log.error(
            f"❌ Respuesta del servidor: {e.response.text if e.response else 'Sin respuesta'}"
        )
        return None


def mark_whatsapp_read(message_id):
    """Marca un mensaje como leído (Blue Check)"""
    try:
        url = settings.WHATSAPP_URL
        headers = {
            "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
            "Content-Type": "application/json",
        }
        data = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        requests.post(url, headers=headers, json=data)
    except Exception as e:
        log.error(f"Error marcando leído: {e}")


def send_typing_indicator(recipient_id):
    """Muestra el estado 'Escribiendo...' al usuario (Placeholder)"""
    # En la API Cloud Standard, el indicador de 'typing' no es tan simple de invocar como en On-Premise.
    pass


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
            settings.WHATSAPP_URL, headers=headers, data=json.dumps(data), timeout=10
        )
        response.raise_for_status()
        res_json = response.json()
        log.debug(f"✅ Catálogo enviado exitosamente: {res_json}")
        try:
            contact_obj = Contact.objects.get(phone=receptor_wsp_id)
            wamid = res_json["messages"][0]["id"] if "messages" in res_json else None
            Message.objects.create(
                contact=contact_obj,
                text="*Bot envió el catálogo*",
                is_bot=True,
                message_type="catalog_message",
                message_id=wamid,
            )
        except Exception as e:
            log.warning(f"⚠️ Error guardando envío de catálogo en BD: {e}")
        return res_json
    except requests.exceptions.RequestException as e:
        log.error(f"❌ Error al enviar catálogo: {e}")
        if hasattr(e.response, "text") and e.response:
            log.error(f"Detalles del error: {e.response.text}")
        return None


def send_product_message(
    receptor_wsp_id,
    catalog_id,
    product_retailer_id,
    body_text="¡Mira este producto! 🛍️",
    product_data=None,
):
    """
    Envía un producto específico del catálogo
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
            settings.WHATSAPP_URL, headers=headers, data=json.dumps(data), timeout=10
        )
        response.raise_for_status()
        res_json = response.json()
        log.debug(f"✅ Producto enviado exitosamente: {res_json}")
        try:
            contact_obj = Contact.objects.get(phone=receptor_wsp_id)
            wamid = res_json["messages"][0]["id"] if "messages" in res_json else None

            # Preparar datos para UI
            text_content = "*Bot envió productos*"
            caption_data = {}

            if product_data:
                # Usar el nombre del producto como texto principal (limpio)
                text_content = product_data.get("name", text_content)
                caption_data["image_url"] = product_data.get("image_url")
                caption_data["price"] = product_data.get("price")
                caption_data["currency"] = product_data.get("currency")

            Message.objects.create(
                contact=contact_obj,
                text=text_content,
                is_bot=True,
                message_type="product",
                caption=json.dumps(caption_data) if caption_data else None,
                message_id=wamid,
            )
        except Exception as e:
            log.warning(f"⚠️ Error guardando producto en BD: {e}")
        return res_json
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
    sections: Lista de dicts [{'title': '...', 'product_items': [{'product_retailer_id': '...'}]}]
    """
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }

    # Crear copia limpia para la API de WhatsApp (solo permite product_retailer_id)
    api_sections = copy.deepcopy(sections)
    for section in api_sections:
        if "product_items" in section:
            for item in section["product_items"]:
                # Mantener solo product_retailer_id, eliminar metadatos extra
                keys_to_keep = ["product_retailer_id"]
                item_keys = list(item.keys())
                for key in item_keys:
                    if key not in keys_to_keep:
                        del item[key]

    data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": receptor_wsp_id,
        "type": "interactive",
        "interactive": {
            "type": "product_list",
            "header": {"type": "text", "text": header_text},
            "body": {"text": body_text},
            "action": {"catalog_id": catalog_id, "sections": api_sections},
        },
    }

    try:
        response = requests.post(
            settings.WHATSAPP_URL, headers=headers, data=json.dumps(data), timeout=10
        )
        response.raise_for_status()
        res_json = response.json()
        log.debug(f"✅ Lista de productos enviada exitosamente: {res_json}")
        try:
            contact_obj = Contact.objects.get(phone=receptor_wsp_id)
            wamid = res_json["messages"][0]["id"] if "messages" in res_json else None
            Message.objects.create(
                contact=contact_obj,
                text="*Bot envió una lista de productos*",
                is_bot=True,
                message_type="product_list",
                caption=json.dumps(sections),
                message_id=wamid,
            )
        except Exception as e:
            log.warning(f"⚠️ Error guardando lista de productos en BD: {e}")
        return res_json
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
            settings.WHATSAPP_URL, headers=headers, data=json.dumps(data), timeout=10
        )
        response.raise_for_status()
        res_json = response.json()
        log.debug(f"Contacto enviado exitosamente: {res_json}")
        try:
            contact_obj = Contact.objects.get(phone=sender_id)
            wamid = res_json["messages"][0]["id"] if "messages" in res_json else None
            Message.objects.create(
                contact=contact_obj,
                text="*Bot envió el contacto registrado*",
                is_bot=True,
                message_type="contact_message",
                message_id=wamid,
            )
        except Contact.DoesNotExist:
            pass
        return res_json
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
        response = requests.post(
            settings.WHATSAPP_URL, headers=headers, json=data, timeout=10
        )
        response.raise_for_status()
        res_json = response.json()
        log.debug(f"MENSAJE DE BOTONES ENVIADO CON ÉXITO: {res_json}")
        try:
            contact_obj = Contact.objects.get(phone=sender_id)
            wamid = res_json["messages"][0]["id"] if "messages" in res_json else None
            Message.objects.create(
                contact=contact_obj,
                text=message,
                is_bot=True,
                message_type="button_message",
                message_id=wamid,
            )
        except Contact.DoesNotExist:
            pass
        return res_json
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
            settings.WHATSAPP_URL, headers=headers, data=json.dumps(data), timeout=10
        )
        response.raise_for_status()
        res_json = response.json()
        log.debug(f"Imagen enviada exitosamente: {res_json}")
        try:
            contact_obj = Contact.objects.get(phone=sender_id)
            wamid = res_json["messages"][0]["id"] if "messages" in res_json else None
            Message.objects.create(
                contact=contact_obj,
                text="*Bot envió una imagen*",
                is_bot=True,
                message_type="image_message",
                message_id=wamid,
            )
        except Contact.DoesNotExist:
            pass
        return res_json
    except requests.exceptions.RequestException as e:
        log.error(f"Error al enviar imagen de Whatsapp {e}")
        log.error(
            f"❌ Respuesta del servidor: {e.response.text if e.response else 'Sin respuesta'}"
        )
        return None


def get_whatsapp_media_url(media_id):
    """Obtiene la URL de descarga de un archivo multimedia de WhatsApp"""
    url = f"https://graph.facebook.com/v21.0/{media_id}"
    headers = {"Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json().get("url")
    except Exception as e:
        log.error(f"Error obteniendo URL de media: {e}")
        return None


def download_audio(url):
    """Descarga el audio para Gemini, retorna bytes"""
    headers = {"Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.content
    except Exception as e:
        log.error(f"Error descargando audio: {e}")
        return None


def download_and_optimize_image(url):
    """Descarga y optimiza la imagen para Gemini"""
    headers = {"Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}"}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        # Procesar con PIL
        image = Image.open(io.BytesIO(response.content))

        # Auto-rotar según EXIF orientation (fix para imágenes rotadas)
        from PIL import ImageOps

        image = ImageOps.exif_transpose(image)

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


def send_interactive_buttons(sender_id, body_text, buttons):
    """
    Envía mensajes con botones interactivos (Reply Buttons).
    buttons: Lista de tuplas/dicts [(id1, title1), (id2, title2)] max 3.
    """
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }

    button_objects = []
    for btn_id, btn_title in buttons:
        button_objects.append(
            {"type": "reply", "reply": {"id": btn_id, "title": btn_title}}
        )

    data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": sender_id,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text},
            "action": {"buttons": button_objects},
        },
    }

    try:
        response = requests.post(
            settings.WHATSAPP_URL, headers=headers, data=json.dumps(data), timeout=10
        )
        response.raise_for_status()
        res_json = response.json()
        log.debug(f"🔘 Botones enviados: {res_json}")
        try:
            contact_obj = Contact.objects.get(phone=sender_id)
            wamid = res_json["messages"][0]["id"] if "messages" in res_json else None
            # Store as text for simplicity
            Message.objects.create(
                contact=contact_obj,
                text=body_text,
                is_bot=True,
                message_type="interactive",
                message_id=wamid,
            )
        except Exception:
            pass
        return res_json
    except requests.exceptions.RequestException as e:
        log.error(f"❌ Error al enviar botones: {e}")
        return None
