from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from .models import Contact, Order, OrderItem, ProductEmbedding, Message
import json
from logger import log


@csrf_exempt
def create_order(request, phone):
    """
    Crea una nueva orden para un contacto específico.
    Payload esperado:
    {
        "items": [
            {"retailer_id": "ID123", "quantity": 1, "unit_price": 50.00, "name": "Camisa", "image_url": "..."}
        ],
        "shipping_cost": 10.00,  (opcional)
        "discount": 0.00         (opcional)
    }
    """
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        contact = Contact.objects.get(phone=phone)
        data = json.loads(request.body)
        items = data.get("items", [])

        if not items:
            return JsonResponse({"error": "No items provided"}, status=400)

        # Crear Orden PENDING
        order = Order.objects.create(
            contact=contact,
            status="PENDING",
            shipping_cost=data.get("shipping_cost", 0),
            discount=data.get("discount", 0),
        )

        for item in items:
            # Buscar producto (opcional, para integridad, pero usamos info snapshot)
            product_embedding = None
            retailer_id = item.get("retailer_id")
            if retailer_id:
                product_embedding = ProductEmbedding.objects.filter(
                    retailer_id=retailer_id
                ).first()

            # Si no existe el producto en BD local (poco probable si viene del catálogo),
            # podríamos crearlo dummy o manejar error. Por ahora asumimos snapshot.
            # Nota: OrderItem requiere 'product' FK. Si el producto no está sincronizado, fallará.
            # Solución robusta: Si no existe, usamos un producto "Genérico" o lo creamos al vuelo (más complejo).
            # Para este MVP, asumiremos que ProductEmbedding existe porque viene del catálogo.

            if not product_embedding:
                # Fallback crítico: Si no existe embedding, no podemos crear OrderItem por la FK.
                # Opción: Ignorar FK (no posible en Django estricto) o fallar.
                # Decisión: Logear y saltar o Error. Retornamos error para debug.
                return JsonResponse(
                    {"error": f"Product {retailer_id} not found locally"}, status=400
                )

            OrderItem.objects.create(
                order=order,
                product=product_embedding,
                quantity=item.get("quantity", 1),
                unit_price=item.get("unit_price", 0),
                product_name=item.get("name", "Unknown Product"),
                product_image_url=item.get("image_url", ""),
            )

        order.calculate_totals()

        # Generar Mensaje de Confirmación en el Chat (Visible para usuario y agente)
        total_str = f"S/{order.total_amount:.2f}"
        items_summary = "\n".join(
            [f"- {i.get('quantity')}x {i.get('name')}" for i in items]
        )

        message_text = (
            f"🧾 *Pedido Generado #{order.id}*\n\n"
            f"{items_summary}\n\n"
            f"💰 *Total: {total_str}*"
        )

        Message.objects.create(
            contact=contact,
            text=message_text,
            is_bot=True,  # Lo marca como enviado por el sistema
            message_type="text",  # O 'order_summary' si quisieras UI especial en Flutter
        )

        return JsonResponse(
            {"status": "success", "order_id": order.id, "total": order.total_amount}
        )

    except Contact.DoesNotExist:
        return JsonResponse({"error": "Contact not found"}, status=404)
    except Exception as e:
        log.error(f"Error creating order: {e}")
        return JsonResponse({"error": str(e)}, status=500)
