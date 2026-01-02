from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from .models import Order, OrderItem
from logger import log


@csrf_exempt
def reset_orders(request):
    """
    DELETE /api/orders/reset/
    CUIDADO: Elimina TODOS los pedidos de la base de datos.
    Usar solo para limpiar datos de prueba.
    """
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method != "DELETE":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        # Contar antes de eliminar
        order_count = Order.objects.count()
        item_count = OrderItem.objects.count()

        # Eliminar todos los pedidos (cascade eliminará OrderItems automáticamente)
        Order.objects.all().delete()

        log.warning(f"🗑️ RESET: Deleted {order_count} orders and {item_count} items")

        return JsonResponse(
            {
                "status": "success",
                "deleted_orders": order_count,
                "deleted_items": item_count,
                "message": "All orders deleted. Dashboard reset to 0.",
            }
        )

    except Exception as e:
        log.error(f"Error resetting orders: {e}")
        return JsonResponse({"error": str(e)}, status=500)
