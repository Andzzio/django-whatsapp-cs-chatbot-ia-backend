from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from .models import Order
from logger import log


@csrf_exempt
def get_orders_list(request):
    """
    GET /api/orders/
    Lista todas las órdenes con filtros opcionales
    Query params:
    - status: pending/completed/cancelled/all (default: all)
    - limit: número de resultados (default: 50)
    """
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        # Filtros
        status_filter = request.GET.get("status", "all").upper()
        limit = int(request.GET.get("limit", 50))

        # Query base
        orders_qs = Order.objects.select_related("contact").prefetch_related("items")

        # Aplicar filtro de estado
        if status_filter != "ALL":
            orders_qs = orders_qs.filter(status=status_filter)

        # Ordenar por más recientes primero
        orders_qs = orders_qs.order_by("-created_at")[:limit]

        # Serializar
        orders_data = []
        for order in orders_qs:
            items_data = [
                {
                    "product_name": item.product_name,
                    "quantity": item.quantity,
                    "unit_price": float(item.unit_price),
                    "subtotal": float(item.subtotal),
                }
                for item in order.items.all()
            ]

            orders_data.append(
                {
                    "id": order.id,
                    "contact_name": order.contact.name,
                    "contact_phone": order.contact.phone,
                    "status": order.status,
                    "total_amount": float(order.total_amount),
                    "subtotal": float(order.subtotal),
                    "shipping_cost": float(order.shipping_cost),
                    "discount": float(order.discount),
                    "items": items_data,
                    "created_at": order.created_at.isoformat(),
                }
            )

        return JsonResponse({"orders": orders_data, "count": len(orders_data)})

    except Exception as e:
        log.error(f"Error fetching orders: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def update_order_status(request, order_id):
    """
    PATCH /api/orders/<id>/status/
    Actualiza el estado de una orden
    Body: {"status": "COMPLETED" | "CANCELLED" | "PENDING"}
    """
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        import json

        order = Order.objects.get(id=order_id)
        data = json.loads(request.body)
        new_status = data.get("status", "").upper()

        if new_status not in ["PENDING", "COMPLETED", "CANCELLED"]:
            return JsonResponse({"error": "Invalid status"}, status=400)

        order.status = new_status
        order.save()

        return JsonResponse({"status": "success", "new_status": new_status})

    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)
    except Exception as e:
        log.error(f"Error updating order status: {e}")
        return JsonResponse({"error": str(e)}, status=500)
