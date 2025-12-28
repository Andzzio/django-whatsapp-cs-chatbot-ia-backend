import time
from datetime import datetime
# from PIL import Image (Removed)

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
    mark_whatsapp_read,
    download_audio,
    download_and_optimize_image,
    get_whatsapp_media_url,
)
from .users import (
    get_context,
    save_user_data,
    start_timer,
    cancel_timer,
)
from .catalog import get_catalog_context, search_and_send_products

# Visual Search imports removed

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

        # --- 🚀 FAST PATH: Intenciones Directas sin LLM ---
        # Interceptar solicitudes obvias de catálogo/productos para respuesta inmediata
        fast_intent_keywords = [
            "ver productos",
            "ver catalogo",
            "ver catálogo",
            "el catalogo",
            "el catálogo",
            "ver prendas",
            "ver ropa",
            "alguna prenda",
            "muestrame productos",
            "mostrar productos",
        ]

        # Si el mensaje es CORTO y contiene alguna palabra clave, enviamos catálogo directo.
        # Evitamos falsos positivos en frases largas complejas donde el contexto importa.
        if len(text_body) < 50 and any(k in text_body for k in fast_intent_keywords):
            log.info(
                f"⚡ Intención de catálogo detectada (Fast Path) en: '{text_body}'"
            )
            send_catalog_message(
                sender_id,
                "¡Claro! 🛍️ Aquí tienes nuestro catálogo completo para que veas todas las prendas:",
            )
            return

        max_reintentos = 4

        # --- CONSTRUCCIÓN DE HISTORIAL ESTRUCTURADO (Structured Chat History) ---
        # 1. Obtener historial previo (Lista de dicts)
        history = get_context(sender_id)
        if not isinstance(history, list):
            history = []

        # 2. Construir mensaje actual
        current_message_parts = []

        if media_id:
            media_url = get_whatsapp_media_url(media_id)
            if media_type == "image":
                log.debug("📸 Procesando imagen para Gemini Vision...")
                if media_url:
                    image_bytes = download_and_optimize_image(media_url)
                    if image_bytes:
                        # Incluimos imagen como parte del contenido
                        current_message_parts.append(
                            types.Part.from_bytes(
                                data=image_bytes, mime_type="image/jpeg"
                            )
                        )
                        if not text_body:
                            text_body = "Describe esta imagen y si es ropa búscala en el catálogo."
            elif media_type == "audio":
                log.debug("🎙️ Procesando audio para Gemini...")
                if media_url:
                    audio_bytes = download_audio(media_url)
                    if audio_bytes:
                        current_message_parts.append(
                            types.Part.from_bytes(
                                data=audio_bytes, mime_type="audio/ogg"
                            )
                        )
                        if not text_body:
                            text_body = "Escucha el audio y atiende al cliente."

        current_message_parts.append({"text": text_body})

        # Agregamos el mensaje actual al historial temporalmente para la llamada API
        user_turn = {"role": "user", "parts": current_message_parts}
        gemini_input_contents = history + [user_turn]

        # Validar tamaño del historial (evitar overflow de contexto/costos)
        if len(gemini_input_contents) > 20:
            gemini_input_contents = gemini_input_contents[-20:]

        # Construir Instrucción del Sistema (ESTÁTICA)
        catalog_context = get_catalog_context(settings.CATALOG_ID)
        system_instruction_text = (
            f"{settings.SYSTEM_PROMPT}\n\n"
            "--- DIRECTRICES DE AGENTE PROACTIVO (SCALABLE BEHAVIOR) ---\n"
            "1. CERO FRICCIÓN: Si el usuario muestra interés en una categoría, NO PREGUNTES si quiere verla. MUESTRA LOS PRODUCTOS INMEDIATAMENTE.\n"
            "2. INTERPRETACIÓN DE INTENCIÓN: Si el contexto implica que el usuario busca algo, asume la orden y EJECUTA la herramienta adecuada.\n"
            "3. EVITAR REDUNDANCIA: No pidas confirmación sobre confirmación.\n\n"
            f"--- CONOCIMIENTO DEL NEGOCIO ---\n{catalog_context}"
        )

        for intento in range(max_reintentos):
            try:
                response = client.models.generate_content(
                    model="models/gemini-flash-lite-latest",
                    contents=gemini_input_contents,
                    config={
                        "system_instruction": system_instruction_text,
                        "tools": [button_tool()],
                    },
                )
                break
            except Exception as e:
                error_texto = str(e).lower()
                if "429" in error_texto or "resource_exhausted" in error_texto:
                    espera = 2 * (intento + 1)
                    time.sleep(espera)
                else:
                    log.error(f"Error Gemini: {e}")
                    return

        if not response:
            return

        # Procesar Respuesta
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
                    log.debug(f"🔧 Function call: {function_name}")
                    if function_name == "show_catalog":
                        send_catalog_message(
                            sender_id, "¡Aquí tienes el catálogo completo!"
                        )
                    elif function_name == "recommend_products":
                        args = part.function_call.args
                        term = args.get("search_term", "ropa")
                        search_and_send_products(sender_id, term)
                    elif function_name == "show_contact":
                        send_whatsapp_message(
                            sender_id, "Claro, aquí tienes el contacto directo:"
                        )
                        send_contact_message(sender_id)

            if response.text and not has_function_call:
                final_text = response.text.replace("CONTEXTO:", "").strip()
                send_whatsapp_message(sender_id, final_text)

            # --- GUARDAR EN MEMORIA (Structured History) ---
            try:
                model_parts = []
                if response.text:
                    model_parts.append({"text": response.text})

                if has_function_call:
                    model_parts.append(
                        {
                            "text": f"[SISTEMA: Acción {function_name} ejecutada, NO repetir oferta]"
                        }
                    )

                model_turn = {"role": "model", "parts": model_parts}
                new_history = gemini_input_contents + [model_turn]

                save_user_data(phone_number=sender_id, context=new_history)
                log.debug(
                    f"🧠 Memoria Estructurada actualizada ({len(new_history)} items)"
                )

            except Exception as e:
                log.error(f"Error guardando memoria: {e}")

        # 2. Iniciar nuevo timer
        start_timer(sender_id)
    except Exception as e:
        log.error(f"❌ Error fatal en hilo de procesamiento: {e}")
