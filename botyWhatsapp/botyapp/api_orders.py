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
            # Buscamos el producto. Si no existe, lo creamos "Lazy" (Auto-Healing)
            retailer_id = item.get("retailer_id") or item.get("id")

            # Robustez: ID obligatorio y Trunk
            if not retailer_id:
                import uuid

                retailer_id = f"custom_{uuid.uuid4().hex[:8]}"
            retailer_id = str(retailer_id)[:190]  # Evitar overflow

            # Parseo seguro de precio
            try:
                raw_price = item.get("unit_price", 0) or item.get("price", 0)
                price_val = float(raw_price)
            except (ValueError, TypeError):
                price_val = 0.0

            # Get or Create seguro con Truncamiento
            name_val = str(item.get("name", "Producto Desconocido"))[:190]

            defaults = {
                "product_name": name_val,
                "price": price_val,
                "search_text": f"{name_val}".lower(),
                "is_available": True,
                "image_url": str(item.get("image_url", ""))[:490],
            }

            product_embedding, created = ProductEmbedding.objects.get_or_create(
                retailer_id=retailer_id, defaults=defaults
            )

            # Si ya existía pero queremos asegurar que tenga info reciente (opcional)
            # if not created:
            #    product_embedding.product_name = defaults["product_name"]
            #    product_embedding.save()

            OrderItem.objects.create(
                order=order,
                product=product_embedding,
                quantity=item.get("quantity", 1),
                unit_price=price_val,
                product_name=name_val,
                product_image_url=item.get("image_url", product_embedding.image_url),
            )

        order.calculate_totals()

        # Generar Mensaje de Confirmación en el Chat (Visible para usuario y agente)
        total_str = f"S/{order.total_amount:.2f}"
        items_summary = "\n".join(
            [f"- {i.get('quantity')}x {i.get('name')}" for i in items]
        )

        lines = [
            f"🧾 *Pedido Generado #{order.id}*",
            "",
            items_summary,
            "",
        ]

        if order.shipping_cost > 0:
            lines.append(f"🚚 Envío: S/{order.shipping_cost:.2f}")

        if order.discount > 0:
            lines.append(f"🏷️ Descuento: -S/{order.discount:.2f}")

        lines.append(f"💰 *Total: {total_str}*")

        message_text = "\n".join(lines)

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
