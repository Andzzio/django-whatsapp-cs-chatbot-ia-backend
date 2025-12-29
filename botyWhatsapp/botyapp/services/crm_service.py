from botyapp.models import Contact
from logger import log


class CRMService:
    """
    Servicio de Inteligencia de Negocio (CRM).
    Analiza interacciones para perfilar usuarios automáticamente.
    """

    TAG_RULES = {
        "vip": 50,  # Score > 50 -> VIP
        "caliente": 20,  # Score > 20 -> Lead Caliente
    }

    @staticmethod
    def analyze_interaction(
        phone: str, text: str, tool_used: str = None, intent_label: str = None
    ):
        """
        Analiza un mensaje entrante y actualiza el perfil del contacto.
        """
        try:
            contact = Contact.objects.get(phone=phone)
            score_delta = 0
            new_tags = set(contact.tags)

            # 1. Detección de Intención (Unified Logic)
            final_intent = None

            # Map tools/intents to business logic
            if tool_used == "show_catalog" or intent_label == "show_catalog":
                final_intent = "browsing"
                score_delta += 2
                new_tags.add("interes_catalogo")
            elif tool_used == "recommend_products":
                final_intent = "searching"
                score_delta += 5
                new_tags.add("busqueda_activa")
            elif tool_used == "contact_support" or intent_label == "contact_support":
                final_intent = "support"
                score_delta += 10  # High value lead wanting human
                new_tags.add("pide_humano")
            elif intent_label == "buying":  # Future expansion
                final_intent = "buying"
                score_delta += 10
                new_tags.add("intencion_compra")

            # Update loop removed - replaced by classifier output logic above ^

            # 2. Actualizar Score
            contact.lead_score += score_delta
            if final_intent:
                contact.last_intent = final_intent

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
