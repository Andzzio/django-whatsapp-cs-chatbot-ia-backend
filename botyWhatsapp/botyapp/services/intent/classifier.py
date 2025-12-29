from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from logger import log


@dataclass
class IntentResult:
    action: str
    confidence: float
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
        # En este caso, el LLMEngine ya actúa como un agente completo que ejecuta herramientas.
        # Para el propósito de "Clasificación Pura" antes de acción, podríamos pedirle JSON.
        # PERO, dado que LLMEngine YA procesa y ejecuta, la estrategia "Generative"
        # aquí es delegar la ejecución completa al engine si la determinista falla.
        # Sin embargo, para cumplir con el patrón "Classifier returns Intent",
        # marcaremos la acción como "USE_LLM_ENGINE".
        return IntentResult(
            action="USE_LLM_ENGINE", confidence=0.9, source="probabilistic"
        )


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
