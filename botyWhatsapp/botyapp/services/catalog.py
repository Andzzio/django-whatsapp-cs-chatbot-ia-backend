import requests
import difflib
import io
from PIL import Image
from django.conf import settings
from django.core.cache import cache
from logger import log
from .whatsapp import (
    send_product_message,
    send_catalog_message,
    send_product_list_message,
)

# Bloques try/except para librerías opcionales de visión
try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None
    log.warning(
        "⚠️ OpenCV/Numpy no instalado. Búsqueda visual por características desactivada."
    )

try:
    import imagehash
except ImportError:
    imagehash = None
    log.warning(
        "⚠️ ImageHash no instalado. La búsqueda visual exacta no funcionará hasta instalarlo."
    )

# Diccionario global para features visuales (ORB)
catalog_descriptors = {}


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
                        "phash": None,  # Placeholder
                    }

                    # 📸 Generar Huella Digital Visual (Hashing + CV)
                    if product.get("image_url"):
                        try:
                            # Descarga rápida
                            img_resp = requests.get(product.get("image_url"), timeout=5)
                            if img_resp.status_code == 200:
                                image_data = img_resp.content

                                # 1. pHash (Respaldo)
                                if imagehash:
                                    img_pil = Image.open(io.BytesIO(image_data))
                                    p_hash = str(imagehash.phash(img_pil))
                                    products_dict[retailer_id]["phash"] = p_hash

                                # 2. Computer Vision (ORB Features) - "Manera Profesional"
                                if cv2 and np:
                                    # Convertir bytes a array numpy para OpenCV
                                    nparr = np.frombuffer(image_data, np.uint8)
                                    img_cv = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)

                                    if img_cv is not None:
                                        orb = cv2.ORB_create(nfeatures=500)
                                        kp, des = orb.detectAndCompute(img_cv, None)
                                        if des is not None:
                                            catalog_descriptors[retailer_id] = des

                        except Exception as e:
                            log.warning(
                                f"No se pudo procesar visión para {retailer_id}: {e}"
                            )

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

        # Limitar a los primeros 80 productos para no saturar el contexto si es muy grande
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
    Filtra productos y decide la mejor UI:
    - Varios resultados -> LISTA Interactiva (Native List Message)
    - Un resultado -> Tarjeta de Producto (Single Product Card)
    - Ninguno -> Botón de Catálogo
    """
    try:
        products_dict = cache.get(f"catalog_products_{settings.CATALOG_ID}")
        if not products_dict:
            products_dict = sync_catalog_products(settings.CATALOG_ID)

        # 0. Búsqueda Determinista por ID (Vía de Alta Velocidad)
        term_clean = search_term.strip()
        if term_clean in products_dict:
            log.debug(f"🎯 Match Exacto por ID detectado: {term_clean}")
            top_match = products_dict[term_clean]
            send_product_message(
                sender_id,
                settings.CATALOG_ID,
                top_match["retailer_id"],
                body_text="Aquí tienes.",
            )
            return

        scored_products = []
        term_clean = search_term.lower().strip()
        tokens = term_clean.split()

        # 1. Filtro Candidatos (Intento estricto)
        candidates = []
        for pid, prod in products_dict.items():
            name = prod.get("name", "").lower()
            if any(token in name for token in tokens):
                candidates.append(prod)

        # Si el filtro estricto falló, usamos TODOS para intentar fuzzy
        if not candidates:
            candidates = list(products_dict.values())

        # 2. Ranking Difuso (Fuzzy)
        for prod in candidates:
            name = prod.get("name", "").lower()
            similarity = difflib.SequenceMatcher(None, term_clean, name).ratio()

            # Umbral de similitud
            if similarity > 0.3:
                scored_products.append((similarity, prod))

        if not scored_products:
            # Fallback direct a Catálogo (Zero Text)
            send_catalog_message(sender_id, "No encontré exactos, pero mira todo:")
            return

        # Ordenar por similitud (Mayor a menor)
        scored_products.sort(key=lambda x: x[0], reverse=True)
        matches = [p[1] for p in scored_products]

        # --- LÓGICA DE UI (LIST vs SINGLE) ---

        # CASO A: Varios Resultados (LISTA)
        # WhatsApp permite hasta 10 items por sección. Mostramos el Top 10.
        if len(matches) > 1:
            top_matches = matches[:10]
            product_items = []
            for prod in top_matches:
                product_items.append(
                    {
                        "product_retailer_id": prod["retailer_id"],
                        "name": prod.get("name"),
                        "image_url": prod.get("image_url"),
                        "price": prod.get("price"),
                        "currency": prod.get("currency"),
                    }
                )

            sections = [{"title": "Resultados", "product_items": product_items}]

            # Enviamos LISTA NATIVA
            send_product_list_message(
                sender_id,
                settings.CATALOG_ID,
                sections,
                header_text=f"Resultados: {search_term}",
                body_text="Selecciona para ver detalles 👇",
            )
            log.debug(f"✅ Lista enviada con {len(top_matches)} productos.")
            return

        # CASO B: Un solo Resultado (SINGLE CARD)
        if len(matches) == 1:
            top_match = matches[0]
            send_product_message(
                sender_id,
                settings.CATALOG_ID,
                top_match["retailer_id"],
                body_text="Aquí tienes.",
            )
            log.debug(f"✅ Producto único enviado: {top_match['name']}")
            return

    except Exception as e:
        log.error(f"Error recomendando productos: {e}")
        send_catalog_message(sender_id)  # Fallback final
