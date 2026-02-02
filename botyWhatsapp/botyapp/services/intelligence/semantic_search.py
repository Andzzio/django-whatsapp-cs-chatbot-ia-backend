"""
Servicio de búsqueda semántica de productos usando embeddings.
Permite encontrar productos por similitud conceptual en lugar de keywords exactos.
"""

import numpy as np
from typing import List, Tuple
from django.core.cache import cache
from django.conf import settings

import google.generativeai as genai

from botyapp.models import ProductEmbedding
from logger import log


class SemanticSearch:
    """Motor de búsqueda semántica basado en embeddings"""

    EMBEDDING_MODEL = "models/embedding-001"  # Gemini embedding model
    CACHE_TTL = 3600  # 1 hora

    def __init__(self):
        genai.configure(api_key=settings.IA_TOKEN)
        self.model = genai.GenerativeModel("gemini-pro")

    def generate_embedding(self, text: str) -> List[float]:
        """
        Genera embedding de un texto usando Gemini.

        Args:
            text: Texto para vectorizar

        Returns:
            Vector embedding (lista de floats)
        """
        try:
            result = genai.embed_content(
                model=self.EMBEDDING_MODEL, content=text, task_type="retrieval_document"
            )
            return result["embedding"]
        except Exception as e:
            log.error(f"Error generando embedding: {e}")
            return []

    def cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """
        Calcula similitud de coseno entre dos vectores.

        Returns:
            Similitud entre 0 y 1 (1 = idénticos)
        """
        try:
            vec1_np = np.array(vec1)
            vec2_np = np.array(vec2)

            dot_product = np.dot(vec1_np, vec2_np)
            norm1 = np.linalg.norm(vec1_np)
            norm2 = np.linalg.norm(vec2_np)

            if norm1 == 0 or norm2 == 0:
                return 0.0

            return float(dot_product / (norm1 * norm2))
        except Exception as e:
            log.error(f"Error calculando similitud: {e}")
            return 0.0

    def search_products(
        self,
        query: str,
        top_k: int = 5,
        min_similarity: float = 0.5,
        available_only: bool = True,
    ) -> List[Tuple[ProductEmbedding, float]]:
        """
        Busca productos por similitud semántica.

        Args:
            query: Texto de búsqueda (ej: "pantalones anchos comodos")
            top_k: Número de resultados a retornar
            min_similarity: Similitud mínima (0-1)
            available_only: Solo productos disponibles

        Returns:
            Lista de tuplas (ProductEmbedding, similarity_score)
        """
        # Generar embedding de la query
        query_embedding = self.generate_embedding(query)
        if not query_embedding:
            log.warning("No se pudo generar embedding de la query")
            return []

        # Obtener productos con embeddings
        queryset = ProductEmbedding.objects.all()
        if available_only:
            queryset = queryset.filter(is_available=True)

        # Calcular similitudes
        results = []
        for product in queryset:
            if not product.embedding_vector:
                continue

            similarity = self.cosine_similarity(
                query_embedding, product.embedding_vector
            )

            if similarity >= min_similarity:
                results.append((product, similarity))

        # Ordenar por similitud descendente
        results.sort(key=lambda x: x[1], reverse=True)

        # Retornar top_k
        top_results = results[:top_k]

        log.debug(
            f"Búsqueda '{query}': {len(top_results)} productos encontrados "
            f"(similitud mín: {min_similarity})"
        )

        return top_results

    def search_by_category(
        self, query: str, category: str, top_k: int = 5
    ) -> List[Tuple[ProductEmbedding, float]]:
        """
        Búsqueda semántica filtrada por categoría.
        """
        query_embedding = self.generate_embedding(query)
        if not query_embedding:
            return []

        products = ProductEmbedding.objects.filter(
            category__icontains=category, is_available=True
        )

        results = []
        for product in products:
            if not product.embedding_vector:
                continue

            similarity = self.cosine_similarity(
                query_embedding, product.embedding_vector
            )
            results.append((product, similarity))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def find_similar_products(
        self, product_id: str, top_k: int = 3
    ) -> List[Tuple[ProductEmbedding, float]]:
        """
        Encuentra productos similares a uno dado (upselling/cross-selling).
        """
        try:
            reference_product = ProductEmbedding.objects.get(retailer_id=product_id)
        except ProductEmbedding.DoesNotExist:
            log.warning(f"Producto {product_id} no encontrado para similitudes")
            return []

        if not reference_product.embedding_vector:
            return []

        # Buscar productos similares (excluyendo el mismo)
        results = []
        for product in ProductEmbedding.objects.filter(is_available=True).exclude(
            retailer_id=product_id
        ):
            if not product.embedding_vector:
                continue

            similarity = self.cosine_similarity(
                reference_product.embedding_vector, product.embedding_vector
            )
            results.append((product, similarity))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def get_search_suggestions(self, partial_query: str) -> List[str]:
        """
        Genera sugerencias de búsqueda basadas en productos existentes.
        """
        cache_key = f"search_suggestions_{partial_query.lower()}"
        cached = cache.get(cache_key)
        if cached:
            return cached

        # Buscar productos que coincidan parcialmente
        matching_products = ProductEmbedding.objects.filter(
            search_text__icontains=partial_query.lower(), is_available=True
        )[:5]

        suggestions = []
        for product in matching_products:
            suggestions.append(product.product_name)
            if product.category:
                suggestions.append(f"{product.category}")

        # Deduplicar y limitar
        unique_suggestions = list(set(suggestions))[:5]

        cache.set(cache_key, unique_suggestions, self.CACHE_TTL)
        return unique_suggestions


# Instancia global
semantic_search = SemanticSearch()
