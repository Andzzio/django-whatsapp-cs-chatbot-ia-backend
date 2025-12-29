from botyapp.models import Contact
from logger import log
from typing import List


class CRMService:
    """
    Servicio de Inteligencia de Negocio (CRM).
    Analiza interacciones para perfilar usuarios automáticamente.
    """

    INTENT_KEYWORDS = {
        "compra": [
            "precio",
            "cuanto cuesta",
            "comprar",
            "pedido",
            "orden",
            "me llevo",
            "quiero",
        ],
        "catalogo": ["ver", "catalogo", "productos", "fotos", "modelos", "tallas"],
        "humano": ["persona", "humano", "asesor", "hablar con alguien", "soporte"],
    }

    TAG_RULES = {
        "vip": 50,  # Score > 50 -> VIP
        "caliente": 20,  # Score > 20 -> Lead Caliente
    }

    @staticmethod
    def analyze_interaction(phone: str, text: str, tool_used: str = None):
        """
        Analiza un mensaje entrante y actualiza el perfil del contacto.
        """
        try:
            contact = Contact.objects.get(phone=phone)
            text_lower = text.lower()
            score_delta = 0
            new_tags = set(contact.tags)

            # 1. Detección de Intención Simple
            intent = None
            if tool_used == "show_catalog":
                intent = "browsing"
                score_delta += 2
                new_tags.add("interes_catalogo")
            elif tool_used == "recommend_products":
                intent = "searching"
                score_delta += 5
                new_tags.add("busqueda_activa")

            # Keywords analysis
            for key, words in CRMService.INTENT_KEYWORDS.items():
                if any(w in text_lower for w in words):
                    if key == "compra":
                        score_delta += 10
                        new_tags.add("intencion_compra")
                        intent = "buying"
                    elif key == "humano":
                        new_tags.add("pide_humano")
                        intent = "support"

            # 2. Actualizar Score
            contact.lead_score += score_delta
            if intent:
                contact.last_intent = intent

            # 3. Auto-Categorización por Score
            if contact.lead_score >= CRMService.TAG_RULES["vip"]:
                new_tags.add("vip")
            elif contact.lead_score >= CRMService.TAG_RULES["caliente"]:
                new_tags.add("lead_caliente")

            # 4. Persistencia
            contact.tags = list(new_tags)
            contact.save()

            log.debug(
                f"📊 CRM Updated for {phone}: Score={contact.lead_score} (+{score_delta}), Tags={list(new_tags)}"
            )

        except Contact.DoesNotExist:
            log.warning(f"CRM: Contacto {phone} no existe.")
        except Exception as e:
            log.error(f"CRM Error: {e}")

    @staticmethod
    def add_tag(phone: str, tag: str):
        try:
            contact = Contact.objects.get(phone=phone)
            tags = set(contact.tags)
            tags.add(tag)
            contact.tags = list(tags)
            contact.save()
        except Exception as e:
            log.error(f"Error adding tag: {e}")
