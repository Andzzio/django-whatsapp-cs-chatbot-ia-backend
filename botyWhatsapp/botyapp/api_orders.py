from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from .models import Contact, Order, OrderItem, ProductEmbedding
import json
from logger import log
from botyapp.services.whatsapp import send_whatsapp_message


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

        # Validar que todos los items tengan talla
        missing_size = [i for i in items if not i.get("size")]
        if missing_size:
            return JsonResponse(
                {
                    "error": "Todos los productos deben tener talla asignada",
                    "items_missing_size": [
                        i.get("name", "Unknown") for i in missing_size
                    ],
                },
                status=400,
            )

        # Crear Orden PENDING (con tallas ya asignadas)
        from decimal import Decimal

        order = Order.objects.create(
            contact=contact,
            status="PENDING",  # Ya tiene tallas, listo para confirmar
            shipping_cost=Decimal(str(data.get("shipping_cost", 0))),
            discount=Decimal(str(data.get("discount", 0))),
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
            from decimal import Decimal

            try:
                raw_price = item.get("price", 0)
                price_val = Decimal(str(raw_price))
            except (ValueError, TypeError, Exception):
                price_val = Decimal("0.0")

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

            # Obtener talla del item
            size = item.get("size", "").upper()

            # Validar stock de la talla
            if size and product_embedding:
                size_field = f"stock_{size.lower()}"
                available_stock = getattr(product_embedding, size_field, 0)
                requested_qty = item.get("quantity", 1)

                if available_stock < requested_qty:
                    return JsonResponse(
                        {
                            "error": "Stock insuficiente",
                            "product": name_val,
                            "size": size,
                            "requested": requested_qty,
                            "available": available_stock,
                        },
                        status=400,
                    )

            # Crear OrderItem con talla
            OrderItem.objects.create(
                order=order,
                product=product_embedding,
                product_name=name_val,
                quantity=item.get("quantity", 1),
                price=price_val,
                size=size,  # ✅ Talla asignada desde POS
            )

        order.calculate_totals()

        # ✅ Auto-descontar stock si es orden confirmada (Dashboard/Con Tallas)
        if order.status == "PENDING":
            try:
                from .api_orders_stock import _deduct_stock_internal

                result = _deduct_stock_internal(order)
                if not result["success"]:
                    log.error(f"⚠️ Error auto-deducting stock: {result['error']}")
                    # Podríamos decidir fallar la creación o solo loguear.
                    # Por ahora solo logueamos para no romper el flujo UX.
                else:
                    log.info(f"✅ Stock auto-deducted for new order #{order.id}")
            except Exception as e:
                log.error(f"❌ Critical error auto-deducting stock: {e}")

        # ✅ Auto-Send Confirmation Message (Fix for "No llegó al cliente")
        # Generar texto del recibo
        lines = ["📋 *Resumen de tu Pedido - SHURUMBA* ✨", ""]
        lines.append(f"🆔 *Orden #{order.id}*")
        lines.append("")
        lines.append("👗 *Tus prendas:*")

        for item in items:
            p_name = item.get("name", "Producto")
            p_qty = item.get("quantity", 1)
            p_price = item.get("price", 0)
            p_size = item.get("size", "N/A")
            lines.append(f"- {p_qty}x {p_name} ({p_size}) - S/{p_price}")

        lines.append("")
        if order.shipping_cost > 0:
            lines.append(f"🚚 *Envío:* S/{order.shipping_cost}")

        lines.append("---------------------")
        lines.append(f"💰 *TOTAL A PAGAR: S/{order.total_amount}*")
        lines.append("")
        lines.append("¡Gracias por elegirnos! 💖")

        receipt_text = "\n".join(lines)

        # Enviar mensaje
        try:
            send_whatsapp_message(contact.phone, receipt_text)
            log.info(f"📤 Receipt sent for Order #{order.id}")
        except Exception as e:
            log.error(f"❌ Failed to send receipt for Order #{order.id}: {e}")

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


@csrf_exempt
def assign_item_size(request, item_id):
    """
    PUT /api/orders/items/<item_id>/size/
    Asigna talla a un OrderItem desde el dashboard.

    Body: {"size": "M"}
    """
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method != "PUT":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body)
        size = data.get("size", "").upper()

        # Validar talla
        if size not in ["S", "M", "L", "XL"]:
            return JsonResponse(
                {"error": "Talla inválida. Use: S, M, L, XL"}, status=400
            )

        # Obtener OrderItem
        item = OrderItem.objects.get(id=item_id)

        # Validar stock de la talla
        if item.product:
            size_field = f"stock_{size.lower()}"
            available_stock = getattr(item.product, size_field, 0)

            if available_stock < item.quantity:
                return JsonResponse(
                    {
                        "error": "Stock insuficiente",
                        "size": size,
                        "requested": item.quantity,
                        "available": available_stock,
                    },
                    status=400,
                )

        # Asignar talla
        item.size = size
        item.save()

        log.info(f"✅ Talla {size} asignada a OrderItem #{item.id}")

        # Si todos los items de la orden tienen talla, cambiar a PENDING
        order = item.order
        all_have_size = all(i.size is not None for i in order.items.all())

        if all_have_size and order.status == "PENDING_SIZE":
            order.status = "PENDING"
            order.save()
            log.info(f"✅ Orden #{order.id} cambió a PENDING")

        return JsonResponse(
            {
                "status": "success",
                "item_id": item.id,
                "size": size,
                "order_status": order.status,
                "all_sizes_assigned": all_have_size,
            }
        )

    except OrderItem.DoesNotExist:
        return JsonResponse({"error": "Item no encontrado"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON inválido"}, status=400)
    except Exception as e:
        log.error(f"Error asignando talla: {e}")
        return JsonResponse({"error": str(e)}, status=500)
