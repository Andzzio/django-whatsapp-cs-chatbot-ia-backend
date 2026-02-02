from django.core.cache import cache
from logger import log
from typing import List, Dict, Any, Optional


class SessionManager:
    """
    Gestor de sesiones centralizado.
    Maneja el estado del usuario, historial de conversación y metadatos (CRM).
    Utiliza Django Cache (Redis/LocMem) como backend de persistencia.
    """

    CACHE_TIMEOUT = 60 * 60 * 24 * 30  # 30 días

    def __init__(self, phone_number: str):
        self.phone_number = phone_number
        self.key = f"Client_{phone_number}"

    def _get_data(self) -> Dict[str, Any]:
        return cache.get(self.key, {}) or {}

    def _save_data(self, data: Dict[str, Any]):
        # Aseguramos que el teléfono siempre esté
        data["phone_number"] = self.phone_number
        cache.set(self.key, data, timeout=self.CACHE_TIMEOUT)

    def get_name(self) -> str:
        data = self._get_data()
        return data.get("client_name", "Cliente")

    def set_name(self, name: str):
        data = self._get_data()
        data["client_name"] = name
        self._save_data(data)

    def get_history(self) -> List[Dict]:
        data = self._get_data()
        history = data.get("context", [])

        # Validación y limpieza de estructura (Sanity check)
        if not isinstance(history, list):
            return []

        clean_history = []
        for turn in history:
            if not isinstance(turn, dict) or "parts" not in turn:
                continue

            # Limpieza profunda de partes corruptas o logs de sistema
            clean_parts = []
            for part in turn["parts"]:
                if isinstance(part, dict) and "text" in part:
                    # Filtro de seguridad: No devolver logs de sistema al contexto si se colaron
                    if not part["text"].startswith("[SISTEMA:"):
                        clean_parts.append(part)
                else:
                    # Partes binarias/otras se mantienen
                    clean_parts.append(part)

            turn["parts"] = clean_parts
            clean_history.append(turn)

        return clean_history

    def update_history(self, new_history: List[Dict]):
        data = self._get_data()
        data["context"] = new_history
        self._save_data(data)
        log.debug(
            f"🧠 Historia actualizada para {self.phone_number}: {len(new_history)} turnos."
        )

    def get_context_image_id(self) -> Optional[str]:
        """Retorna el ID de la última imagen procesada en contexto"""
        return self._get_data().get("image_id")

    def set_context_image_id(self, image_id: str):
        data = self._get_data()
        data["image_id"] = image_id
        self._save_data(data)

    def clear_session(self):
        cache.delete(self.key)
