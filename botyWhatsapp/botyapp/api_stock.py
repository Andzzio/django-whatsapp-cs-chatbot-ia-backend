from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.core.cache import cache
from .models import ProductEmbedding
from logger import log


@csrf_exempt
def update_product_stock(request, retailer_id):
    """
    PUT /api/products/<retailer_id>/stock/
    Actualiza stock y disponibilidad de un producto

    Body:
    {
        "stock_quantity": 50,
        "is_available": true  // Opcional
    }

    Returns:
    {
        "status": "success",
        "retailer_id": "PROD001",
        "stock_quantity": 50,
        "is_available": true
    }
    """
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method != "PUT":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        import json

        data = json.loads(request.body)
        stock_quantity = data.get("stock_quantity")
        is_available = data.get("is_available")  # Opcional

        # Validación de stock
        if stock_quantity is None:
            return JsonResponse({"error": "stock_quantity is required"}, status=400)

        try:
            stock_quantity = int(stock_quantity)
        except (ValueError, TypeError):
            return JsonResponse(
                {"error": "stock_quantity must be a valid integer"}, status=400
            )

        if stock_quantity < 0:
            return JsonResponse({"error": "stock_quantity must be >= 0"}, status=400)

        # Buscar producto
        try:
            product = ProductEmbedding.objects.get(retailer_id=retailer_id)
        except ProductEmbedding.DoesNotExist:
            return JsonResponse({"error": "Product not found"}, status=404)

        # Actualizar stock
        product.stock_quantity = stock_quantity

        # Actualizar disponibilidad
        if is_available is not None:
            # Si se envió explícitamente, usar ese valor
            product.is_available = bool(is_available)
        else:
            # Auto-calcular basado en stock
            if stock_quantity == 0:
                product.is_available = False
            elif stock_quantity > 0 and not product.is_available:
                # Si hay stock y estaba marcado como no disponible, activar
                product.is_available = True

        product.save()

        # Invalidar cache del catálogo
        if hasattr(settings, "CATALOG_ID"):
            cache_key = f"catalog_products_{settings.CATALOG_ID}"
            cache.delete(cache_key)

        log.info(
            f"Stock updated for {retailer_id}: stock={stock_quantity}, "
            f"available={product.is_available}"
        )

        return JsonResponse(
            {
                "status": "success",
                "retailer_id": retailer_id,
                "stock_quantity": product.stock_quantity,
                "is_available": product.is_available,
            }
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        log.error(f"Error updating stock for {retailer_id}: {e}")
        return JsonResponse({"error": str(e)}, status=500)
