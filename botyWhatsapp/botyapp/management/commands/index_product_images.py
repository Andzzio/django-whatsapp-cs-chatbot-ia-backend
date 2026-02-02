"""
Django management command para indexar embeddings de imágenes.
Ejecutar UNA VEZ en local para indexar catálogo existente.

Uso:
    python manage.py index_product_images
"""

from django.core.management.base import BaseCommand
from botyapp.models import ProductEmbedding
from botyapp.services.intelligence.product_image_matcher import product_matcher


class Command(BaseCommand):
    help = "Indexa embeddings de imágenes de todos los productos disponibles"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-indexar incluso si ya tienen embedding",
        )

    def handle(self, *args, **options):
        force = options.get("force", False)

        # Filtrar productos
        filters = {"is_available": True, "image_url__isnull": False}

        if not force:
            filters["image_embedding_vector__isnull"] = True

        products = ProductEmbedding.objects.filter(**filters)

        total = products.count()

        if total == 0:
            self.stdout.write(self.style.WARNING("No hay productos para indexar"))
            return

        self.stdout.write(self.style.SUCCESS(f"Indexando {total} productos...\n"))

        success_count = 0
        error_count = 0

        for i, product in enumerate(products, 1):
            self.stdout.write(f"[{i}/{total}] {product.product_name}... ", ending="")

            try:
                product_matcher.index_product_image(product)
                success_count += 1
                self.stdout.write(self.style.SUCCESS("✓"))
            except Exception as e:
                error_count += 1
                self.stdout.write(self.style.ERROR(f"✗ ({str(e)[:50]})"))

        self.stdout.write("\n")
        self.stdout.write(
            self.style.SUCCESS(
                f"✅ Completado: {success_count} exitosos, {error_count} errores"
            )
        )

        if error_count > 0:
            self.stdout.write(
                self.style.WARNING(
                    f"\n⚠️  {error_count} productos no se pudieron indexar. "
                    f"Revisa que las URLs de imágenes sean válidas."
                )
            )
