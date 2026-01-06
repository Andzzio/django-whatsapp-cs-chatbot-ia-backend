import requests
import difflib
from django.conf import settings
from django.core.cache import cache
from logger import log
from .whatsapp import (
    send_product_message,
    send_catalog_message,
    send_product_list_message,
)
from botyapp.models import ProductEmbedding
from django.utils import timezone

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
                    # Parseo de Precio: Meta envía "100.00 PEN" o "50 USD"
                    raw_price = product.get("price", "0")
                    raw_sale_price = product.get("sale_price")

                    def parse_price(p_str):
                        if not p_str:
                            return 0.0
                        import re

                        # Mantener solo dígitos, puntos y comas
                        clean = re.sub(r"[^\d.,]", "", str(p_str))
                        if not clean:
                            return 0.0

                        # Lógica para detectar separador decimal
                        if "," in clean and "." in clean:
                            # Caso mixto: 1,200.50 o 1.200,50
                            if clean.rfind(",") > clean.rfind("."):
                                # Coma al final (1.200,50) -> Decimal es coma
                                clean = clean.replace(".", "").replace(",", ".")
                            else:
                                # Punto al final (1,200.50) -> Decimal es punto, quitar comas
                                clean = clean.replace(",", "")
                        elif "," in clean:
                            # Solo comas: 49,99 -> 49.99
                            clean = clean.replace(",", ".")

                        try:
                            return float(clean)
                        except ValueError:
                            return 0.0

                    price_val = parse_price(raw_price)
                    if raw_sale_price:
                        sale_val = parse_price(raw_sale_price)
                        # Si hay sale price válido y menor, úsalo
                        if sale_val > 0:
                            price_val = sale_val

                    # Obtener información de stock desde ProductEmbedding (si existe)
                    stock_s = 0
                    stock_m = 0
                    stock_l = 0
                    stock_xl = 0
                    is_available = True
                    try:
                        product_embedding = ProductEmbedding.objects.get(
                            retailer_id=retailer_id
                        )
                        stock_s = product_embedding.stock_s
                        stock_m = product_embedding.stock_m
                        stock_l = product_embedding.stock_l
                        stock_xl = product_embedding.stock_xl
                        is_available = product_embedding.is_available
                    except ProductEmbedding.DoesNotExist:
                        pass  # Usar valores por defecto

                    products_dict[retailer_id] = {
                        "name": product.get("name", "Sin nombre"),
                        "price": price_val,  # Precio numérico limpio
                        "display_price": raw_sale_price
                        if raw_sale_price
                        else raw_price,  # Para UI original
                        "description": product.get("description", ""),
                        "retailer_id": retailer_id,
                        "image_url": product.get("image_url", ""),
                        "phash": None,
                        "stock_s": stock_s,  # ✅ Stock por talla
                        "stock_m": stock_m,
                        "stock_l": stock_l,
                        "stock_xl": stock_xl,
                        "is_available": is_available,
                    }

                    # ⚠️ OPTIMIZACIÓN: Desactivamos procesamiento de imagen en tiempo real
                    # El cálculo de ORB/Hash tarda 1-2s por producto. Con 50 productos = 1.5 minutos de espera.
                    # Esto debe moverse a una Tarea en Background (Celery/Cron)
                    """
                    if product.get("image_url"):
                        try:
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
                    """

                    # Actualizar DB local (ProductEmbedding) para búsquedas rápidas
                    try:
                        ProductEmbedding.objects.update_or_create(
                            retailer_id=retailer_id,
                            defaults={
                                "product_name": product.get("name", "Sin nombre"),
                                "price": price_val,
                                "image_url": product.get("image_url", "") or "",
                                "search_text": f"{product.get('name', '')} {product.get('description', '')}".lower(),
                            },
                        )
                    except Exception as e:
                        log.warning(
                            f"⚠️ Error syncing ProductEmbedding SQL (no crítico): {e}"
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


def sync_facebook_to_db(force=False):
    """
    Sincroniza el Catálogo de Facebook con la tabla SQL ProductEmbedding.
    Vital para que la búsqueda por imagen funcione.
    """
    log.info("📥 Iniciando Sync Facebook -> SQL DB...")
    products_dict = sync_catalog_products(settings.CATALOG_ID)

    if not products_dict:
        log.error("❌ No se obtuvieron productos de Facebook para sincronizar DB")
        return False

    count = 0
    for retailer_id, product_data in products_dict.items():
        try:
            # Verificar si ya existe el embedding textual (Opcional: saltar si ya tiene)
            # Pero para imagen necesitamos asegurar que el registro exista

            name = product_data.get("name", "")
            description = product_data.get("description", "")
            category = product_data.get("category", "")
            image_url = product_data.get("image_url", "")

            # Parsear precio
            price_value = 0.0
            price_raw = product_data.get("price")
            if isinstance(price_raw, dict):
                price_value = float(price_raw.get("amount", 0))
            elif isinstance(price_raw, str):
                try:
                    price_clean = price_raw.replace("S/", "").replace(",", ".").strip()
                    price_value = float(price_clean)
                except Exception:
                    pass
            elif isinstance(price_raw, (int, float)):
                price_value = float(price_raw)

            # Crear/Actualizar Objeto
            ProductEmbedding.objects.update_or_create(
                retailer_id=retailer_id,
                defaults={
                    "product_name": name,
                    "description": description,
                    "price": price_value,
                    "category": category,
                    "image_url": image_url,
                    "is_available": True,
                    "last_synced": timezone.now(),
                    # search_text y embedding_vector se pueden generar aqui o en paso posterior
                    # Por simplicidad para imagen, lo vital es el objeto y la URL
                },
            )
            count += 1
        except Exception as e:
            log.error(f"Error syncing product {retailer_id}: {e}")

    log.info(f"✅ DB Sync Completado: {count} productos actualizados en tabla SQL.")
    return True


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
            return True

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
            # Si no hay matches, pero el término es muy genérico (ej: "pantalones"),
            # intentamos buscar por CATEGORIA en lugar de nombre.
            for pid, prod in products_dict.items():
                cat = prod.get("category", "").lower()
                if term_clean in cat or cat in term_clean:
                    scored_products.append((0.9, prod))

        # Si aun asi no hay, fallback
        if not scored_products:
            # Fallback: Mostrar catálogo general
            send_catalog_message(
                sender_id,
                "No encontré ese nombre exacto, ¡pero mira nuestra colección!",
            )
            return False

        # Ordenar por similitud (Mayor a menor)
        # Eliminamos duplicados por retailer_id
        seen_ids = set()
        unique_matches = []
        scored_products.sort(key=lambda x: x[0], reverse=True)

        for score, prod in scored_products:
            if prod["retailer_id"] not in seen_ids:
                unique_matches.append(prod)
                seen_ids.add(prod["retailer_id"])

        matches = unique_matches

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
            return True

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
            return True

    except Exception as e:
        log.error(f"Error recomendando productos: {e}")
        send_catalog_message(sender_id)  # Fallback final
        return False
