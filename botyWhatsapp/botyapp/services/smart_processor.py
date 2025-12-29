"""
Integración de identificación por imagen y checkout inteligente.
Este módulo se ejecuta ANTES del Flujo de Sales Intelligence para interceptar:
1. Imágenes → Identificar producto
2. Intención de compra fuerte → Activar checkout
"""

from logger import log
from botyapp.models import Contact, ProductEmbedding
from botyapp.services.sales.purchase_intent_detector import PurchaseIntentDetector
from botyapp.services.sales.order_processor import OrderProcessor
from botyapp.services.whatsapp import (
    send_whatsapp_message,
    send_product_message,
)
from django.conf import settings


class SmartMessageProcessor:
    """
    Procesador inteligente que maneja:
    - Identificación de productos por imagen
    - Detección de intención de compra
    - Activación de checkout automático
    """

    def __init__(self):
        self.order_processor = OrderProcessor()

    def process(
        self,
        contact: Contact,
        raw_text: str,
        media_id: str = None,
        media_type: str = None,
    ) -> dict:
        """
        Procesa mensaje con inteligencia profesional.

        Returns:
            {
                'handled': bool,  # True si ya manejó el mensaje completamente
                'response': str,   # Respuesta generada (si handled=True)
                'context': dict,   # Contexto para siguiente etapa
            }
        """
        # 1. ¿Es una imagen? → Identificar producto
        if media_id and media_type == "image":
            return self._handle_image(contact, raw_text, media_id)

        # 2. ¿Cliente está en checkout activo? → Continuar checkout
        active_order = self.order_processor.get_active_order(contact)
        if active_order:
            return self._handle_checkout_flow(contact, active_order, raw_text)

        # 3. ¿Detecta intención de compra fuerte? → Iniciar checkout
        context = self._get_conversation_context(contact)
        intent = PurchaseIntentDetector.detect(raw_text, context)

        if intent.should_checkout:
            return self._initiate_checkout(contact, raw_text, intent)

        # No manejado → dejar que flujo normal procese
        return {
            "handled": False,
            "context": context,
            "intent": intent,
        }

    def _handle_image(self, contact: Contact, caption: str, media_id: str) -> dict:
        """
        Maneja recepción de imagen.
        Estrategia Profesional: No adivinar inmediato. Dar control al usuario.
        Opciones: Ver Catálogo o Hablar con Humano.
        """
        try:
            from botyapp.services.whatsapp import send_button_catalog_agent

            log.info(
                f"📸 Imagen recibida de {contact.name}. Enviando menú de opciones."
            )

            # Enviar menú de decisión
            # Mensaje mejorado solicitado por cliente
            send_button_catalog_agent(
                contact.phone,
                (
                    "¡Hola! Gracias por enviarnos la foto.\n"
                    "¡Es una prenda espectacular! ✨\n\n"
                    "Queremos que tu experiencia de compra sea perfecta. "
                    "Por favor, selecciona una opción para continuar:\n\n"
                    "🛍️ *Ver Catálogo*: Compra de forma rápida y segura.\n"
                    "👤 *Contactar Agente*: Recibe asesoría personalizada de nuestro equipo."
                ),
            )

            # Opcional: Podríamos identificar el producto en background y guardarlo en caché
            # para cuando el usuario presione "Ver Catálogo", pero por ahora mantenlo simple y robusto.

            return {
                "handled": True,
                "response": "Menu de imagen enviado",
                "waiting_for_user_choice": True,
            }

        except Exception as e:
            log.error(f"Error handling image menu: {e}")
            return {"handled": False}

    def _handle_checkout_flow(self, contact: Contact, order, message: str) -> dict:
        """Maneja flujo de checkout activo"""
        try:
            stage = order.checkout_stage

            if stage == "COLLECTING_ADDRESS":
                # Cliente envió distrito
                result = self.order_processor.collect_address(order, message)
                send_whatsapp_message(contact.phone, result.message)

                log.info(f"📍 Dirección capturada para Order #{order.id}")

                return {
                    "handled": True,
                    "response": result.message,
                    "order": order,
                }

            elif stage == "PROCESSING_PAYMENT":
                # Cliente envió método de pago o comprobante
                message_lower = message.lower().strip()

                if "contra" in message_lower and "entrega" in message_lower:
                    # Contra-entrega
                    result = self.order_processor.complete_order_contra_entrega(order)
                else:
                    # Asumimos que envió comprobante
                    result = self.order_processor.process_payment(
                        order, payment_method="yape/plin", payment_proof=message
                    )

                send_whatsapp_message(contact.phone, result.message)

                log.info(f"💳 Pago procesado para Order #{order.id}")

                return {
                    "handled": True,
                    "response": result.message,
                    "order": order,
                    "order_completed": True,
                }

        except Exception as e:
            log.error(f"Error en checkout flow: {e}")
            return {"handled": False}

        return {"handled": False}

    def _initiate_checkout(self, contact: Contact, message: str, intent) -> dict:
        """Inicia checkout cuando detecta intención fuerte"""
        try:
            # Obtener producto mencionado o último visto
            product = self._extract_product_from_message(contact, message)

            if not product:
                response = "¿Cuál producto te gustaría? 🤔"
                send_whatsapp_message(contact.phone, response)
                return {
                    "handled": True,
                    "response": response,
                }

            # INICIAR CHECKOUT
            result = self.order_processor.start_checkout(contact, product, quantity=1)
            send_whatsapp_message(contact.phone, result.message)

            if result.should_send_card:
                send_product_message(
                    contact.phone, product.retailer_id, settings.CATALOG_ID
                )

            log.info(
                f"🛒 CHECKOUT INICIADO: {contact.name} - {product.product_name} (confidence: {intent.confidence})"
            )

            return {
                "handled": True,
                "response": result.message,
                "order": result.order,
                "checkout_initiated": True,
            }

        except Exception as e:
            log.error(f"Error iniciando checkout: {e}")
            return {"handled": False}

    def _get_conversation_context(self, contact: Contact) -> dict:
        """Obtiene contexto conversacional para detector de intención"""
        try:
            state = contact.conversation_state
            return {
                "current_stage": state.current_stage,
                "asked_price": "precio"
                in str(contact.messages.filter(is_bot=False).last().text).lower()
                if contact.messages.exists()
                else False,
                "asked_size": "talla"
                in str(contact.messages.filter(is_bot=False).last().text).lower()
                if contact.messages.exists()
                else False,
                "viewed_product": len(state.viewed_products) > 0,
                "objection_resolved": state.objection_count > 0,
            }
        except Exception:
            return {}

    def _extract_product_from_message(
        self, contact: Contact, message: str
    ) -> ProductEmbedding:
        """Extrae producto mencionado en mensaje o último visto"""
        try:
            # Primero: buscar último producto visto
            state = contact.conversation_state
            if state.viewed_products:
                last_product_id = state.viewed_products[-1]
                return ProductEmbedding.objects.get(retailer_id=last_product_id)
        except Exception:
            pass

        # Fallback: buscar por nombre en mensaje
        message_lower = message.lower()
        products = ProductEmbedding.objects.filter(is_available=True)

        for product in products:
            if product.product_name.lower() in message_lower:
                return product

        return None

    def _save_last_viewed_product(self, contact: Contact, product: ProductEmbedding):
        """Guarda producto visto en contexto"""
        try:
            state, _ = contact.conversation_state, None
            try:
                state = contact.conversation_state
            except Exception:
                from botyapp.models import ConversationState

                state = ConversationState.objects.create(contact=contact)

            if product.retailer_id not in state.viewed_products:
                state.viewed_products.append(product.retailer_id)
                state.save(update_fields=["viewed_products"])
        except Exception as e:
            log.warning(f"No se pudo guardar producto visto: {e}")


# Singleton
smart_processor = SmartMessageProcessor()
