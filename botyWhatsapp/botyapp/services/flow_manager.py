from botyapp.models import Contact
from botyapp.services.whatsapp import (
    send_whatsapp_message,
    send_catalog_message,
    send_interactive_buttons,
)
from botyapp.services.intent.classifier import intent_classifier
from logger import log


class FlowManager:
    """
    Gestor de Flujos Determinísticos (Finite State Machine).
    Controla la navegación estricta para ventas y soporte.
    """

    @staticmethod
    def process(
        contact: Contact,
        message_body: str,
        message_type: str = "text",
        media_id: str = None,
    ):
        """
        Punto de entrada único.
        Retorna True si el mensaje fue manejado por el flujo.
        Retorna False si debe pasar al LLM (Solo en estado INITIAL).
        """
        state = contact.current_state

        log.info(
            f"🚦 FlowManager Processing | User: {contact.name} | State: {state} | Msg: {message_body[:30]}"
        )

        # 0. INTERCEPTOR DE IMÁGENES (Priority High)
        if media_id:
            return FlowManager._handle_incoming_image(contact, media_id)

        # 0.1 INTERCEPTOR DE BOTONES (Payloads)
        if message_type == "interactive":
            # "action_handoff_image" or "action_show_catalog"
            if message_body == "action_handoff_image":
                return FlowManager._perform_handoff(
                    contact, "User sent image and requested agent."
                )
            elif message_body == "action_show_catalog":
                # Reset to browsing
                FlowManager.transition_to(contact, Contact.States.BROWSING_CATALOG)
                send_catalog_message(contact.phone, "¡Aquí tienes! 🛍️")
                return True

        # 1. VALIDACIÓN MANUAL (Single Source of Truth)
        # La validación `is_bot_active` ya ocurrió en message_handler.
        # Aquí asumimos que si llegamos, el bot TIENE permiso para hablar.

        # 2. SELECCIÓN DE HANDLER SEGÚN ESTADO
        handler_map = {
            Contact.States.INITIAL: FlowManager._handle_initial,
            Contact.States.BROWSING_CATALOG: FlowManager._handle_browsing,
            Contact.States.PRODUCT_SELECTION: FlowManager._handle_product_selection,
            Contact.States.CONFIRM_CART: FlowManager._handle_confirm_cart,
            # Legacy states fallback to Initial (via default get)
            Contact.States.COMPLETED: FlowManager._handle_completed,
            # Si se reactiva manualmente, LOCKED_HUMAN se comporta como INITIAL
            Contact.States.LOCKED_HUMAN: FlowManager._handle_initial,
        }

        handler = handler_map.get(state, FlowManager._handle_initial)
        return handler(contact, message_body, message_type, media_id)

    # ----------------------------------------------------------------------
    # STATE HANDLERS
    # ----------------------------------------------------------------------

    @staticmethod
    def _update_lead_status(contact, status):
        """
        Updates the contact's lead status tag.
        Removes old status tags to ensure purely 'Current State'.
        """
        # Define statuses
        STATUSES = ["LEAD:COLD", "LEAD:WARM", "LEAD:HOT", "LEAD:DISTRESSED"]

        # Remove old status tags
        current_tags = contact.tags or []
        new_tags = [t for t in current_tags if t not in STATUSES]

        # Add new status
        tag = f"LEAD:{status}"
        if tag not in new_tags:
            new_tags.append(tag)

        contact.tags = new_tags
        contact.save()
        log.info(f"🏷️ Lead Status Updated: {contact.name} -> {status}")

    @staticmethod
    def _handle_initial(contact, text, msg_type, media_id):
        """
        Estado Inicial.
        """
        FlowManager._update_lead_status(contact, "COLD")

        # 1. Clasificación Rápida (Fuzzy + AI)
        intent_result = intent_classifier.classify(text)

        # A. Solicitud de Catálogo -> Mover a BROWSING
        if intent_result.action == "show_catalog":
            FlowManager.transition_to(contact, Contact.States.BROWSING_CATALOG)
            send_catalog_message(
                contact.phone,
                "¡Claro! Aquí tienes nuestra colección completa ✨\n¿Buscas algo específico (ej: vestidos, blusas)?",
            )
            return True

        # B. Solicitud de Humano -> Mover a LOCKED_HUMAN
        if intent_result.action == "contact_support":
            FlowManager._handoff_distressed(contact)
            return True

        # C. Default -> Dejar pasar al LLM
        return False

    @staticmethod
    def _handle_browsing(contact, text, msg_type, media_id):
        FlowManager._update_lead_status(contact, "COLD")

        if not text:
            return True

        # 1. Inteligencia Previa: ¿Es una búsqueda o una charla?
        intent = intent_classifier.classify(text)

        # Si el usuario solo saluda ("Hola") o pide soporte
        if intent.action in ["contact_support"]:
            FlowManager._handoff_distressed(contact)
            return True

        if intent.intent == "greeting" and len(text) < 10:
            send_whatsapp_message(
                contact.phone,
                "¡Hola! Sigo aquí. Dime qué buscas o escribe 'Salir' para volver al inicio.",
            )
            return True

        # 2. Buscar producto en Embeddings (Semantic Search)
        from botyapp.services.catalog import search_and_send_products

        # Usamos la función de catálogo directamente
        found = search_and_send_products(contact.phone, text)

        if found:
            # Preguntar si quiere comprar
            send_whatsapp_message(
                contact.phone,
                "¿Te gustaría pedir alguno? 🤔\nPuedes preguntar precios o decir **'Lo quiero'**.",
            )
            FlowManager.transition_to(contact, Contact.States.PRODUCT_SELECTION)

        return True

    @staticmethod
    def _handle_product_selection(contact, text, msg_type, media_id):
        # 1. ANALYZE INTENT
        # Is it a question (WARM) or a buy signal (HOT)?

        text_lower = text.lower()
        # Keywords for "Question"
        question_keywords = [
            "precio",
            "costo",
            "talla",
            "medida",
            "tela",
            "material",
            "color",
            "foto",
            "?",
        ]
        is_question = any(k in text_lower for k in question_keywords)

        if is_question:
            # WARM LEAD logic
            FlowManager._update_lead_status(contact, "WARM")
            # Here we would use an LLM/Tool to answer. For now, acknowledge and nudge.
            send_whatsapp_message(
                contact.phone,
                "Es una excelente prenda. ✨ \n\nSi deseas separarla antes de que se agote, escribe **'Lo quiero'**.",
            )
            return True

        # Check Confirmation (HOT LEAD)
        buy_keywords = [
            "quiero",
            "comprar",
            "dame",
            "llevo",
            "confirmar",
            "pedido",
            "si",
            "sí",
            "ok",
        ]
        is_buy_signal = any(k in text_lower for k in buy_keywords)

        if is_buy_signal:
            return FlowManager._perform_handoff(contact, text)

        # Fallback (Ambiguous)
        send_whatsapp_message(
            contact.phone,
            "¿Te gustaría confirmar el pedido? Responde 'Sí' o 'Lo quiero'.",
        )
        return True

    @staticmethod
    def _handle_confirm_cart(contact, text, msg_type, media_id):
        # Unused state primarily, but redirect to handoff just in case
        return FlowManager._perform_handoff(contact, text)

    # ----------------------------------------------------------------------
    # HANDOFF LOGIC (The "Goal")
    # ----------------------------------------------------------------------

    @staticmethod
    def _perform_handoff(contact, text):
        """
        Executes the 'Zero-Interference' Handoff.
        1. Creates Order (WAITING_AGENT).
        2. Tags as HOT.
        3. Locks Chat.
        """
        FlowManager._update_lead_status(contact, "HOT")

        # 1. Recuperar contexto (Producto)
        selected_product = contact.flow_context.get(
            "selected_product", text[:50]
        )  # Use text as fallback name

        # 2. Crear Orden (Pending Agent)
        from botyapp.models import Order, OrderItem

        # Create minimal order structure
        order = Order.objects.create(
            contact=contact,
            status="PROFORMA",  # Dashboard sees this in Proformas Tab
            checkout_stage="COLLECTING_ADDRESS",  # Agent needs to collect this
            payment_proof="WAITING_AGENT_INTERVENTION",
        )

        # Add placeholder item
        OrderItem.objects.create(
            order=order,
            product_name=selected_product,  # Name from context or text
            price=0,  # Agent fixes price
            quantity=1,
        )

        log.info(f"🚨 HOT LEAD: Handoff triggered for {contact.name}")

        # 3. Notificar y Bloquear (Make it sound like a human checking stock)
        send_whatsapp_message(
            contact.phone,
            "¡Excelente elección! 🛍️✨\n\nDéjame verificar el stock en almacén ahora mismo. 🧐\n\nEn unos minutos te confirmo los detalles para coordinar el envío. ⏳",
        )

        # Disable bot so the toggle turns OFF in the App
        contact.is_bot_active = False
        contact.needs_human_attention = True
        contact.save()
        FlowManager.transition_to(contact, Contact.States.LOCKED_HUMAN)
        return True

    @staticmethod
    def _handoff_distressed(contact):
        FlowManager._update_lead_status(contact, "DISTRESSED")
        send_whatsapp_message(contact.phone, "Entiendo, un humano te atenderá. 👨‍💻")
        # Disable bot logic
        contact.is_bot_active = False
        contact.needs_human_attention = True
        FlowManager.transition_to(contact, Contact.States.LOCKED_HUMAN)

    @staticmethod
    def _handle_completed(contact, text, msg_type, media_id):
        # Reset a Initial para permitir nueva compra
        FlowManager.transition_to(contact, Contact.States.INITIAL)
        # Dejmos pasar al LLM para saludo? Or respondemos nosotros.
        send_whatsapp_message(
            contact.phone, "Hola de nuevo 👋. ¿En qué puedo ayudarte hoy?"
        )
        return True

    @staticmethod
    def _handle_incoming_image(contact, media_id):
        """
        Maneja CUALQUIER imagen entrante.
        Ofrece: Contactar Humano vs Ver Catálogo.
        """
        buttons = [
            ("action_handoff_image", "Contactar Asesor"),
            ("action_show_catalog", "Ver Catálogo"),
        ]
        send_interactive_buttons(
            contact.phone,
            "📸 Imagen recibida.\n\n¿Deseas que un asesor revise est@ foto o prefieres ver nuestro catálogo?",
            buttons,
        )
        return True

    # ----------------------------------------------------------------------
    # HELPERS
    # ----------------------------------------------------------------------

    @staticmethod
    def transition_to(contact, new_state):
        old_state = contact.current_state
        contact.current_state = new_state
        contact.save()
        log.info(
            f"🔄 State Transition: {old_state} -> {new_state} | User: {contact.phone}"
        )
