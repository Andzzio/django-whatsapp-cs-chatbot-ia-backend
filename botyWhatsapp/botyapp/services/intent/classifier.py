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
        import difflib

        text_lower = text.lower().strip()

        # 1. Exact or Partial Match logic optimization
        for action, keywords in self.KEYWORDS.items():
            for k in keywords:
                # A. Contención directa (Rápida)
                if k in text_lower:
                    return IntentResult(
                        action=action, confidence=1.0, source="deterministic_exact"
                    )

                # B. Fuzzy Match (Tolerancia a typos: 'katalogo', 'catálogo')
                # Solo si la palabra es suficientemente larga para evitar falsos positivos
                if len(k) > 4:
                    # Buscamos similitud palabra por palabra en el input
                    input_words = text_lower.split()
                    matches = difflib.get_close_matches(k, input_words, n=1, cutoff=0.8)
                    if matches:
                        log.debug(f"🔍 Fuzzy Match: '{matches[0]}' ~= '{k}'")
                        return IntentResult(
                            action=action, confidence=0.85, source="deterministic_fuzzy"
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
                model="models/gemini-flash-lite-latest",
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )

            if response.text:
                data = json.loads(response.text)
                intent = data.get("intent", "unknown")
                entities = data.get("entities", [])

                # Mapear intents a acciones internas
                action = "USE_LLM_ENGINE"

                # REGLA DE NEGOCIO: Si la intención es ver productos o preguntar precios,
                # la acción CORRECTA es mostrar el catálogo. No dejar al LLM divagar.
                if intent in ["ordering", "product_inquiry", "product_search"]:
                    action = "show_catalog"

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
