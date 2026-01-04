"""
Sistema de checkout en 3 pasos: Producto → Dirección → Pago
Maneja el flujo completo de conversión de intención a pedido confirmado.
"""

from django.conf import settings
from botyapp.models import Order, OrderItem, Contact, ProductEmbedding
from typing import Optional
from dataclasses import dataclass
from logger import log


@dataclass
class CheckoutResult:
    """Resultado de operación de checkout"""

    order: Order
    message: str
    next_stage: str
    should_send_card: bool = False


class OrderProcessor:
    """
    Procesador de pedidos con FSM de checkout.

    Flujo:
    1. CONFIRMING_PRODUCT: Cliente confirma qué quiere
    2. COLLECTING_ADDRESS: Captura distrito/dirección
    3. PROCESSING_PAYMENT: Guía a métodos de pago
    4. COMPLETED: Pedido confirmado
    """

    SHIPPING_COSTS = {
        # Distritos de Lima (puedes personalizarlo)
        "default": 10.00,
        # Ejemplos de costos personalizados
        "san isidro": 8.00,
        "miraflores": 8.00,
        "surco": 10.00,
        "la molina": 12.00,
    }

    def start_checkout(
        self, contact: Contact, product: ProductEmbedding, quantity: int = 1
    ) -> CheckoutResult:
        """
        Inicia proceso de checkout cuando se detecta intención de compra fuerte.

        Args:
            contact: Cliente
            product: Producto seleccionado
            quantity: Cantidad (default: 1)

        Returns:
            CheckoutResult con mensaje y orden creada
        """
        # Crear pedido en estado pendiente
        order = Order.objects.create(
            contact=contact, status="PENDING", checkout_stage="CONFIRMING_PRODUCT"
        )

        # Agregar item al pedido
        OrderItem.objects.create(
            order=order,
            product=product,
            quantity=quantity,
            price=product.price,
            product_name=product.product_name,
        )

        # Calcular totales
        order.calculate_totals()

        # Actualizar estado conversacional
        try:
            conv_state = contact.conversation_state
            conv_state.current_stage = "conversion"
            conv_state.save(update_fields=["current_stage"])
        except Exception:
            pass  # No crítico si no tiene conversation_state

        # Mensaje de confirmación agresivo
        total = float(product.price) * quantity
        message = (
            f"🎉 ¡Perfecto! Procesando tu pedido:\n\n"
            f"📦 **{product.product_name}**\n"
            f"   Cantidad: {quantity}\n"
            f"   💰 Total: S/{total:.0f}\n\n"
            f"🚚 **Envío:**\n"
            f"¿A qué distrito envío tu pedido?\n\n"
            f"_Escribe tu distrito (ejemplo: San Isidro)_"
        )

        log.info(f"✅ Checkout started: Order #{order.id} for {contact.name}")

        return CheckoutResult(
            order=order,
            message=message,
            next_stage="COLLECTING_ADDRESS",
            should_send_card=True,  # Mostrar tarjeta del producto
        )

    def collect_address(
        self, order: Order, district: str, full_address: Optional[str] = None
    ) -> CheckoutResult:
        """
        Captura dirección de envío y muestra métodos de pago.

        Args:
            order: Orden en proceso
            district: Distrito de envío
            full_address: Dirección completa (opcional)

        Returns:
            CheckoutResult con métodos de pago
        """
        # Guardar dirección
        order.shipping_district = district.strip()
        order.shipping_address = full_address or f"{district}, Lima"
        order.checkout_stage = "PROCESSING_PAYMENT"

        # Calcular costo de envío
        district_lower = district.lower().strip()
        shipping_cost = self.SHIPPING_COSTS.get(
            district_lower, self.SHIPPING_COSTS["default"]
        )
        order.shipping_cost = shipping_cost

        # Recalcular totales
        order.calculate_totals()
        order.save()

        # Mensaje con métodos de pago
        message = (
            f"✅ Dirección registrada: **{district}**\n\n"
            f"💳 **Resumen de Pago:**\n"
            f"   Productos: S/{order.subtotal:.0f}\n"
            f"   Envío: S/{shipping_cost:.0f}\n"
            f"   **Total: S/{order.total_amount:.0f}**\n\n"
            f"📱 **Métodos de pago disponibles:**\n"
            f"1️⃣ Yape/Plin: {getattr(settings, 'YAPE_NUMBER', '999-999-999')}\n"
            f"2️⃣ Transferencia BCP: {getattr(settings, 'BCP_ACCOUNT', '123-456-789')}\n"
            f"3️⃣ Contra-entrega (+S/5)\n\n"
            f"_Envía tu comprobante de pago o elige contra-entrega_"
        )

        log.info(f"✅ Address collected for Order #{order.id}: {district}")

        return CheckoutResult(
            order=order, message=message, next_stage="PROCESSING_PAYMENT"
        )

    def process_payment(
        self, order: Order, payment_method: str, payment_proof: Optional[str] = None
    ) -> CheckoutResult:
        """
        Marca pago procesado (pendiente de verificación manual).

        Args:
            order: Orden en proceso
            payment_method: Método usado (yape,  plin, transferencia, contra_entrega)
            payment_proof: URL del comprobante o mensaje del cliente

        Returns:
            CheckoutResult con confirmación
        """
        order.payment_method = payment_method
        order.payment_proof = payment_proof or ""
        order.checkout_stage = "COMPLETED"
        order.status = "CONFIRMED"  # Confirmado (pendiente de verificación)
        order.save()

        # Mensaje de confirmación final
        message = (
            f"🎊 ¡Pedido confirmado!\n\n"
            f"📋 **Número de pedido:** #{str(order.id)[:8]}\n"
            f"📦 Tu **{order.items.first().product_name}** llegará en 2-3 días\n\n"
            f"Te avisaremos cuando esté en camino. ¡Gracias por tu compra! 💕"
        )

        log.info(f"✅ Payment processed for Order #{order.id}: {payment_method}")

        return CheckoutResult(order=order, message=message, next_stage="COMPLETED")

    def complete_order_contra_entrega(self, order: Order) -> CheckoutResult:
        """
        Completa pedido con contra-entrega (sin comprobante).

        Args:
            order: Orden en proceso

        Returns:
            CheckoutResult con confirmación
        """
        order.payment_method = "contra_entrega"
        order.payment_proof = "Pago contra-entrega"
        order.shipping_cost += 5.00  # Cargo adicional
        order.calculate_totals()
        order.checkout_stage = "COMPLETED"
        order.status = "CONFIRMED"
        order.save()

        message = (
            f"✅ Pedido confirmado con **pago contra-entrega**\n\n"
            f"📋 **Número:** #{str(order.id)[:8]}\n"
            f"💰 **Total a pagar:** S/{order.total_amount:.0f}\n"
            f"   (Incluye S/5 por contra-entrega)\n\n"
            f"📦 Llegará en 2-3 días. Prepara el efecto 😊\n"
            f"¡Gracias por tu compra! 💕"
        )

        log.info(f"✅ Order #{order.id} confirmed with contra-entrega")

        return CheckoutResult(order=order, message=message, next_stage="COMPLETED")

    def get_active_order(self, contact: Contact) -> Optional[Order]:
        """
        Obtiene pedido activo del cliente (si tiene uno en proceso).

        Args:
            contact: Cliente

        Returns:
            Order activo o None
        """
        return Order.objects.filter(
            contact=contact,
            status="PENDING",
            checkout_stage__in=[
                "CONFIRMING_PRODUCT",
                "COLLECTING_ADDRESS",
                "PROCESSING_PAYMENT",
            ],
        ).first()

    def cancel_order(self, order: Order, reason: str = "Cliente canceló") -> str:
        """
        Cancela un pedido en proceso.

        Args:
            order: Orden a cancelar
            reason: Razón de cancelación

        Returns:
            Mensaje de confirmación
        """
        order.status = "CANCELLED"
        order.payment_proof = f"CANCELADO: {reason}"
        order.save()

        log.info(f"❌ Order #{order.id} cancelled: {reason}")

        return (
            f"✅ Pedido #{str(order.id)[:8]} cancelado.\n"
            f"Si cambias de opinión, con gusto te ayudo a ordenar de nuevo 😊"
        )
