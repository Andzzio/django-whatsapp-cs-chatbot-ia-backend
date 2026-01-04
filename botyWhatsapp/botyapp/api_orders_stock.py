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

    # Llamar lógica interna
    result = _deduct_stock_internal(order)

    if result["success"]:
        return JsonResponse(
            {
                "status": "success",
                "order_id": order.id,
                "deducted_items": result["deducted_items"],
            }
        )
    else:
        status_code = 400
        # Check if error is 'Stock already deducted' (logic from previous code)
        # For simplicity, returning 400 is fine as internal errors return detailed messages
        return JsonResponse(
            {"error": result["error"], "details": result.get("details")},
            status=status_code,
        )


def _deduct_stock_internal(order):
    """
    Internal logic to deduct stock from an order.
    Returns dict: {'success': bool, 'error': str, 'details': list, 'deducted_items': list}
    """
    try:
        # Validar que no se haya descontado ya, A MENOS que haya sido revertido
        if order.stock_deducted and not order.stock_reverted:
            return {
                "success": False,
                "error": "Stock already deducted for this order",
            }

        # Verificar que todos los items tengan talla
        items_without_size = []
        for item in order.items.all():
            if not item.size:
                items_without_size.append(item.product_name)

        if items_without_size:
            return {
                "success": False,
                "error": "All items must have a size assigned before deducting stock",
                "details": items_without_size,
            }

        # Verificar stock suficiente por talla
        insufficient_stock = []
        for item in order.items.all():
            stock_field = f"stock_{item.size.lower()}"
            current_stock = getattr(item.product, stock_field, 0)

            if current_stock < item.quantity:
                insufficient_stock.append(
                    {
                        "product": item.product_name,
                        "size": item.size,
                        "needed": item.quantity,
                        "available": current_stock,
                    }
                )

        if insufficient_stock:
            return {
                "success": False,
                "error": "Insufficient stock",
                "details": insufficient_stock,
            }

        # Descontar stock
        deducted_items = []
        for item in order.items.all():
            product = item.product
            stock_field = f"stock_{item.size.lower()}"
            current_stock = getattr(product, stock_field)
            new_stock = current_stock - item.quantity

            setattr(product, stock_field, new_stock)

            # Auto-marcar como no disponible si todo el stock llega a 0
            if product.total_stock == 0:
                product.is_available = False

            product.save()

            deducted_items.append(
                {
                    "product_name": item.product_name,
                    "size": item.size,
                    "quantity_deducted": item.quantity,
                    "new_stock": new_stock,
                }
            )

        # Marcar orden
        order.stock_deducted = True
        order.stock_reverted = False  # Resetear si estaba revertido
        order.stock_deducted_at = timezone.now()
        order.save()

        # Invalidar cache de productos
        if hasattr(settings, "CATALOG_ID"):
            cache_key = f"catalog_products_{settings.CATALOG_ID}"
            cache.delete(cache_key)

        log.info(f"Stock deducted for order #{order.id}: {len(deducted_items)} items")

        return {"success": True, "deducted_items": deducted_items}

    except Exception as e:
        log.error(f"Error deducting stock internally: {e}")
        return {"success": False, "error": str(e)}


@csrf_exempt
def revert_order_stock(request, order_id):
    """
    Revierte el stock descontado de una orden.
    PUT /api/orders/<order_id>/revert-stock/
    """
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method != "PUT":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        order = Order.objects.get(id=order_id)

        # Validar que el stock haya sido descontado
        if not order.stock_deducted:
            return JsonResponse(
                {"error": "Stock was not deducted for this order"}, status=400
            )

        # Validar que no haya sido revertido ya
        if order.stock_reverted:
            return JsonResponse(
                {"error": "Stock already reverted for this order"}, status=400
            )

        # Revertir stock
        result = _revert_stock_internal(order)

        if result["success"]:
            return JsonResponse(result)
        else:
            return JsonResponse({"error": result["error"]}, status=400)

    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)
    except Exception as e:
        log.error(f"Error reverting stock: {e}")
        return JsonResponse({"error": str(e)}, status=500)


def _revert_stock_internal(order):
    """
    Lógica interna para revertir stock (reutilizable).
    Retorna dict con 'success' y mensaje/error.
    """
    try:
        reverted_items = []

        for item in order.items.all():
            if not item.product:
                log.warning(f"OrderItem {item.id} has no product, skipping")
                continue

            if not item.size:
                return {
                    "success": False,
                    "error": f"Item '{item.product_name}' has no size assigned",
                }

            # Sumar stock según talla
            stock_field = f"stock_{item.size.lower()}"
            current_stock = getattr(item.product, stock_field, 0)
            new_stock = current_stock + item.quantity

            setattr(item.product, stock_field, new_stock)
            item.product.save()

            reverted_items.append(
                {
                    "product": item.product_name,
                    "size": item.size,
                    "quantity": item.quantity,
                    "new_stock": new_stock,
                }
            )

            log.info(
                f"Stock reverted for {item.product_name} ({item.size}): "
                f"+{item.quantity} → {new_stock}"
            )

        # Marcar como revertido
        order.stock_reverted = True
        order.save()

        # Invalidar caché de productos
        cache_key = f"catalog_products_{settings.CATALOG_ID}"
        cache.delete(cache_key)

        log.info(f"✅ Stock reverted for Order #{order.id}")

        return {
            "success": True,
            "message": f"Stock reverted successfully for {len(reverted_items)} items",
            "reverted_items": reverted_items,
        }

    except Exception as e:
        log.error(f"Error in _revert_stock_internal: {e}")
        return {"success": False, "error": str(e)}
