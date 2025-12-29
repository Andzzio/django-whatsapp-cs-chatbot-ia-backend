"""
Rastreador de contexto conversacional.
Analiza qué se ha discutido y detecta señales de compra.
"""

from typing import Dict, List, Optional, Any
import re
from botyapp.models import Contact, Message
from logger import log


class ContextTracker:
    """Analiza contexto de conversación para detectar señales de compra"""

    # Patrones de señales de compra
    BUYING_SIGNALS = {
        "asked_price": [
            r"\bcuanto\b",
            r"\bprecio\b",
            r"\bcuesta\b",
            r"\bcosto\b",
            r"\bvalor\b",
        ],
        "asked_sizes": [
            r"\btalla\b",
            r"\bsize\b",
            r"\bmedida\b",
            r"\btam[aã]o\b",
            r"\bque talla\b",
        ],
        "asked_availability": [
            r"\bhay\b",
            r"\btienes\b",
            r"\bdisponible\b",
            r"\bstock\b",
            r"\bquedan\b",
        ],
        "asked_shipping": [
            r"\benvio\b",
            r"\bentrega\b",
            r"\bdelivery\b",
            r"\bcuando llega\b",
        ],
        "confirmed_interest": [
            r"\bsi\b.*\bgusta\b",
            r"\bme encanta\b",
            r"\bperfecto\b",
            r"\bdale\b",
            r"\bok\b",
            r"\bbueno\b",
        ],
    }

    # Patrones de objeciones
    OBJECTION_PATTERNS = {
        "price": [
            r"\bcaro\b",
            r"\bmucho\b.*\bprecio\b",
            r"\bno puedo\b",
            r"\bmi presupuesto\b",
        ],
        "doubt": [
            r"\bno se\b",
            r"\bdudas?\b",
            r"\bno estoy segur[oa]\b",
        ],
        "delay": [
            r"\blo pienso\b",
            r"\bdespu[eé]s\b",
            r"\bm[aá]s tarde\b",
            r"\bme avisa\b",
        ],
    }

    def __init__(self, contact: Contact, recent_messages: int = 10):
        """
        Args:
            contact: Cliente
            recent_messages: Número de mensajes recientes a analizar
        """
        self.contact = contact
        self.recent_messages = list(
            contact.messages.order_by("-timestamp")[:recent_messages]
        )

    def analyze_conversation(self) -> Dict[str, Any]:
        """
        Analiza mensajes recientes y extrae contexto.

        Returns:
            Dict con señales detectadas:
            {
                'asked_price': bool,
                'asked_sizes': bool,
                'asked_availability': bool,
                'asked_shipping': bool,
                'confirmed_interest': bool,
                'has_objection': bool,
                'objection_type': str | None,
                'message_count': int,
                'engagement_signals': int,  # Suma de señales positivas
            }
        """
        context = {
            "asked_price": False,
            "asked_sizes": False,
            "asked_availability": False,
            "asked_shipping": False,
            "confirmed_interest": False,
            "has_objection": False,
            "objection_type": None,
            "message_count": len(self.recent_messages),
        }

        # Analizar mensajes del usuario (no bot)
        user_messages = [
            msg.text.lower() for msg in self.recent_messages if not msg.is_bot
        ]

        full_text = " ".join(user_messages)

        # Detectar señales de compra
        for signal_name, patterns in self.BUYING_SIGNALS.items():
            for pattern in patterns:
                if re.search(pattern, full_text, re.IGNORECASE):
                    context[signal_name] = True
                    break

        # Detectar objeciones
        for objection_type, patterns in self.OBJECTION_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, full_text, re.IGNORECASE):
                    context["has_objection"] = True
                    context["objection_type"] = objection_type
                    break
            if context["has_objection"]:
                break

        # Calcular engagement (señales positivas)
        engagement_signals = sum(
            [
                context["asked_price"],
                context["asked_sizes"],
                context["asked_availability"],
                context["asked_shipping"],
                context["confirmed_interest"],
            ]
        )
        context["engagement_signals"] = engagement_signals

        return context

    def detect_intent_from_message(self, message_text: str) -> str:
        """
        Detecta intención de un mensaje específico.

        Returns:
            'buy', 'browse', 'question', 'objection', 'unknown'
        """
        text_lower = message_text.lower()

        # Intención de compra
        buy_keywords = [
            "comprar",
            "llevar",
            "quiero",
            "aparto",
            "pedir",
            "confirmo",
            "si lo quiero",
        ]
        if any(kw in text_lower for kw in buy_keywords):
            return "buy"

        # Navegar/explorar
        browse_keywords = [
            "ver",
            "mostrar",
            "catalogo",
            "productos",
            "fotos",
            "que tienes",
            "opciones",
        ]
        if any(kw in text_lower for kw in browse_keywords):
            return "browse"

        # Objeción
        if self._has_objection(text_lower):
            return "objection"

        # Pregunta (contiene ?)
        if "?" in message_text:
            return "question"

        return "unknown"

    def _has_objection(self, text: str) -> bool:
        """Detecta si texto contiene objeción"""
        for patterns in self.OBJECTION_PATTERNS.values():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    return True
        return False

    def extract_product_mentions(self) -> List[str]:
        """
        Extrae menciones de productos en conversación.
        Útil para saber qué le interesa al cliente.

        Returns:
            Lista de palabras clave mencionadas
        """
        user_messages = [
            msg.text.lower() for msg in self.recent_messages if not msg.is_bot
        ]

        # Palabras clave comunes de productos (expandir según catálogo)
        product_keywords = [
            "palazzo",
            "vestido",
            "blusa",
            "pantalon",
            "falda",
            "short",
            "conjunto",
            "top",
            "crop",
            "jeans",
        ]

        mentioned = []
        full_text = " ".join(user_messages)

        for keyword in product_keywords:
            if keyword in full_text:
                mentioned.append(keyword)

        return mentioned

    def should_escalate_to_human(self) -> bool:
        """
        Determina si debe derivar a humano.

        Criterios:
        - Muchas objeciones sin resolver
        - Cliente confundido (muchas preguntas repetidas)
        - Cliente solicita explícitamente hablar con persona
        """
        context = self.analyze_conversation()

        # Solicitud explícita
        user_messages = [
            msg.text.lower() for msg in self.recent_messages if not msg.is_bot
        ]
        full_text = " ".join(user_messages)

        human_requests = [
            r"\bhumano\b",
            r"\bpersona\b",
            r"\bagente\b",
            r"\bgerente\b",
        ]

        for pattern in human_requests:
            if re.search(pattern, full_text, re.IGNORECASE):
                return True

        # Muchas objeciones
        # Esto lo determinaríamos desde StateManager.objection_count
        # pero aquí solo analizamos texto

        return False
