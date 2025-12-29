"""
Sistema de manejo de objeciones con escalado progresivo.
Vende por VALOR primero, descuento solo como última carta.
"""

from typing import Dict, Optional
from botyapp.models import Contact, ProductEmbedding
from botyapp.services.dialogue.state_manager import StateManager


class ObjectionHandler:
    """Maneja objeciones sin regalar dinero"""

    MAX_DISCOUNT = 3.0  # soles (política del negocio)

    def __init__(self, contact: Contact):
        self.contact = contact
        self.state_manager = StateManager(contact)

    def handle_price_objection(
        self, product: Optional[ProductEmbedding] = None
    ) -> Dict[str, any]:
        """
        Maneja objeción de precio con escalado progresivo.

        Estrategia:
        1. Valor percibido (calidad, testimonios)
        2. Urgencia/escasez
        3. Descuento S/1
        4. Descuento S/2
        5. Descuento S/3 (MÁXIMO)

        Returns:
            Dict con 'message' y 'discount_offered'
        """
        attempt = self.state_manager.state.objection_count

        # Incrementar contador
        self.state_manager.increment_objection_count()
        self.state_manager.state.last_objection_type = "price"
        self.state_manager.state.save()

        if attempt == 0:
            # Primer intento: Valor percibido
            return self._emphasize_quality(product)
        elif attempt == 1:
            # Segundo intento: Urgencia
            return self._create_urgency(product)
        elif attempt == 2:
            # Tercer intento: Descuento S/1
            return self._offer_discount(1.0, product)
        elif attempt == 3:
            # Cuarto intento: Descuento S/2
            return self._offer_discount(2.0, product)
        else:
            # Último intento: Descuento S/3
            return self._offer_discount(3.0, product)

    def _emphasize_quality(self, product: Optional[ProductEmbedding]) -> Dict:
        """Técnica #1: Social Proof + Calidad"""
        message = "Entiendo 😊 "

        if product:
            message += (
                f"El *{product.product_name}* es de nuestros bestsellers:\n"
                "✨ Tela de alta durabilidad\n"
                "⭐ Excelente calificación de clientes\n"
                "🔄 Garantía de cambio si no te queda perfecta\n\n"
            )
        else:
            message += (
                "Nuestros productos son de alta calidad:\n"
                "✨ Telas importadas duraderas\n"
                "⭐ Clientes satisfechos\n"
                "🔄 Garantía de cambio\n\n"
            )

        message += "¿Qué talla usas normalmente?"

        return {
            "message": message,
            "technique": "social_proof",
            "discount_offered": 0.0,
        }

    def _create_urgency(self, product: Optional[ProductEmbedding]) -> Dict:
        """Técnica #2: Escasez Real"""
        stock = product.stock_quantity if product else 5

        message = (
            f"Si te sirve dato, quedan solo *{stock} unidades* 🔥\n"
            "Este modelo se agota rápido en temporada.\n\n"
            "¿Te lo reservo mientras piensas?"
        )

        return {
            "message": message,
            "technique": "scarcity",
            "discount_offered": 0.0,
        }

    def _offer_discount(
        self, amount: float, product: Optional[ProductEmbedding]
    ) -> Dict:
        """Técnica #3-5: Descuento MICRO progresivo"""
        # Verificar si puede dar más descuento
        remaining = self.state_manager.get_discount_remaining()
        actual_discount = min(amount, remaining)

        if actual_discount == 0:
            return {
                "message": (
                    "Lo siento, ese ya es nuestro mejor precio 😊\n"
                    "¿Te ayudo con algo más?"
                ),
                "technique": "final_offer",
                "discount_offered": 0.0,
            }

        # Registrar descuento
        self.state_manager.record_discount(actual_discount)

        original_price = float(product.price) if product else 0
        new_price = original_price - actual_discount

        message = (
            f"Te lo dejo en *S/{new_price:.0f}* "
            f"({'solo hoy' if actual_discount == 3 else 'por este mes'}) 😊\n\n"
            "Incluye:\n"
        )

        if product:
            message += f"✨ {product.product_name}\n"

        message += (
            "📦 Envío rápido (2-3 días)\n"
            "🔄 Cambio gratis si no te queda\n\n"
            "¿Confirmo tu pedido?"
        )

        return {
            "message": message,
            "technique": "micro_discount",
            "discount_offered": actual_discount,
            "new_price": new_price,
        }

    def handle_size_concern(self) -> str:
        """Maneja duda sobre tallas"""
        return (
            "Te comparto nuestra guía de tallas:\n\n"
            "S:  Busto 82-86cm | Cintura 64-68cm\n"
            "M:  Busto 86-90cm | Cintura 68-72cm\n"
            "L:  Busto 90-94cm | Cintura 72-76cm\n"
            "XL: Busto 94-98cm | Cintura 76-80cm\n\n"
            "🔄 Si no te queda, cambio gratis en 7 días.\n"
            "¿Qué talla crees que te queda mejor?"
        )

    def handle_availability_concern(self, product: Optional[ProductEmbedding]) -> str:
        """Maneja pregunta de disponibilidad"""
        if product:
            if product.is_available and product.stock_quantity > 0:
                return (
                    f"✅ Sí, tenemos el *{product.product_name}* disponible!\n"
                    f"Stock: {product.stock_quantity} unidades\n\n"
                    "¿En qué talla lo necesitas?"
                )
            else:
                return (
                    f"😔 Lo siento, el *{product.product_name}* está agotado.\n"
                    "Pero tengo modelos similares que te pueden gustar.\n"
                    "¿Te los muestro?"
                )
        else:
            return "¿Qué producto te interesa? Así reviso stock para ti 😊"

    def handle_delay_objection(self) -> str:
        """Maneja "lo voy a pensar" """
        return (
            "Claro, tómate tu tiempo 😊\n\n"
            "Solo te comento que:\n"
            "🔥 El stock es limitado\n"
            "📈 Los precios pueden cambiar\n\n"
            "¿Quieres que te lo reserve por 24 horas sin compromiso?"
        )
