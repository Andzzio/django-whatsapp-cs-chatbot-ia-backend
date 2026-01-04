from datetime import datetime
import pytz
from django.conf import settings
from logger import log
from .whatsapp import send_whatsapp_message
from .catalog import get_product_info
from .users import get_user_name


def get_current_time():
    """Obtiene la hora actual formateada"""
    # Zona horaria de Perú
    peru_tz = pytz.timezone("America/Lima")
    now = datetime.now(peru_tz)
    return now.strftime("%d/%m/%Y %I:%M %p")


def notify_owner(order_data, sender_id, total_price, currency):
    """
    Notifica al dueño sobre una nueva orden recibida
    """
    catalog_id = order_data.get("catalog_id")
    product_items = order_data.get("product_items", [])
    customer_note = order_data.get("text", "")

    # Construir mensaje para el dueño
    owner_message = "🔔 *NUEVA ORDEN RECIBIDA*\n\n"
    owner_message += f"👤 *Cliente:* {get_user_name(sender_id)}\n"
    owner_message += f"📱 *Número:* {sender_id}\n"
    owner_message += f"📋 *Catalog ID:* {catalog_id}\n\n"
    owner_message += "📦 *PRODUCTOS:*\n\n"

    for idx, item in enumerate(product_items, 1):
        product_sku = item.get("product_retailer_id")
        quantity = item.get("quantity", 1)
        item_price = float(item.get("item_price", 0))
        subtotal = item_price * quantity

        # Intentar obtener nombre del producto
        product_info = get_product_info(catalog_id, product_sku)
        product_name = product_info.get("name", f"Producto {product_sku}")

        owner_message += f"{idx}. *{product_name}*\n"
        owner_message += f"   SKU: `{product_sku}`\n"
        owner_message += f"   Cantidad: {quantity}\n"
        owner_message += f"   Precio unit: {currency} {item_price:.2f}\n"
        owner_message += f"   Subtotal: {currency} {subtotal:.2f}\n\n"

    owner_message += f"{'─' * 30}\n"
    owner_message += f"💰 *TOTAL: {currency} {total_price:.2f}*\n"

    if customer_note:
        owner_message += f"\n💬 *Nota del cliente:*\n_{customer_note}_\n"

    owner_message += f"\n⏰ Hora: {get_current_time()}"

    # Enviar al número del dueño
    owner_phone = settings.OWNER_PHONE_NUMBER
    send_whatsapp_message(owner_phone, owner_message)
    log.debug(f"✅ Notificación enviada al dueño: {owner_phone}")


