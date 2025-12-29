"""
Integración de identificación por imagen y checkout inteligente.
Este módulo se ejecuta ANTES del Flujo de Sales Intelligence para interceptar:
1. Imágenes → Identificar producto
2. Intención de compra fuerte → Activar checkout
"""

from logger import log
from botyapp.models import Contact, ProductEmbedding
from botyapp.services.intelligence.product_image_matcher import product_matcher
from botyapp.services.sales.purchase_intent_detector import PurchaseIntentDetector
from botyapp.services.sales.order_processor import OrderProcessor
from botyapp.services.whatsapp import (
    send_whatsapp_message,
    send_product_message,
    download_and_optimize_image,
    get_whatsapp_media_url,
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
        """Identifica producto en imagen"""
        try:
            # Descargar imagen
            media_url = get_whatsapp_media_url(media_id)
            if not media_url:
                return {"handled": False}

            image_bytes = download_and_optimize_image(media_url)
            if not image_bytes:
                return {"handled": False}

            # Identificar producto
            log.info(f"🔍 Identificando producto en imagen para {contact.name}")
            matches = product_matcher.identify_product(image_bytes)

            if matches and matches[0].is_certain:
                # Match con >85% confianza → Es este producto
                product = matches[0].product
                confidence = matches[0].confidence

                response = (
                    f"✅ **Identificado:** {product.product_name}\n\n"
                    f"💰 Precio: S/{product.price}\n"
                    f"📏 Tallas disponibles: S, M, L, XL\n\n"
                    f"¿Te gustaría comprarlo?"
                )

                send_whatsapp_message(contact.phone, response)
                send_product_message(
                    contact.phone, product.retailer_id, settings.CATALOG_ID
                )

                # Guardar en contexto para posible checkout
                self._save_last_viewed_product(contact, product)

                log.info(
                    f"✅ Producto identificado con {confidence:.0%} confianza: {product.product_name}"
                )

                return {
                    "handled": True,
                    "response": response,
                    "product_identified": product,
                }

            elif matches:
                # Match 70-85% → Probablemente uno de estos
                response = (
                    "📸 Vi tu imagen. Podría ser uno de estos productos:\n"
                    "(Dime el número del que buscas)"
                )

                send_whatsapp_message(contact.phone, response)

                # Enviar top 3 productos
                for match in matches[:3]:
                    send_product_message(
                        contact.phone, match.product.retailer_id, settings.CATALOG_ID
                    )

                return {
                    "handled": True,
                    "response": response,
                    "candidates": matches,
                }

            else:
                # No match >70%
                response = (
                    "📸 Vi tu imagen pero no logré identificar el producto específico.\n\n"
                    "¿Puedes decirme qué estás buscando?\n"
                    "Ejemplo: 'palazzo tribal' o 'vestido flores'"
                )

                send_whatsapp_message(contact.phone, response)

                return {
                    "handled": True,
                    "response": response,
                }

        except Exception as e:
            log.error(f"Error en identificación de imagen: {e}")
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
