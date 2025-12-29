from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from logger import log


@dataclass
class IntentResult:
    action: str
    confidence: float
    intent: Optional[str] = None  # Semantic Intent (e.g. 'ordering', 'inquiry')
    entities: Optional[List[str]] = None  # Extracted Entities (e.g. ['jeans', 'red'])
    payload: Optional[Dict[str, Any]] = None
    source: str = "unknown"  # 'deterministic', 'probabilistic'


class IntentStrategy(ABC):
    @abstractmethod
    def detect(self, text: str) -> Optional[IntentResult]:
        pass


class DeterministicStrategy(IntentStrategy):
    """
    Nivel 1: Detección rápida por patrones rígidos.
    Alta velocidad, costo cero.
    """

    KEYWORDS = {
        "show_catalog": [
            "catalogo",
            "catálogo",
            "ver productos",
            "modelos",
            "fotos",
            "precios",
            "informacion",
            "ver ropa",
            "muestrame productos",
        ],
        "contact_support": [
            "humano",
            "asesor",
            "persona",
            "hablar con alguien",
            "soporte",
            "ayuda",
        ],
    }

    def detect(self, text: str) -> Optional[IntentResult]:
        text_lower = text.lower().strip()

        # 1. Exact or Partial Match logic optimization
        # Iteramos sobre los intents conocidos
        for action, keywords in self.KEYWORDS.items():
            if any(k in text_lower for k in keywords):
                return IntentResult(
                    action=action, confidence=1.0, source="deterministic"
                )
        return None


class GenerativeStrategy(IntentStrategy):
    """
    Nivel 2: Detección semántica por IA (Google Gemini).
    Alta inteligencia, costo de inferencia.
    """

    def detect(self, text: str) -> Optional[IntentResult]:
        """
        Usa Gemini Flash Lite para clasificar intención y extraer entidades en JSON.
        """
        try:
            from google import genai
            from django.conf import settings
            import json

            client = genai.Client(api_key=settings.IA_TOKEN)

            prompt = (
                "Clasifica la intención del usuario en JSON estricto. "
                "Intents posibles: ordering, product_inquiry, support, unknown. "
                "Entities: lista de productos mencionados. "
                f"Mensaje: '{text}'"
            )

            response = client.models.generate_content(
                model="models/gemini-flash-latest",
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )

            if response.text:
                data = json.loads(response.text)
                intent = data.get("intent", "unknown")
                entities = data.get("entities", [])

                # Mapear intents a acciones internas
                action = "USE_LLM_ENGINE"
                if intent in ["ordering", "product_inquiry"]:
                    # El MessageHandler interceptará esto para mostrar catálogo
                    pass

                return IntentResult(
                    action=action,
                    confidence=0.9,
                    source="generative_ai",
                    intent=intent,
                    entities=entities,
                )

        except Exception as e:
            log.error(f"Generative Intent Error: {e}")

        return IntentResult(action="USE_LLM_ENGINE", confidence=0.0, source="fallback")


class IntentClassifier:
    """
    Fachada del Sistema de Intenciones Enterprise.
    Aplica estrategias en cascada (Chain of Responsibility).
    """

    def __init__(self):
        self.strategies: List[IntentStrategy] = [
            DeterministicStrategy(),
            GenerativeStrategy(),
        ]

    def classify(self, text: str) -> IntentResult:
        for strategy in self.strategies:
            result = strategy.detect(text)
            if result:
                log.debug(f"🧠 Intent Detected via {result.source}: {result.action}")
                return result

        # Fallback default
        return IntentResult(action="USE_LLM_ENGINE", confidence=0.0, source="fallback")


# Global Instance
intent_classifier = IntentClassifier()
