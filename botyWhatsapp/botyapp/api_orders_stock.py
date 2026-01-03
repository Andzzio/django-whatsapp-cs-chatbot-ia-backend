from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from .models import Order
from logger import log


@csrf_exempt
def deduct_order_stock(request, order_id):
    """
    POST /api/orders/<order_id>/deduct-stock/
    Descuenta stock de todos los productos en la orden
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    try:
        order = Order.objects.prefetch_related("items__product").get(id=order_id)
    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)

    # Validar que no se haya descontado ya
    if order.stock_deducted:
        return JsonResponse(
            {
                "error": "Stock already deducted for this order",
                "deducted_at": (
                    order.stock_deducted_at.isoformat()
                    if order.stock_deducted_at
                    else None
                ),
            },
            status=400,
        )

    # Verificar stock suficiente
    insufficient_stock = []
    for item in order.items.all():
        if item.product.stock_quantity < item.quantity:
            insufficient_stock.append(
                {
                    "product": item.product_name,
                    "needed": item.quantity,
                    "available": item.product.stock_quantity,
                }
            )

    if insufficient_stock:
        return JsonResponse(
            {"error": "Insufficient stock", "details": insufficient_stock}, status=400
        )

    # Descontar stock
    deducted_items = []
    for item in order.items.all():
        product = item.product
        product.stock_quantity -= item.quantity

        # Auto-marcar como no disponible si llega a 0
        if product.stock_quantity == 0:
            product.is_available = False

        product.save()

        deducted_items.append(
            {
                "product_name": item.product_name,
                "quantity_deducted": item.quantity,
                "new_stock": product.stock_quantity,
            }
        )

    # Marcar orden
    order.stock_deducted = True
    order.stock_deducted_at = timezone.now()
    order.save()

    # Invalidar cache de productos
    if hasattr(settings, "CATALOG_ID"):
        cache_key = f"catalog_products_{settings.CATALOG_ID}"
        cache.delete(cache_key)

    log.info(f"Stock deducted for order #{order.id}: {len(deducted_items)} items")

    return JsonResponse(
        {"status": "success", "order_id": order.id, "deducted_items": deducted_items}
    )
