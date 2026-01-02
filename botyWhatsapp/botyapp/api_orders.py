from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from .models import Contact, Order, OrderItem, ProductEmbedding
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
                # No guardamos product_image_url para ahorrar espacio en BD
            )

        order.calculate_totals()

        # No generamos mensaje de texto aquí. El frontend se encarga de la presentación.
        # Solo devolvemos la data estructurada.

        return JsonResponse(
            {
                "status": "success",
                "order": {
                    "id": order.id,
                    "total": float(order.total_amount),
                    "subtotal": float(order.subtotal),
                    "shipping": float(order.shipping_cost),
                    "discount": float(order.discount),
                    "items": items,  # Ya contiene quantity, name, price
                },
            }
        )

    except Contact.DoesNotExist:
        return JsonResponse({"error": "Contact not found"}, status=404)
    except Exception as e:
        log.error(f"Error creating order: {e}")
        return JsonResponse({"error": str(e)}, status=500)