def process_order(order_data, sender_id):
    """
    Procesa una orden recibida del catálogo
    """
    try:
        # Extraer información de la orden
        catalog_id = order_data.get("catalog_id")
        product_items = order_data.get("product_items", [])
        text = order_data.get("text", "")  # Nota o comentario del cliente

        log.debug("🛒 NUEVA ORDEN RECIBIDA")
        log.debug(f"📋 Catalog ID: {catalog_id}")
        log.debug(f"👤 Cliente: {sender_id}")
        log.debug(f"💬 Nota del cliente: {text}")
        log.debug("📦 Productos:")

        total_items = 0
        total_price = 0
        order_summary = "📦 *Resumen de tu pedido:*\n\n"

        for idx, item in enumerate(product_items, 1):
            product_retailer_id = item.get("product_retailer_id")
            quantity = item.get("quantity", 1)
            item_price = float(item.get("item_price", 0))
            currency = item.get("currency", "PEN")

            # 🔍 Obtener información del producto desde la API
            log.debug(
                f"🔍 Consultando producto {idx}/{len(product_items)}: {product_retailer_id}"
            )
            product_info = get_product_info(catalog_id, product_retailer_id)
            product_name = product_info["name"]

            subtotal = item_price * quantity
            total_items += quantity
            total_price += subtotal

            log.debug(f"  ✓ Nombre: {product_name}")
            log.debug(f"  ✓ Cantidad: {quantity}")
            log.debug(f"  ✓ Precio unitario: {currency} {item_price:.2f}")
            log.debug(f"  ✓ Subtotal: {currency} {subtotal:.2f}\n")

            # Formatear el resumen para WhatsApp
            order_summary += f"{idx}. *{product_name}*\n"
            order_summary += f"   📦 Cantidad: {quantity} {'unidad' if quantity == 1 else 'unidades'}\n"
            order_summary += f"   💵 Precio unitario: {currency} {item_price:.2f}\n"
            order_summary += f"   💰 Subtotal: {currency} {subtotal:.2f}\n\n"

        shipping_cost = 10.0
        total_with_shipping = total_price + shipping_cost

        order_summary += f"{'─' * 30}\n"
        order_summary += f"📊 *Total de productos:* {total_items}\n"
        order_summary += f"📦 *Subtotal:* {currency} {total_price:.2f}\n"
        order_summary += f"🚚 *Envío:* {currency} {shipping_cost:.2f}\n"
        order_summary += f"💰 *TOTAL FINAL:* {currency} {total_with_shipping:.2f}\n"

        if text:
            order_summary += f"\n💬 *Tu nota:* _{text}_"

        # Enviar confirmación al cliente
        confirmation_message = (
            f"✅ *¡Pedido recibido exitosamente!*\n\n"
            f"{order_summary}\n\n"
            f"Estamos procesando tu pedido. 📋\n"
            f"En breve nos pondremos en contacto contigo para coordinar la entrega. 🚚\n\n"
            f"_Gracias por tu compra_ 🙌✨"
        )

        send_whatsapp_message(sender_id, confirmation_message)

        # ✅ CREAR ORDEN EN BASE DE DATOS
        from botyapp.models import Contact, Order, OrderItem, ProductEmbedding

        try:
            contact = Contact.objects.get(phone=sender_id)

            # Crear la orden
            order = Order.objects.create(
                contact=contact,
                status="PENDING",
                checkout_stage="COMPLETED",
                shipping_cost=shipping_cost,
                subtotal=total_price,
                total_amount=total_with_shipping,
            )

            # Crear items de la orden
            for item in product_items:
                product_retailer_id = item.get("product_retailer_id")
                quantity = item.get("quantity", 1)
                item_price = float(item.get("item_price", 0))

                try:
                    # Buscar producto en BD
                    product = ProductEmbedding.objects.get(
                        retailer_id=product_retailer_id
                    )

                    OrderItem.objects.create(
                        order=order,
                        product=product,
                        quantity=quantity,
                        unit_price=item_price,
                    )
                except ProductEmbedding.DoesNotExist:
                    log.warning(
                        f"⚠️ Producto {product_retailer_id} no encontrado en BD, saltando item"
                    )
                    continue

            # Recalcular totales
            order.calculate_totals()

            log.debug(f"✅ Orden #{order.id} creada en BD exitosamente")

        except Contact.DoesNotExist:
            log.error(f"❌ Contacto {sender_id} no encontrado")
        except Exception as db_error:
            log.error(f"❌ Error creando orden en BD: {db_error}")
            import traceback

            traceback.print_exc()

        log.debug("✅ Orden procesada exitosamente")
        log.debug(f"💰 Total: {currency} {total_price:.2f}")
        log.debug(f"{'=' * 50}\n")

        notify_owner(order_data, sender_id, total_price, currency)
        return True
    except Exception as e:
        log.error(f"❌ Error procesando orden: {e}")
        import traceback

        traceback.print_exc()

        # Enviar mensaje de error al cliente
        send_whatsapp_message(
            sender_id,
            "😔 Lo sentimos, hubo un error al procesar tu pedido. "
            "Por favor, inténtalo nuevamente o contáctanos directamente.",
        )
        return False
