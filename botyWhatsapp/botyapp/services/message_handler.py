from django.utils import timezone
from django.conf import settings
from botyapp.models import Contact, Message, ProductEmbedding
from botyapp.services.whatsapp import send_whatsapp_message, mark_whatsapp_read
from botyapp.services.intent.classifier import intent_classifier
from botyapp.services.flow_manager import FlowManager
from botyapp.services.llm.engine import LLMEngine
from logger import log
import threading


def handle_incoming_message(message_data):
    """
    MASTER ROUTER (Updated 2026 - Hybrid FSM Architecture)

    Flujo:
    1. Validaciones Técnicas (Deduplicación, estado activo).
    2. Enrutamiento por ESTADO (State-Driven):
       - Si el usuario está en un flujo (ej: Checkout), FlowManager toma el control.
       - Si es Initial, IntentClassifier decide.
    3. Fallback a LLM (Conversación libre).
    4. Global Safety Net (Captura de errores críticos).
    """

    # --- 0. SAFETY NET (GLOBAL ERROR HANDLER) ---
    try:
        # Extracción segura de datos básicos
        entry = message_data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]

        # Ignorar actualizaciones de estado (sent, delivered, read)
        if "messages" not in value:
            return

        message = value["messages"][0]
        sender_id = message["from"]  # Phone number
        message_id = message["id"]
        timestamp = message["timestamp"]

        # Soporte para diversos tipos de mensaje
        text_body = ""
        message_type = message.get("type", "text")
        media_id = None

        if message_type == "text":
            text_body = message["text"]["body"]
        elif message_type in ["image", "video", "audio", "document"]:
            # Captura caption si existe
            text_body = message.get(message_type, {}).get("caption", "")
            media_body = message.get(message_type, {})
            media_id = media_body.get("id")
        elif message_type == "interactive":
            # Botones y Listas
            interactive = message["interactive"]
            if interactive["type"] == "button_reply":
                text_body = interactive["button_reply"]["id"]  # Usamos ID como payload
            elif interactive["type"] == "list_reply":
                text_body = interactive["list_reply"]["title"]  # O description id
        elif message_type == "order":
            # Pedido de Catálogo
            order_details = message.get("order", {})
            catalog_id = order_details.get("catalog_id")
            product_items = order_details.get("product_items", [])

            # Construir recibo legible (Estilo Dashboard)
            lines = ["📋 *Pedido de Catálogo WhatsApp*", ""]
            total = 0.0

            for item in product_items:
                retailer_id = item.get("product_retailer_id")
                qty = item.get("quantity", 1)
                price = float(item.get("item_price", 0))
                currency = item.get("currency", "PEN")

                # Buscar Nombre
                product_name = "Producto"
                try:
                    p = ProductEmbedding.objects.filter(retailer_id=retailer_id).first()
                    if p:
                        product_name = p.product_name
                except Exception:
                    pass

                line_total = price * qty
                total += line_total
                # Formato: • 2x Nombre - PEN 50.00
                lines.append(f"• {qty}x {product_name} - {currency} {price:.2f}")

            lines.append("")
            lines.append(f"💰 *Total:* S/ {total:.2f}")
            lines.append("🚚 *Envío:* Procesando según ubicación 📍")

            text_body = "\n".join(lines)

        log.info(
            f"📨 Msg received from {sender_id}: {text_body[:50]}... ({message_type})"
        )

        # --- 1. GESTIÓN DE CONTACTO & DEDUPLICACIÓN ---
        # Optimization: get name from profile only if not exists
        profile_name = "Unknown"
        contacts_data = value.get("contacts")
        if contacts_data:
            profile_name = contacts_data[0].get("profile", {}).get("name", "Unknown")

        contact, created = Contact.objects.get_or_create(
            phone=sender_id, defaults={"name": profile_name}
        )

        # Marcar como leído
        threading.Thread(target=mark_whatsapp_read, args=(message_id,)).start()

        # Verificar duplicados (Idempotencia)
        if Message.objects.filter(message_id=message_id).exists():
            log.warning(f"🔁 Duplicate message {message_id} ignored.")
            return

        # Guardar Mensaje de Usuario en DB
        # TODO: Handle media download/cache in background
        Message.objects.create(
            contact=contact,
            text=text_body,
            is_bot=False,
            message_type=message_type,
            message_id=message_id,
            media_id=media_id,  # Can be null
            caption=text_body if media_id else None,
            timestamp=timezone.now(),
        )

        # Verificar si el bot está desactivado manualmente
        if not contact.is_bot_active:
            log.info(f"🔇 Bot disabled for {sender_id}. Ignoring.")
            return

        # --- 2. COMANDOS DE ADMIN (Safety Valves) ---
        if text_body.lower() == "/reset":
            # Hard Reset de emergencia
            contact.current_state = Contact.States.INITIAL
            contact.flow_context = {}
            contact.save()
            send_whatsapp_message(sender_id, "🔄 Bot reiniciado a Estado Inicial.")
            return

        if text_body.lower() == "/build":
            # Reindex trigger
            try:
                from botyapp.services.intelligence.product_image_matcher import (
                    product_matcher,
                )

                threading.Thread(target=product_matcher.reindex_all_products).start()
                send_whatsapp_message(
                    sender_id, "🔧 Indexando productos en background..."
                )
            except ImportError:
                send_whatsapp_message(sender_id, "⚠️ Módulo de IA visual no cargado.")
            return

        # --- 3. STATE MACHINE ROUTER (THE NEW BRAIN) ---
        extra_data = {}
        if message_type == "order":
            extra_data["product_items"] = message.get("order", {}).get(
                "product_items", []
            )

        # A. Si el usuario está atrapado en un estado transaccional (No-Initial),
        #    el FlowManager tiene prioridad ABSOLUTA.
        #    Excepción: Si FlowManager retorna False, significa que quiere liberar al LLM (raro).
        if contact.current_state != Contact.States.INITIAL:
            handled = FlowManager.process(
                contact, text_body, message_type, media_id, extra_data
            )
            if handled:
                return  # El flujo manejó la respuesta. Fin.

        # B. Si estamos en INITIAL, usamos Inteligencia Híbrida.

        # Intent Classifier (Fuzzy + AI)
        intent_result = intent_classifier.classify(text_body)

        # Mapeo de Intenciones a Acciones de Flujo
        # Si la intención es "Venta", forzamos la entrada al FlowManager
        if intent_result.action in ["show_catalog", "contact_support"]:
            # Dejamos que FlowManager decida cómo entrar al estado
            FlowManager.process(contact, text_body, message_type, media_id, extra_data)
            return

        # --- 4. LLM FALLBACK (CHARLA LIBRE) ---
        # Si no es un flujo estricto, dejamos que Gemini responda conversacionalmente.
        engine = LLMEngine(settings.IA_TOKEN)
        # LLMEngine argument validation: sender_id, text_body, media_id, media_type
        engine.process_message(sender_id, text_body, media_id, message_type)

    except Exception as e:
        # --- 5. GLOBAL ERROR TRAP ---
        import traceback

        error_trace = traceback.format_exc()
        log.error(f"🔥 CRITICAL BOT ERROR: {error_trace}")

        # Respuesta de emergencia al usuario (Failover gracefully)
        try:
            if "sender_id" in locals():
                send_whatsapp_message(
                    sender_id,
                    f"🚧 Tuve un pequeño error interno: {str(e)[:100]}... 🙏",
                )
        except Exception:
            pass  # Si falla el mensaje de error, no podemos hacer nada más.
