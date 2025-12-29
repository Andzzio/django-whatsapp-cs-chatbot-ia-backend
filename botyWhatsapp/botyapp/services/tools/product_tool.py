from google.genai import types
from .base import BaseTool
from botyapp.services.catalog import search_and_send_products
from logger import log


class RecommendProductsTool(BaseTool):
    """
    Herramienta para recomendar productos específicos.
    """

    @property
    def name(self) -> str:
        return "recommend_products"

    @property
    def description(self) -> str:
        return (
            "Usa esta función cuando el usuario busque un tipo de producto específico "
            "(ej: 'pantalones', 'vestidos', 'ofertas'). Filtra y muestra productos relevantes con imagen."
        )

    @property
    def parameters_schema(self) -> types.Schema:
        return types.Schema(
            type=types.Type.OBJECT,
            properties={
                "search_term": types.Schema(
                    type=types.Type.STRING,
                    description="Término de búsqueda o categoría (ej: 'pantalón', 'falda', 'azul')",
                )
            },
            required=["search_term"],
        )

    def execute(self, sender_id, **kwargs):
        term = kwargs.get("search_term", "ropa")
        log.debug(
            f"🔍 Ejecutando RecommendProductsTool para {sender_id}, termino: {term}"
        )
        search_and_send_products(sender_id, term)
        return {"status": "executed", "action": "recommend_products", "term": term}
