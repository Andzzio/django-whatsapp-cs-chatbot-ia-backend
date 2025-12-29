"""
Django signals para auto-indexar imágenes de productos.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver
from botyapp.models import ProductEmbedding
import threading
from logger import log


@receiver(post_save, sender=ProductEmbedding)
def auto_index_product_image(sender, instance, created, **kwargs):
    """
    Auto-indexa imagen del producto cuando se crea o actualiza.
    Se ejecuta en background thread para no bloquear.
    """
    # Solo indexar si:
    # 1. Es nuevo producto (created=True), O
    # 2. No tiene embedding de imagen todavía
    should_index = created or not instance.image_embedding_vector

    if should_index and instance.image_url:
        log.info(f"Queueing image indexing for {instance.product_name}")

        # Lazy import para evitar circular dependency
        from botyapp.services.intelligence.product_image_matcher import product_matcher

        # Ejecutar en background thread
        threading.Thread(
            target=product_matcher.index_product_image,
            args=(instance,),
            daemon=True,
            name=f"IndexProduct-{instance.retailer_id}",
        ).start()
