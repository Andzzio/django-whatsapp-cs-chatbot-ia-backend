"""
Identificación de productos por imagen usando embeddings multimodales.
Permite que el bot identifique exactamente qué producto es a partir de screenshots.
"""

from dataclasses import dataclass
from typing import List, TYPE_CHECKING
import numpy as np
from google import genai
from google.genai import types
from django.conf import settings
import requests
import threading
from logger import log

if TYPE_CHECKING:
    from botyapp.models import ProductEmbedding


@dataclass
class ProductMatch:
    """Resultado de matching de imagen"""

    product: "ProductEmbedding"
    confidence: float  # 0-1
    is_certain: bool  # True si >0.85


class ProductImageMatcher:
    """
    Identifica productos a partir de screenshots (TikTok, Instagram, etc).
    Usa embeddings multimodales de Gemini para comparación visual.

    Casos de uso:
    - Cliente manda screenshot de TikTok → Identifica producto exacto
    - Cliente manda foto del producto → Identifica cuál es
    - Precisión esperada: 90%+ para screenshots limpios
    """

    def __init__(self):
        self.client = genai.Client(api_key=settings.IA_TOKEN)
        self.product_image_embeddings = {}  # {retailer_id: np.array}
        self._lock = threading.Lock()
        self._load_embeddings_from_db()

    def _load_embeddings_from_db(self):
        """Carga embeddings pre-calculados al iniciar"""
        from botyapp.models import ProductEmbedding

        products = ProductEmbedding.objects.filter(
            is_available=True, image_embedding_vector__isnull=False
        ).only("retailer_id", "image_embedding_vector")

        for p in products:
            if p.image_embedding_vector:
                self.product_image_embeddings[p.retailer_id] = np.array(
                    p.image_embedding_vector
                )

        log.info(
            f"✅ Loaded {len(self.product_image_embeddings)} product image embeddings"
        )

    def identify_product(self, screenshot_bytes: bytes) -> List[ProductMatch]:
        """
        Identifica qué producto aparece en screenshot.

        Args:
            screenshot_bytes: Bytes de la imagen

        Returns:
            Top 3 matches ordenados por confianza (si >70%)
        """
        # Embedding de la imagen query
        query_embedding = self._get_image_embedding(screenshot_bytes)

        with self._lock:
            retailer_ids = list(self.product_image_embeddings.keys())

            if not retailer_ids:
                log.warning("No product embeddings available")
                return []

            # Vectorización: matriz de todos los embeddings
            embeddings_matrix = np.array(
                [self.product_image_embeddings[rid] for rid in retailer_ids]
            )

        # Cosine similarity batch (súper rápido con NumPy)
        similarities = np.dot(embeddings_matrix, query_embedding) / (
            np.linalg.norm(embeddings_matrix, axis=1) * np.linalg.norm(query_embedding)
        )

        # Top 3 candidatos
        top_3_indices = np.argsort(similarities)[-3:][::-1]

        matches = []
        from botyapp.models import ProductEmbedding

        for idx in top_3_indices:
            similarity = float(similarities[idx])

            # Solo considerar si similitud >0.70
            if similarity >= 0.70:
                try:
                    product = ProductEmbedding.objects.get(
                        retailer_id=retailer_ids[idx]
                    )
                    matches.append(
                        ProductMatch(
                            product=product,
                            confidence=similarity,
                            is_certain=(similarity >= 0.85),  # >85% = casi seguro
                        )
                    )
                except ProductEmbedding.DoesNotExist:
                    continue

        return matches

    def _get_image_embedding(self, image_bytes: bytes) -> np.ndarray:
        """
        Genera embedding de imagen con Gemini.
        Usa modelo multimodal-embedding-001 especializado para imágenes.
        """
        result = self.client.models.embed_content(
            model="models/multimodal-embedding-001",
            content=types.Part.from_bytes(image_bytes, "image/jpeg"),
        )

        return np.array(result["embedding"])

    def index_product_image(self, product: "ProductEmbedding"):
        """
        Genera y guarda embedding de imagen del producto.
        Se ejecuta automáticamente vía signal cuando se agrega/actualiza producto.

        Args:
            product: Instancia de ProductEmbedding
        """
        if not product.image_url:
            log.warning(f"Product {product.retailer_id} has no image_url")
            return

        try:
            # Descargar imagen del producto
            log.info(f"Downloading image for {product.product_name}...")
            img_response = requests.get(product.image_url, timeout=15)
            img_response.raise_for_status()
            img_bytes = img_response.content

            # Generar embedding
            log.info(f"Generating embedding for {product.product_name}...")
            embedding = self._get_image_embedding(img_bytes)

            # Guardar en DB (JSON field)
            product.image_embedding_vector = embedding.tolist()
            product.save(update_fields=["image_embedding_vector"])

            # Actualizar caché en memoria
            with self._lock:
                self.product_image_embeddings[product.retailer_id] = embedding

            log.info(f"✅ Indexed image for {product.product_name}")

        except requests.RequestException as e:
            log.error(f"Error downloading image for {product.retailer_id}: {e}")
        except Exception as e:
            log.error(f"Error indexing image for {product.retailer_id}: {e}")

    def reindex_all_products(self):
        """
        Re-indexa todos los productos.
        Útil para migración inicial o cuando cambia el modelo de embeddings.
        """
        from botyapp.models import ProductEmbedding

        products = ProductEmbedding.objects.filter(
            is_available=True, image_url__isnull=False
        )

        total = products.count()
        log.info(f"Starting reindex of {total} products...")

        for i, product in enumerate(products, 1):
            log.info(f"[{i}/{total}] Indexing {product.product_name}")
            self.index_product_image(product)

        log.info(f"✅ Reindexed {total} products")


# Singleton global
product_matcher = ProductImageMatcher()
