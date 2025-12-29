"""
Gestor de estado de conversación (Finite State Machine).
Rastrea en qué etapa del embudo de venta está cada cliente.

Estados del embudo:
1. DISCOVERY: Cliente explorando, descubriendo necesidad
2. ENGAGEMENT: Cliente comprometido, viendo productos
3. CONSIDERATION: Cliente evaluando, posibles objeciones
4. CONVERSION: Cliente listo para comprar
5. CLOSED: Venta cerrada o perdida
"""

from typing import Dict, Any
from django.utils import timezone
from django.db.models import F

from botyapp.models import Contact, ConversationState
from logger import log


class StateManager:
    """Máquina de estados para conversaciones de venta"""

    # Transiciones permitidas (de → a)
    TRANSITIONS = {
        "discovery": ["engagement", "consideration"],
        "engagement": ["consideration", "conversion", "discovery"],
        "consideration": ["conversion", "engagement", "discovery"],
        "conversion": ["closed"],
        "closed": ["discovery"],  # Cliente regresa
    }

    def __init__(self, contact: Contact):
        self.contact = contact
        self.state, _ = ConversationState.objects.get_or_create(contact=contact)

    @property
    def current_stage(self) -> str:
        """Obtiene el estado actual"""
        self.state.refresh_from_db()
        return self.state.current_stage

    def can_transition(self, to_stage: str) -> bool:
        """Verifica si la transición es válida"""
        return to_stage in self.TRANSITIONS.get(self.current_stage, [])

    def transition_to(self, new_stage: str, reason: str = "") -> bool:
        """
        Ejecuta transición de estado.

        Args:
            new_stage: Nuevo estado objetivo
            reason: Razón del cambio (para logging)

        Returns:
            True si transición exitosa, False si no permitida
        """
        if not self.can_transition(new_stage):
            log.warning(
                f"Transición inválida: {self.current_stage} → {new_stage} "
                f"para {self.contact.name}"
            )
            return False

        old_stage = self.current_stage
        self.state.current_stage = new_stage
        self.state.last_interaction = timezone.now()
        self.state.save()

        log.info(
            f"🔄 Estado cambiado: {old_stage} → {new_stage} | "
            f"Cliente: {self.contact.name} | Razón:{reason}"
        )
        return True

    def auto_advance(self, context: Dict[str, Any]) -> None:
        """
        Avanza automáticamente basado en señales conversacionales.

        Args:
            context: Contexto de la conversación
                - has_question: bool
                - viewed_products: int
                - expressed_interest: bool
                - asked_price: bool
                - asked_availability: bool
        """
        current = self.current_stage

        # Discovery → Engagement
        if current == "discovery" and context.get("viewed_products", 0) > 0:
            self.transition_to("engagement", "Cliente vio productos")

        # Engagement → Consideration
        elif current == "engagement":
            if context.get("asked_price") or context.get("asked_availability"):
                self.transition_to(
                    "consideration", "Cliente preguntó precio/disponibilidad"
                )

        # Consideration → Conversion
        elif current == "consideration":
            buying_signals = [
                context.get("asked_sizes"),
                context.get("asked_shipping"),
                context.get("confirmed_interest"),
            ]
            if sum(bool(x) for x in buying_signals) >= 2:
                self.transition_to("conversion", "Múltiples señales de compra")

    def increment_objection_count(self) -> None:
        """Incrementa contador de objeciones"""
        ConversationState.objects.filter(id=self.state.id).update(
            objection_count=F("objection_count") + 1
        )
        self.state.refresh_from_db()

    def add_viewed_product(self, retailer_id: str) -> None:
        """Registra producto visto"""
        viewed = self.state.viewed_products or []
        if retailer_id not in viewed:
            viewed.append(retailer_id)
            self.state.viewed_products = viewed
            self.state.save()

    def add_to_cart(self, product_id: str, quantity: int = 1) -> None:
        """Agrega producto al carrito virtual"""
        cart = self.state.cart_items or []

        # Buscar si ya está en carrito
        found = False
        for item in cart:
            if item.get("product_id") == product_id:
                item["qty"] = item.get("qty", 1) + quantity
                found = True
                break

        if not found:
            cart.append({"product_id": product_id, "qty": quantity})

        self.state.cart_items = cart
        self.state.save()

    def update_engagement_score(self, delta: float) -> None:
        """
        Actualiza score de engagement.

        Args:
            delta: Cambio en el score (positivo o negativo)
        """
        new_score = max(0, min(100, self.state.engagement_score + delta))
        self.state.engagement_score = new_score
        self.state.save()

    def record_discount(self, amount: float) -> bool:
        """
        Registra descuento otorgado.

        Returns:
            True si aún puede dar más descuento, False si llegó al límite
        """
        MAX_DISCOUNT = 3.0  # soles

        new_total = float(self.state.discount_given) + amount
        if new_total > MAX_DISCOUNT:
            return False

        self.state.discount_given = new_total
        self.state.save()
        return True

    def get_discount_remaining(self) -> float:
        """Descuento que aún puede ofrecer"""
        MAX_DISCOUNT = 3.0
        return MAX_DISCOUNT - float(self.state.discount_given)

    def reset(self) -> None:
        """Resetea estado (nueva conversación)"""
        self.state.current_stage = "discovery"
        self.state.engagement_score = 0.0
        self.state.objection_count = 0
        self.state.discount_given = 0.0
        self.state.cart_items = []
        self.state.viewed_products = []
        self.state.last_objection_type = ""
        self.state.save()

        log.info(f"🔄 Estado reseteado para {self.contact.name}")
