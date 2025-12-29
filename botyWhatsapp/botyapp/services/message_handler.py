from datetime import datetime
from logger import log
from django.db import IntegrityError
from botyapp.models import Contact, Message
from botyapp.services.whatsapp import (
    mark_whatsapp_read,
    send_catalog_message,
)
from botyapp.services.users import cancel_timer
from botyapp.services.llm.engine import llm_engine


class MessageHandler:
    """
    Orquestador de mensajes entrantes.
    Maneja el flujo: Webhook -> DB -> Validaciones -> IA / Tool.
    """

    @staticmethod
    def process_incoming(
        sender_id,
        raw_text,
        timestamp,
        message_id,
        media_id=None,
        media_type="image",
        reply_to_message_id=None,
    ):
        try:
            log.debug(f"📨 MessageHandler: Procesando mensaje de {sender_id}")

            # 1. Marcar como leído
            mark_whatsapp_read(message_id)

            # 2. Cancelar timer anterior
            cancel_timer(sender_id)

            # 3. Verificar/Obtener Contacto
            try:
                contact_obj = Contact.objects.get(phone=sender_id)
            except Contact.DoesNotExist:
                log.error(
                    f"❌ Contacto no encontrado para recibir mensaje: {sender_id}"
                )
                return

            # Lookup Reply Original Message
            reply_to_msg = None
            if reply_to_message_id:
                try:
                    reply_to_msg = Message.objects.filter(
                        message_id=reply_to_message_id
                    ).first()
                    if reply_to_msg:
                        log.debug(f"🔗 Vinculando respuesta a: {reply_to_message_id}")
                except Exception as e:
                    log.warning(f"⚠️ Error buscando mensaje original: {e}")

            # 4. Guardar Mensaje en BD (Solo Texto se guarda aquí, multimedia lo guarda views.py)
            # views.py ya guarda Image/Audio antes de llamar al hilo, pero NO guarda texto.
            # Debemos detectar si es texto puro para guardarlo.
            # Si media_id viene nulo, asumimos texto.
            if not media_id:
                try:
                    Message.objects.create(
                        contact=contact_obj,
                        text=raw_text.strip(),
                        is_bot=False,
                        message_id=message_id,
                        reply_to=reply_to_msg,
                    )
                except IntegrityError:
                    log.warning(f"🛑 Mensaje duplicado en DB: {message_id}")
                    return
                except Exception as e:
                    log.error(f"⚠️ Error guardando mensaje texto: {e}")
                    return

            # 5. Verificar Estado del Bot (Switch ON/OFF)
            if not contact_obj.is_bot_active:
                log.debug("🤖 Bot desactivado para este usuario.")
                return

            if contact_obj.bot_disabled_at:
                try:
                    ts = float(timestamp)
                    message_timestamp = datetime.fromtimestamp(ts)
                    if message_timestamp < contact_obj.bot_disabled_at:
                        log.debug("⏳ Mensaje antiguo ignorado.")
                        return
                except Exception:
                    pass

            # 6. ENTERPRISE INTENT CLASSIFICATION SYSTEM
            # "Google-grade" Strategy Pattern: Deterministic -> Probabilistic
            from botyapp.services.intent.classifier import intent_classifier
            from botyapp.services.crm_service import CRMService

            intent_result = intent_classifier.classify(raw_text)
            text_body = raw_text.lower().strip()

            # --- COMANDO DE EMERGENCIA: RESET HISTORIAL ---
            if text_body == "/reset" or text_body == "reset":
                log.info(f"🧹 Clearing history for {sender_id}")
                try:
                    # Borrar mensajes antiguos
                    Message.objects.filter(contact=contact_obj).delete()
                    # Resetear estado conversacional
                    if hasattr(contact_obj, "conversation_state"):
                        contact_obj.conversation_state.current_stage = "initial"
                        contact_obj.conversation_state.save()

                    from botyapp.services.whatsapp import send_whatsapp_message

                    send_whatsapp_message(
                        sender_id, "🧹 *Historial reseteado.* Empecemos de nuevo. 👋"
                    )
                    return
                except Exception as e:
                    log.error(f"Error resetting history: {e}")
            # ---------------------------------------------
            if text_body and text_body.strip().lower() == "/build":
                try:
                    from botyapp.services.whatsapp import send_whatsapp_message
                    from botyapp.services.intelligence.product_image_matcher import (
                        product_matcher,
                    )
                    import threading

                    send_whatsapp_message(
                        sender_id,
                        "🔧 *Iniciando re-indexación manual...* Esto tomará unos minutos.",
                    )

                    # Ejecutar en background para no bloquear
                    threading.Thread(
                        target=product_matcher.reindex_all_products
                    ).start()
                    return
                except Exception as e:
                    log.error(f"Error triggering build: {e}")
            # ---------------------------------------------

            # ---------------------------------------------
            if text_body and text_body.strip().lower() == "/build":
                try:
                    from botyapp.services.whatsapp import send_whatsapp_message
                    from botyapp.services.intelligence.product_image_matcher import (
                        product_matcher,
                    )
                    from botyapp.services.catalog import sync_facebook_to_db
                    from django.core.cache import cache
                    import threading

                    send_whatsapp_message(
                        sender_id,
                        "🔧 *Iniciando Mantenimiento Completo...* \n(3 Pasos: Sync FB -> DB -> Cache -> IA)\nEsto puede tardar.",
                    )

                    def run_full_build():
                        # 1. Sync FB -> SQL
                        sync_facebook_to_db(force=True)
                        # 2. Clear Cache
                        cache.clear()
                        # 3. Reindex Images
                        product_matcher.reindex_all_products()

                    # Ejecutar en background para no bloquear
                    threading.Thread(target=run_full_build).start()
                    return
                except Exception as e:
                    log.error(f"Error triggering build: {e}")

            # ---------------------------------------------
            if text_body and text_body.strip().lower() == "/status":
                try:
                    from botyapp.models import ProductEmbedding
                    from django.core.cache import cache
                    from django.conf import settings
                    from botyapp.services.whatsapp import send_whatsapp_message

                    db_count = ProductEmbedding.objects.count()
                    img_index_count = ProductEmbedding.objects.filter(
                        image_embedding_vector__isnull=False
                    ).count()
                    cache_status = (
                        "✅ Activo"
                        if cache.get(f"catalog_products_{settings.CATALOG_ID}")
                        else "⚠️ Vacío"
                    )

                    status_msg = (
                        f"📊 *Estado del Sistema*\n\n"
                        f"💾 *Base de Datos SQL:* {db_count} productos\n"
                        f"👁️ *Imágenes Indexadas:* {img_index_count} productos\n"
                        f"⚡ *Caché Rápida:* {cache_status}\n\n"
                        f"Si 'Imágenes Indexadas' es 0, usa /build."
                    )
                    send_whatsapp_message(sender_id, status_msg)
                    return
                except Exception as e:
                    log.error(f"Error checking status: {e}")
            # ---------------------------------------------
            # ---------------------------------------------
            # ---------------------------------------------
            # 6. INTERCEPTOR VISUAL ESCALABLE (AI-DRIVEN) 🧠
            # Usamos el IntentClassifier (que ya usas y es escalable) para detectar intención de compra.
            # Si la IA detecta que el usuario quiere ver productos, FORZAMOS la herramienta visual.
            # Esto sirve para cualquier producto nuevo que agregues, sin hardcodear palabras.

            forced_visual_intents = [
                "ordering",
                "product_inquiry",
                "show_catalog",
                "product_search",
            ]

            if intent_result.intent in forced_visual_intents:
                log.info(
                    f"🛑 Visual Intent Detected ({intent_result.intent}): {text_body}"
                )

                # Extraemos qué producto quiere ver (Si el classifier no lo da, usamos el texto completo)
                # Intentamos limpiar un poco si es una frase larga
                search_query = text_body

                # Si tenemos entities del classifier, las usamos (escalabilidad real)
                if hasattr(intent_result, "entities") and intent_result.entities:
                    # Priorizamos la entidad detected (ej: usuario dice "muestrame pantalones rojos", entidad="pantalones rojos")
                    search_query = " ".join(intent_result.entities)

                try:
                    from botyapp.services.catalog import search_and_send_products

                    search_and_send_products(sender_id, search_query)

                    CRMService.analyze_interaction(
                        sender_id, text_body, intent_label="visual_forced_ai"
                    )
                    return
                except Exception as e:
                    log.error(f"Error in Scalable Visual Interceptor: {e}")

                # ---------------------------------------------

                # ---------------------------------------------
                log.info(
                    f"✨ IntentClassifier: Catálogo activado ({intent_result.source})"
                )
                send_catalog_message(
                    sender_id, "¡Claro! 🛍️ Aquí tienes nuestro catálogo completo:"
                )
                CRMService.analyze_interaction(
                    sender_id, text_body, intent_label="show_catalog"
                )
                return

            elif intent_result.action == "contact_support":
                log.info(
                    f"✨ IntentClassifier: Soporte Humano activado ({intent_result.source})"
                )
                from botyapp.services.whatsapp import send_contact_message

                send_contact_message(sender_id)
                CRMService.analyze_interaction(
                    sender_id, text_body, intent_label="contact_support"
                )
                return

            # 7. SMART MESSAGE PROCESSOR (Image Recognition + Checkout)
            # Procesa ANTES del Sales Intelligence para interceptar:
            # - Imágenes → Identificar producto
            # - Intención de compra fuerte → Activar checkout
            from botyapp.services.smart_processor import smart_processor

            smart_result = smart_processor.process(
                contact=contact_obj,
                raw_text=raw_text,
                media_id=media_id,
                media_type=media_type,
            )

            # Si smart_processor manejó completamente el mensaje, terminar aquí
            if smart_result.get("handled"):
                log.info(
                    f"✅ Smart Processor manejó el mensaje: {smart_result.get('response', '')[:50]}"
                )

                # Análisis CRM
                CRMService.analyze_interaction(
                    sender_id, text_body, intent_label="smart_processor"
                )
                return

            # 8. SALES INTELLIGENCE SYSTEM (Autonomous Sales Bot)
            # Solo se ejecuta si smart_processor no manejó el mensaje
            log.debug("🤖 Procesando con Sales Intelligence System")

            try:
                from botyapp.services.dialogue.sales_flow import SalesFlow

                # Generar respuesta base con LLM
                llm_response = llm_engine._generate_smart_response(
                    sender_id, raw_text, media_id, media_type
                )

                # Procesar con SalesFlow para enriquecer
                sales_flow = SalesFlow(contact_obj)
                sales_result = sales_flow.process_message(raw_text, llm_response)

                # Usar respuesta enriquecida (con objeciones/cierre)
                final_response = sales_result.get("response", llm_response)

                # Enviar respuesta
                from botyapp.services.whatsapp import send_whatsapp_message

                send_whatsapp_message(sender_id, final_response)

                # Guardar respuesta del bot en BD
                Message.objects.create(
                    contact=contact_obj,
                    text=final_response,
                    is_bot=True,
                )

                # Mostrar productos si el sistema lo recomienda
                if sales_result.get("should_show_products"):
                    from botyapp.services.whatsapp import send_product_message
                    from django.conf import settings

                    for product in sales_result.get("products", [])[:5]:
                        send_product_message(
                            sender_id, product.retailer_id, settings.CATALOG_ID
                        )

                # Análisis CRM
                CRMService.analyze_interaction(
                    sender_id,
                    text_body,
                    intent_label=sales_result.get("action_taken", "sales_flow"),
                )

                log.info(
                    f"✅ Sales Intelligence: {sales_result.get('action_taken', 'processed')}"
                )

            except Exception as e:
                log.error(f"⚠️ Error en SalesFlow, fallback a LLM: {e}")
                # Fallback: usar LLM normal si falla SalesFlow
                llm_engine.process_message(
                    sender_id=sender_id,
                    text_body=raw_text,
                    media_id=media_id,
                    media_type=media_type,
                )

        except Exception as e:
            log.error(f"❌ Error CRÍTICO en MessageHandler: {e}")
