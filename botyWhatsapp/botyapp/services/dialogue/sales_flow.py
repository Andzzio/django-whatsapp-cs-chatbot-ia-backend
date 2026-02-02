"""
Orquestador del flujo completo de venta.
Coordina: Búsqueda Semántica → Estado → Objeciones → Cierre
"""

from typing import Optional, Dict, Any
from botyapp.models import Contact, ProductEmbedding
from botyapp.services.intelligence.semantic_search import semantic_search
from botyapp.services.dialogue.state_manager import StateManager
from botyapp.services.dialogue.context_tracker import ContextTracker
from botyapp.services.intelligence.objection_handler import ObjectionHandler
from botyapp.services.intelligence.sales_closer import SalesCloser
from logger import log


class SalesFlow:
    """Coordina todo el flujo de venta automático"""

    def __init__(self, contact: Contact):
        self.contact = contact
        self.state_manager = StateManager(contact)
        self.context_tracker = ContextTracker(contact)
        self.objection_handler = ObjectionHandler(contact)
        self.sales_closer = SalesCloser(contact)

    def process_message(self, user_message: str, llm_response: str) -> Dict[str, Any]:
        """
        Procesa un mensaje en el contexto de venta.

        Args:
            user_message: Mensaje del usuario
            llm_response: Respuesta base del LLM

        Returns:
            Dict con acciones a tomar:
            {
                'response': str,  # Mensaje final
                'should_show_products': bool,
                'products': List[ProductEmbedding],
                'state_changed': bool,
                'new_state': str,
            }
        """
        result = {
            "response": llm_response,
            "should_show_products": False,
            "products": [],
            "state_changed": False,
            "new_state": self.state_manager.current_stage,
            "action_taken": None,
        }

        # 1. Analizar contexto del mensaje
        context = self.context_tracker.analyze_conversation()
        intent = self.context_tracker.detect_intent_from_message(user_message)

        # 2. Actualizar engagement score
        if context["engagement_signals"] > 0:
            self.state_manager.update_engagement_score(
                context["engagement_signals"] * 10
            )

        # 3. Avanzar estado automáticamente
        old_stage = self.state_manager.current_stage
        self.state_manager.auto_advance(
            {
                "viewed_products": len(self.state_manager.state.viewed_products),
                "asked_price": context["asked_price"],
                "asked_availability": context["asked_availability"],
                "asked_sizes": context["asked_sizes"],
                "asked_shipping": context["asked_shipping"],
                "confirmed_interest": context["confirmed_interest"],
            }
        )

        if old_stage != self.state_manager.current_stage:
            result["state_changed"] = True
            result["new_state"] = self.state_manager.current_stage

        # 4. Manejar objeciones
        if context["has_objection"]:
            objection_type = context["objection_type"]

            if objection_type == "price":
                # Buscar producto mencionado en conversación
                products_mentioned = self.context_tracker.extract_product_mentions()
                product = self._find_product(products_mentioned)

                objection_response = self.objection_handler.handle_price_objection(
                    product
                )
                result["response"] = objection_response["message"]
                result["action_taken"] = "handled_price_objection"

                log.info(
                    f"💰 Objeción de precio manejada para {self.contact.name} "
                    f"(intento {self.state_manager.state.objection_count})"
                )

            elif objection_type == "delay":
                result["response"] = self.objection_handler.handle_delay_objection()
                result["action_taken"] = "handled_delay"

        # 5. Búsqueda semántica si menciona productos
        if intent == "browse" or intent == "question":
            products_mentioned = self.context_tracker.extract_product_mentions()
            if products_mentioned:
                products = self._semantic_product_search(" ".join(products_mentioned))
                if products:
                    result["should_show_products"] = True
                    result["products"] = products
                    result["action_taken"] = "semantic_search"

        # 6. Intentar cierre si hay señales
        if self.sales_closer.should_attempt_close():
            product_mentioned = self.context_tracker.extract_product_mentions()
            product = self._find_product(product_mentioned)

            close_msg = self.sales_closer.generate_close_message(product)

            # Agregar al final de la respuesta del LLM
            result["response"] = f"{llm_response}\n\n{close_msg}"
            result["action_taken"] = "attempted_close"

            log.info(f"🎯 Intento de cierre para {self.contact.name}")

        return result

    def _semantic_product_search(
        self, query: str, top_k: int = 5
    ) -> list[ProductEmbedding]:
        """Ejecuta búsqueda semántica y registra productos vistos"""
        results = semantic_search.search_products(
            query, top_k=top_k, min_similarity=0.5
        )

        # Registrar productos vistos
        for product, score in results:
            self.state_manager.add_viewed_product(product.retailer_id)

        return [product for product, score in results]

    def _find_product(self, keywords: list[str]) -> Optional[ProductEmbedding]:
        """Busca producto por keywords (fallback)"""
        if not keywords:
            return None

        for keyword in keywords:
            try:
                # Búsqueda simple por nombre
                product = ProductEmbedding.objects.filter(
                    product_name__icontains=keyword, is_available=True
                ).first()

                if product:
                    return product
            except Exception:
                continue

        return None

    def handle_product_selected(self, product_id: str, quantity: int = 1) -> str:
        """
        Cliente selecciona un producto (desde catálogo).
        """
        try:
            product = ProductEmbedding.objects.get(retailer_id=product_id)
        except ProductEmbedding.DoesNotExist:
            return "Lo siento, no encontré ese producto."

        # Agregar a carrito
        self.state_manager.add_to_cart(product_id, quantity)
        self.state_manager.add_viewed_product(product_id)

        # Avanzar a consideration
        if self.state_manager.current_stage == "discovery":
            self.state_manager.transition_to("engagement", "Producto seleccionado")

        # Generar respuesta
        return (
            f"✅ Agregado al carrito:\n"
            f"• {product.product_name} x{quantity}\n"
            f"💰 S/{float(product.price) * quantity:.0f}\n\n"
            "¿Algo más que necesites?"
        )

    def get_recommendations(
        self, based_on: Optional[str] = None
    ) -> list[ProductEmbedding]:
        """
        Genera recomendaciones personalizadas.

        Args:
            based_on: Producto base para similitudes ('productid' o None para histórico)
        """
        if based_on:
            results = semantic_search.find_similar_products(based_on, top_k=3)
            return [product for product, score in results]

        # Basado en preferencias del usuario
        viewed = self.state_manager.state.viewed_products
        if viewed:
            # Buscar similares al último visto
            return self.get_recommendations(based_on=viewed[-1])

        return []
