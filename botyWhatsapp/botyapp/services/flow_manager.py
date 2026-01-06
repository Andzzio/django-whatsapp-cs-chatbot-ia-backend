from botyapp.models import Contact
from botyapp.services.whatsapp import send_whatsapp_message, send_catalog_message
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

        # 1. SOPORTE HUMANO (BLOQUEO TOTAL)
        if state == Contact.States.LOCKED_HUMAN:
            # Silencio absoluto. El bot está "apagado" para este usuario.
            return True

        # 2. SELECCIÓN DE HANDLER SEGÚN ESTADO
        handler_map = {
            Contact.States.INITIAL: FlowManager._handle_initial,
            Contact.States.BROWSING_CATALOG: FlowManager._handle_browsing,
            Contact.States.PRODUCT_SELECTION: FlowManager._handle_product_selection,
            Contact.States.CONFIRM_CART: FlowManager._handle_confirm_cart,
            Contact.States.COLLECT_ADDRESS: FlowManager._handle_address,
            Contact.States.SELECT_PAYMENT: FlowManager._handle_payment,
            Contact.States.UPLOAD_PROOF: FlowManager._handle_proof,
            Contact.States.COMPLETED: FlowManager._handle_completed,
        }

        handler = handler_map.get(state, FlowManager._handle_initial)
        return handler(contact, message_body, message_type, media_id)

    # ----------------------------------------------------------------------
    # STATE HANDLERS
    # ----------------------------------------------------------------------

    @staticmethod
    def _handle_initial(contact, text, msg_type, media_id):
        """
        Estado Inicial.
        - Si detecta intención de venta severa -> Cambia estado y ejecuta.
        - Si no -> Retorna False para dejar pasar al LLM (Charla casual).
        """
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
            FlowManager.transition_to(contact, Contact.States.LOCKED_HUMAN)
            send_whatsapp_message(
                contact.phone,
                "👩‍💻 Entendido. Un asesor humano te atenderá en breve. \n(El bot se ha pausado).",
            )
            # TODO: Notificar al dashboard
            contact.needs_human_attention = True
            contact.save()
            return True

        # C. Default -> Dejar pasar al LLM
        return False

    @staticmethod
    def _handle_browsing(contact, text, msg_type, media_id):
        """
        El usuario está viendo el catálogo.
        Esperamos: "Quiero el rojo", "Precio del vestido", etc.
        """
        # Si el usuario quiere salir o hablar casual, podemos detectar keywords de salida
        # Por ahora, asumimos que todo es búsqueda de producto.

        # 1. Buscar producto en Embeddings (Semantic Search)
        from botyapp.services.catalog import search_and_send_products

        # Usamos la función de catálogo directamente
        found = search_and_send_products(contact.phone, text)

        if found:
            # Preguntar si quiere comprar
            send_whatsapp_message(
                contact.phone,
                "¿Te gustaría pedir alguno? Responde con el nombre o 'Sí'",
            )
            FlowManager.transition_to(contact, Contact.States.PRODUCT_SELECTION)

        # Si no encontró, search_and_send_products ya envió el mensaje de fallback.
        # Nos quedamos en BROWSING_CATALOG esperando otro intento.
        return True

        return True

    @staticmethod
    def _handle_product_selection(contact, text, msg_type, media_id):
        """
        Usuario seleccionó producto. Confirmar e ir a Checkout.
        """
        # Detección simple: Si dice "Sí" o nombre de producto.
        # Idealmente extraemos el item.

        FlowManager.transition_to(contact, Contact.States.CONFIRM_CART)
        send_whatsapp_message(
            contact.phone,
            "¡Perfecto! He añadido eso a tu carrito virtual 🛒.\n\nEl total es S/ XX.XX\n\n¿Deseas confirmar el pedido? (Responde 'Confirmar')",
        )
        return True

    @staticmethod
    def _handle_confirm_cart(contact, text, msg_type, media_id):
        if "confirmar" in text.lower() or "si" in text.lower():
            FlowManager.transition_to(contact, Contact.States.COLLECT_ADDRESS)
            send_whatsapp_message(
                contact.phone,
                "¡Excelente! 🎉\n\nPor favor envíame tu **Dirección de Entrega** (Departamento, Distrito, Calle y Número).",
            )
        else:
            send_whatsapp_message(
                contact.phone,
                "Para continuar, por favor escribe 'Confirmar' o dime si quieres ver más productos.",
            )
            # Si quiere ver más, podríamos volver a BROWSING.
        return True

    @staticmethod
    def _handle_address(contact, text, msg_type, media_id):
        # Validación básica: longitud mínima
        if len(text) < 10:
            send_whatsapp_message(
                contact.phone,
                "Esa dirección parece muy corta. Por favor envíame la dirección completa (Distrito, Calle, #).",
            )
            return True

        # Guardar en contexto
        contact.flow_context["address"] = text
        contact.save()

        FlowManager.transition_to(contact, Contact.States.SELECT_PAYMENT)
        send_whatsapp_message(
            contact.phone,
            "¡Anotado! 📝\n\nSelecciona tu método de pago:\n1. Yape / Plin\n2. Transferencia BCP\n\n(Escribe 1 o 2)",
        )
        return True

    @staticmethod
    def _handle_payment(contact, text, msg_type, media_id):
        FlowManager.transition_to(contact, Contact.States.UPLOAD_PROOF)
        send_whatsapp_message(
            contact.phone,
            "Perfecto. Por favor realiza el pago al número **999-999-999** y envíame la **FOTO del comprobante** aquí. 📸",
        )
        return True

    @staticmethod
    def _handle_proof(contact, text, msg_type, media_id):
        if msg_type == "image":
            FlowManager.transition_to(contact, Contact.States.COMPLETED)
            send_whatsapp_message(
                contact.phone,
                "¡Recibido! 🧾✨\n\nTu pedido ha sido **CONFIRMADO**. Un asesor verificará el pago y te enviaremos el número de tracking pronto.\n\n¡Gracias por tu compra! ❤️",
            )
            contact.flow_context["payment_proof_id"] = media_id
            contact.save()

            # Crear Orden en DB (Simplificado)
            # Order.objects.create(...)
        else:
            send_whatsapp_message(
                contact.phone,
                "Por favor envía una **IMAGEN** del comprobante para procesar tu pedido. 🙏",
            )
        return True

    @staticmethod
    def _handle_completed(contact, text, msg_type, media_id):
        # Ya terminó. Si habla de nuevo, ¿lo mandamos a inicial?
        # O le decimos "Tu pedido está en proceso".
        # Reset a Initial para permitir nueva compra
        FlowManager.transition_to(contact, Contact.States.INITIAL)
        # Dejmos pasar al LLM para saludo? Or respondemos nosotros.
        send_whatsapp_message(
            contact.phone, "Hola de nuevo 👋. ¿En qué puedo ayudarte hoy?"
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
