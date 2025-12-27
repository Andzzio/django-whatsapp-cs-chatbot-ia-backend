import time
import random
import io
from datetime import datetime
from PIL import Image
from google import genai
from google.genai import types
from django.conf import settings
from logger import log
from botyapp.models import Contact, Message
from django.db import IntegrityError
from .whatsapp import (
    send_whatsapp_message,
    send_catalog_message,
    send_contact_message,
    send_image,
    mark_whatsapp_read,
    download_audio,
    download_and_optimize_image,
    get_whatsapp_media_url,
)
from .users import (
    get_context,
    save_user_data,
    get_image_id,
    start_timer,
    cancel_timer,
    get_user_name,
)
from .catalog import get_catalog_context, search_and_send_products, catalog_descriptors

# Imports opcionales para Visión
try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None

try:
    import imagehash
except ImportError:
    imagehash = None

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


def process_gemini_message(
    sender_id, raw_text, timestamp, message_id, media_id=None, media_type="image"
):
    try:
        log.debug(
            f"🧵 Procesando mensaje en background para: {sender_id} (Media: {media_id} - {media_type})"
        )

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
                        # 0. BÚSQUEDA VISUAL (Híbrida: pHash + Computer Vision)
                        detected_product_id = None

                        # A) Intento con Computer Vision (ORB) - El más robusto para patrones/overlays
                        if cv2 and np and catalog_descriptors:
                            try:
                                nparr = np.frombuffer(image_bytes, np.uint8)
                                user_cv_img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
                                if user_cv_img is not None:
                                    orb = cv2.ORB_create(nfeatures=500)
                                    kp_user, des_user = orb.detectAndCompute(
                                        user_cv_img, None
                                    )

                                    if des_user is not None:
                                        bf = cv2.BFMatcher(
                                            cv2.NORM_HAMMING, crossCheck=True
                                        )
                                        best_matches_count = 0

                                        for (
                                            cat_id,
                                            cat_des,
                                        ) in catalog_descriptors.items():
                                            matches = bf.match(des_user, cat_des)
                                            # Ordenar por distancia (mejores primero)
                                            matches = sorted(
                                                matches, key=lambda x: x.distance
                                            )
                                            # Tomar los top 50 matches
                                            good_matches = [
                                                m for m in matches if m.distance < 60
                                            ]  # Umbral de calidad

                                            if len(good_matches) > best_matches_count:
                                                best_matches_count = len(good_matches)
                                                if (
                                                    best_matches_count > 20
                                                ):  # Mínimo matches para considerar válido
                                                    detected_product_id = cat_id

                                        if detected_product_id:
                                            log.info(
                                                f"👁️ CV MATCH (ORB): {detected_product_id} con {best_matches_count} coincidencias."
                                            )
                            except Exception as e:
                                log.error(f"Error en CV Match: {e}")

                        # B) Respaldo con pHash (Si CV falló o no está disponible)
                        if not detected_product_id and imagehash:
                            try:
                                user_img = Image.open(io.BytesIO(image_bytes))
                                user_hash = imagehash.phash(user_img)

                                # Nota: products_dict se usa aquí pero no se importó explícitamente.
                                # Necesitamos importarlo o obtenerlo via cache.
                                from django.core.cache import cache

                                products_dict = cache.get(
                                    f"catalog_products_{settings.CATALOG_ID}"
                                )
                                if products_dict:
                                    best_dist = 100
                                    for pid, prod in products_dict.items():
                                        if prod.get("phash"):
                                            cat_hash = imagehash.hex_to_hash(
                                                prod["phash"]
                                            )
                                            dist = user_hash - cat_hash
                                            if dist < best_dist:
                                                best_dist = dist
                                                if dist < 12:  # Umbral estricto
                                                    detected_product_id = pid
                                    if detected_product_id:
                                        log.info(
                                            f"🎯 pHash MATCH: {detected_product_id} (Dist: {best_dist})"
                                        )

                            except Exception as e:
                                log.error(f"Error en pHash: {e}")

                        # [MODO SEGURO - USUARIO SOLICITA SOLO CATÁLOGO]
                        if image_bytes:
                            # 1. Enviar mensaje amigable
                            send_whatsapp_message(
                                sender_id,
                                "¡Me encanta ese estilo! 😍 Mira, aquí tienes nuestro catálogo completo para que encuentres ese modelo y muchos más: 👇",
                            )
                            # 2. Enviar catálogo
                            send_catalog_message(sender_id)
                            # 3. Detener flujo aquí (no pasamos a Gemini)
                            return

            elif media_type == "audio":
                # Flujo de Audio: Oído sónico
                log.debug("🎙️ Procesando audio para Gemini...")
                if media_url:
                    audio_bytes = download_audio(media_url)
                    if audio_bytes:
                        gemini_contents.append(
                            types.Part.from_bytes(
                                data=audio_bytes, mime_type="audio/ogg"
                            )
                        )
                        if not text_body:
                            text_body = "Escucha el audio, identifica qué busca el cliente y EJECUTA 'recommend_products' o la función necesaria. NO pidas confirmación, actúa."

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
