"""
Comando Django para sincronizar y vectorizar productos del catálogo.
Uso: python manage.py generate_embeddings
"""

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from botyapp.models import ProductEmbedding
from botyapp.services.catalog import sync_catalog_products
from botyapp.services.intelligence.semantic_search import semantic_search


class Command(BaseCommand):
    help = "Sincroniza productos de Meta Commerce y genera embeddings para búsqueda semántica"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Forzar regeneración de embeddings existentes",
        )

    def handle(self, *args, **options):
        force = options["force"]

        self.stdout.write(
            self.style.SUCCESS("🚀 Iniciando vectorización del catálogo...")
        )

        # 1. Sincronizar productos desde Meta Commerce
        self.stdout.write("📥 Sincronizando productos de Meta Commerce...")
        try:
            products_dict = sync_catalog_products(settings.CATALOG_ID)
            if not products_dict:
                self.stdout.write(
                    self.style.ERROR("❌ No se obtuvieron productos del catálogo")
                )
                return

            self.stdout.write(
                self.style.SUCCESS(f"✅ {len(products_dict)} productos sincronizados")
            )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error sincronizando catálogo: {e}"))
            return

        # 2. Vectorizar cada producto
        vectorized_count = 0
        skipped_count = 0
        error_count = 0

        self.stdout.write("\n🧠 Generando embeddings...")

        for retailer_id, product_data in products_dict.items():
            try:
                # Verificar si ya existe
                existing = ProductEmbedding.objects.filter(
                    retailer_id=retailer_id
                ).first()

                if existing and existing.embedding_vector and not force:
                    skipped_count += 1
                    if skipped_count % 10 == 0:
                        self.stdout.write(f"   ⏭️  Saltados: {skipped_count}")
                    continue

                # Preparar texto para embedding
                name = product_data.get("name", "")
                description = product_data.get("description", "")
                category = product_data.get("category", "")

                search_text = f"{name} {description} {category}".strip().lower()

                # Generar embedding
                embedding = semantic_search.generate_embedding(search_text)

                if not embedding:
                    self.stdout.write(
                        self.style.WARNING(
                            f"   ⚠️  No se pudo generar embedding para: {name[:50]}"
                        )
                    )
                    error_count += 1
                    continue

                # Parsear precio (manejo robusto de S/XX,XX)
                price_value = None
                price_raw = product_data.get("price")

                if isinstance(price_raw, dict):
                    # Formato dict: {'amount': '40.00', 'currency': 'PEN'}
                    price_value = float(price_raw.get("amount", 0))
                elif isinstance(price_raw, str):
                    # Formato string: "S/40,00" -> 40.00
                    try:
                        price_clean = (
                            price_raw.replace("S/", "").replace(",", ".").strip()
                        )
                        price_value = float(price_clean)
                    except Exception:
                        price_value = None
                elif isinstance(price_raw, (int, float)):
                    price_value = float(price_raw)

                # Crear o actualizar en DB
                ProductEmbedding.objects.update_or_create(
                    retailer_id=retailer_id,
                    defaults={
                        "product_name": name,
                        "description": description,
                        "price": price_value,
                        "category": category,
                        "image_url": product_data.get(
                            "image_url"
                        ),  # NUEVO: Guardar URL de imagen
                        "embedding_vector": embedding,
                        "search_text": search_text,
                        "is_available": True,
                        "stock_quantity": 10,  # Default, ajustar si tienes API de stock
                        "last_synced": timezone.now(),
                    },
                )

                vectorized_count += 1

                if vectorized_count % 5 == 0:
                    self.stdout.write(f"   ✨ Vectorizados: {vectorized_count}")

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"   ❌ Error procesando {retailer_id}: {e}")
                )
                error_count += 1
                continue

        # 3. Resumen
        self.stdout.write("\n" + self.style.SUCCESS("=" * 60))
        self.stdout.write(self.style.SUCCESS(f"✅ Vectorizados: {vectorized_count}"))
        self.stdout.write(f"⏭️  Saltados: {skipped_count}")
        if error_count:
            self.stdout.write(self.style.WARNING(f"⚠️  Errores: {error_count}"))

        total_in_db = ProductEmbedding.objects.count()
        self.stdout.write(f"\n📊 Total en base de datos: {total_in_db}")
        self.stdout.write(self.style.SUCCESS("=" * 60))

        # 4. Test rápido de búsqueda
        self.stdout.write("\n🧪 Probando búsqueda semántica...")
        test_queries = ["palazzo", "comodo", "vestido fiesta"]

        for query in test_queries:
            results = semantic_search.search_products(
                query, top_k=3, min_similarity=0.4
            )
            self.stdout.write(f'\n   🔍 "{query}":')
            if results:
                for product, score in results:
                    self.stdout.write(
                        f"      • {product.product_name} (similitud: {score:.2f})"
                    )
            else:
                self.stdout.write("      (sin resultados)")

        self.stdout.write("\n" + self.style.SUCCESS("🎉 ¡Vectorización completa!"))
