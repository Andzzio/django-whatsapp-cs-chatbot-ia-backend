from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from .models import ProductEmbedding
from logger import log
import json


@csrf_exempt
def update_stock(request):
    """
    Endpoint para actualizar stock por tallas de un producto
    POST /api/products/stock/
    Body: {
        "retailer_id": "ABC123",
        "stock_s": 10,
        "stock_m": 15,
        "stock_l": 8,
        "stock_xl": 5,
        "is_available": true
    }
    """
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method == "POST":
        try:
            data = json.loads(request.body)
            retailer_id = data.get("retailer_id")

            if not retailer_id:
                return JsonResponse({"error": "retailer_id is required"}, status=400)

            # Buscar producto
            try:
                product = ProductEmbedding.objects.get(retailer_id=retailer_id)
            except ProductEmbedding.DoesNotExist:
                return JsonResponse(
                    {"error": f"Product {retailer_id} not found"}, status=404
                )

            # Actualizar stocks por talla
            if "stock_s" in data:
                product.stock_s = int(data["stock_s"])
            if "stock_m" in data:
                product.stock_m = int(data["stock_m"])
            if "stock_l" in data:
                product.stock_l = int(data["stock_l"])
            if "stock_xl" in data:
                product.stock_xl = int(data["stock_xl"])

            # Actualizar disponibilidad
            if "is_available" in data:
                product.is_available = bool(data["is_available"])
            else:
                # Auto-ajustar disponibilidad basado en stock total
                if product.total_stock == 0:
                    product.is_available = False
                elif product.total_stock > 0 and not product.is_available:
                    product.is_available = True

            product.save()

            log.debug(
                f"Stock updated for {retailer_id}: S={product.stock_s}, M={product.stock_m}, "
                f"L={product.stock_l}, XL={product.stock_xl}, available={product.is_available}"
            )

            # Invalidar caché
            from django.core.cache import cache

            cache_key = f"catalog_products_{settings.CATALOG_ID}"
            cache.delete(cache_key)

            return JsonResponse(
                {
                    "status": "success",
                    "product": {
                        "retailer_id": product.retailer_id,
                        "name": product.product_name,
                        "stock_s": product.stock_s,
                        "stock_m": product.stock_m,
                        "stock_l": product.stock_l,
                        "stock_xl": product.stock_xl,
                        "total_stock": product.total_stock,
                        "is_available": product.is_available,
                    },
                }
            )

        except ValueError as e:
            return JsonResponse({"error": f"Invalid data: {str(e)}"}, status=400)
        except Exception as e:
            log.error(f"Error updating stock: {e}")
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Method not allowed"}, status=405)
