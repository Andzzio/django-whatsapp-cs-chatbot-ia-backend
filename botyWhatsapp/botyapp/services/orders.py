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

            # --- MOD: Reverted Talla por confirmar ---
            order_summary += f"{idx}. *{product_name}*\n"
            # order_summary += "   (Talla: Por confirmar)\n" # REMOVED
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
            f"📝 *Estado:* Pendiente de confirmación\n"
            f"👥 *ATENCIÓN:* Hemos recibido tu pedido. Un asesor humano revisará las tallas contigo en breve.\n\n"
            f"_Gracias por tu compra_ 🙌✨"
        )

        send_whatsapp_message(sender_id, confirmation_message)

        # ✅ CREAR ORDEN EN BASE DE DATOS con status PENDING_SIZE
        from botyapp.models import Contact, Order, OrderItem, ProductEmbedding

        try:
            contact = Contact.objects.get(phone=sender_id)

            # --- MOD: Desactivar Bot y Pedir Atención Humana (User Request) ---
            contact.is_bot_active = False
            contact.needs_human_attention = True
            contact.save()
            log.info(
                f"🤖 Bot DESACTIVADO para {sender_id} tras recibir pedido (Handover)."
            )

            # Crear la orden con status PROFORMA (sin tallas asignadas)
            order = Order.objects.create(
                contact=contact,
                status="PROFORMA",  # Estado: Proforma (antes PENDING_SIZE)
                checkout_stage="COMPLETED",
                shipping_cost=shipping_cost,
                subtotal=total_price,
                total_amount=total_with_shipping,
            )

            # Crear items de la orden SIN talla (se asigna desde dashboard)
            for item in product_items:
                product_retailer_id = item.get("product_retailer_id")
                quantity = item.get("quantity", 1)
                item_price = float(item.get("item_price", 0))

                try:
                    # Buscar producto en BD
                    product = ProductEmbedding.objects.get(
                        retailer_id=product_retailer_id
                    )

                    # Crear OrderItem SIN talla
                    OrderItem.objects.create(
                        order=order,
                        product=product,
                        product_name=product.product_name,
                        quantity=quantity,
                        price=item_price,
                        size=None,  # ✅ Sin talla - se asigna desde dashboard
                    )

                    log.debug(
                        f"  ✅ Item guardado: {product.product_name} x{quantity} (Sin talla)"
                    )

                except ProductEmbedding.DoesNotExist:
                    log.warning(
                        f"  ⚠️  Producto {product_retailer_id} no encontrado en BD"
                    )
                    # Crear item sin product reference
                    OrderItem.objects.create(
                        order=order,
                        product=None,
                        product_name=f"Producto {product_retailer_id}",
                        quantity=quantity,
                        price=item_price,
                        size=None,
                    )

            log.info(
                f"✅ Orden #{order.id} registrada en BD (PENDING_SIZE - {order.items.count()} items)"
            )

        except Contact.DoesNotExist:
            log.error(f"❌ Contacto {sender_id} no encontrado en BD")
        except Exception as e:
            log.error(f"❌ Error guardando orden: {e}")

        # Notificar al dueño
        notify_owner(order_data, sender_id, total_with_shipping, currency)

        log.info(f"✅ Orden procesada exitosamente para {sender_id}")
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
