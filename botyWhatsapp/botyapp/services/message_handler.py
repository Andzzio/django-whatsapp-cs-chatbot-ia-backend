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
        sender_id, raw_text, timestamp, message_id, media_id=None, media_type="image"
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

            text_body = raw_text.lower().strip()

            # 6. 🚀 FAST PATH: Intenciones Directas (Sin gastar tokens LLM)
            fast_intent_keywords = [
                "ver productos",
                "ver catalogo",
                "ver catálogo",
                "el catalogo",
                "el catálogo",
                "ver prendas",
                "ver ropa",
                "muestrame productos",
            ]

            if len(text_body) < 50 and any(
                k in text_body for k in fast_intent_keywords
            ):
                log.info(f"⚡ Fast Path: Catálogo solicitado por {sender_id}")
                send_catalog_message(
                    sender_id, "¡Claro! 🛍️ Aquí tienes nuestro catálogo completo:"
                )
                from botyapp.services.crm_service import CRMService

                CRMService.analyze_interaction(
                    sender_id, text_body, tool_used="show_catalog"
                )
                return

            # 7. Delegar al Cerebro (LLM Engine)
            # El engine maneja historial, timers, llamadas a tools y sus respuestas.
            llm_engine.process_message(
                sender_id=sender_id,
                text_body=raw_text,
                media_id=media_id,
                media_type=media_type,
            )

        except Exception as e:
            log.error(f"❌ Error CRÍTICO en MessageHandler: {e}")
