"""
Motor de cierre de ventas usando técnicas psicológicas.
"""

from typing import Optional
from botyapp.models import Contact, ProductEmbedding
from botyapp.services.dialogue.state_manager import StateManager
from botyapp.services.dialogue.context_tracker import ContextTracker


class SalesCloser:
    """Cierre de ventas con técnicas probadas"""

    def __init__(self, contact: Contact):
        self.contact = contact
        self.state_manager = StateManager(contact)
        self.context = ContextTracker(contact)

    def should_attempt_close(self) -> bool:
        """
        Detecta si es momento de intentar cierre.

        Señales de compra:
        - Preguntó por precio
        - Preguntó por tallas
        - Preguntó por envío
        - Engagement score > 70
        - Vio productos > 2 veces
        """
        analysis = self.context.analyze_conversation()
        state = self.state_manager.state

        signals = [
            analysis["asked_price"],
            analysis["asked_sizes"],
            analysis["asked_shipping"],
            state.engagement_score > 70,
            len(state.viewed_products) >= 2,
        ]

        return sum(signals) >= 2

    def generate_close_message(self, product: Optional[ProductEmbedding] = None) -> str:
        """
        Genera mensaje de cierre personalizado.

        Usa técnicas en orden:
        1. Social Proof (siempre)
        2. Urgencia (si stock bajo)
        3. Garantía (reduce riesgo)
        4. Descuento (solo si ya dio objeciones)
        5. Call to Action directo
        """
        state = self.state_manager.state
        close_elements = []

        # Encabezado
        if product:
            close_elements.append(f"*{product.product_name}*\n")

        # 1. Social Proof (SIEMPRE)
        close_elements.append("⭐ Producto popular entre nuestras clientas")

        # 2. Urgencia (SI aplica)
        if product and product.stock_quantity < 5:
            close_elements.append(f"🔥 Solo {product.stock_quantity} disponibles")

        # 3. Garantía
        close_elements.append("🔄 Cambio gratis en 7 días si no te queda")

        # 4. Precio (con descuento si aplicó)
        if product:
            price = float(product.price)
            discount = float(state.discount_given)

            if discount > 0:
                final_price = price - discount
                close_elements.append(
                    f"💰 Precio especial: *S/{final_price:.0f}* "
                    f"(S/{discount:.0f} de descuento)"
                )
            else:
                close_elements.append(f"💰 Precio: *S/{price:.0f}*")

        # 5. Call to Action
        close_elements.append("\n¿Confirmo tu pedido? ✅")

        return "\n".join(close_elements)

    def generate_cart_summary(self) -> Optional[str]:
        """
        Genera resumen de carrito si hay items.
        """
        cart = self.state_manager.state.cart_items

        if not cart:
            return None

        summary = "🛒 *Tu carrito:*\n\n"

        total = 0.0
        for item in cart:
            product_id = item.get("product_id")
            qty = item.get("qty", 1)

            try:
                product = ProductEmbedding.objects.get(retailer_id=product_id)
                price = float(product.price) * qty
                total += price

                summary += f"• {product.product_name} x{qty} - S/{price:.0f}\n"
            except ProductEmbedding.DoesNotExist:
                continue

        # Aplicar descuento si hay
        discount = float(self.state_manager.state.discount_given)
        if discount > 0:
            total -= discount
            summary += f"\n🎁 Descuento: -S/{discount:.0f}\n"

        # Envío
        shipping = 5.0  # Costo fijo (ajustar según lógica)
        total += shipping

        summary += f"\n📦 Envío: S/{shipping:.0f}\n"
        summary += f"💳 *Total: S/{total:.0f}*\n\n"
        summary += "¿Confirmas tu pedido?"

        return summary

    def generate_upsell_message(self, current_product: ProductEmbedding) -> str:
        """
        Genera mensaje de upselling (productos relacionados).
        """
        from botyapp.services.intelligence.semantic_search import semantic_search

        similar = semantic_search.find_similar_products(
            current_product.retailer_id, top_k=2
        )

        if not similar:
            return ""

        message = "💡 A otras clientas también les gustó:\n\n"

        for product, score in similar:
            message += f"• *{product.product_name}* - S/{product.price}\n"

        message += "\n¿Te interesa alguno?"

        return message

    def create_payment_link_message(
        self, product: ProductEmbedding, final_price: float
    ) -> str:
        """
        Genera mensaje con link de pago (integrar con Yape/Plin/Niubiz).
        """
        # TODO: Integrar con plataforma de pagos real
        # Por ahora, mensaje manual

        message = (
            f"✅ *Pedido confirmado!*\n\n"
            f"📦 {product.product_name}\n"
            f"💰 Total: S/{final_price:.0f}\n\n"
            "Para confirmar tu pago:\n"
            "📱 Pago: **924471992** (Yape/Plin)\n"
            "💳 Depósito: Cuenta 123-456-789\n\n"
            "Envía tu comprobante y tu dirección de envío 📍"
        )

        return message
