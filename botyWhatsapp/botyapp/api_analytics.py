from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate, TruncMonth
from django.utils import timezone
from django.core.cache import cache
from datetime import timedelta
from .models import Contact, Message, Order
from logger import log


@csrf_exempt
def get_analytics_stats(request):
    """
    GET /api/analytics/stats/
    Retorna métricas agregadas para la pantalla de Estadísticas.

    Query params:
    - period: today, week, month, year (default: week)

    Returns:
    - total_conversations: Total de conversaciones (contactos con mensajes)
    - pending_orders: Pedidos pendientes
    - unread_messages: Total mensajes no leídos del usuario
    - online_now: Contactos con actividad en últimas 24h
    - sales_total: Ventas totales del período
    - avg_ticket: Ticket promedio
    - conversion_rate: Tasa de conversión
    - new_clients: Nuevos clientes en el período
    """
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        period = request.GET.get("period", "week")

        # Cache key basada en el período
        cache_key = f"analytics_stats_{period}"
        cached_data = cache.get(cache_key)
        if cached_data:
            return JsonResponse(cached_data)

        now = timezone.now()

        # Calcular inicio del período
        if period == "today":
            period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "week":
            # Inicio de la semana (lunes)
            period_start = now - timedelta(days=now.weekday())
            period_start = period_start.replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        elif period == "month":
            period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        elif period == "year":
            period_start = now.replace(
                month=1, day=1, hour=0, minute=0, second=0, microsecond=0
            )
        else:
            # Default a semana
            period_start = now - timedelta(days=now.weekday())
            period_start = period_start.replace(
                hour=0, minute=0, second=0, microsecond=0
            )

        # 1. Total de conversaciones (contactos con mensajes en el período)
        total_conversations = (
            Contact.objects.filter(messages__timestamp__gte=period_start)
            .distinct()
            .count()
        )

        # 2. Pedidos pendientes (sin filtro de período, siempre pendientes actuales)
        pending_orders = Order.objects.filter(status="PENDING").count()

        # 3. Mensajes no leídos del usuario (no del bot)
        unread_messages = Message.objects.filter(is_read=False, is_bot=False).count()

        # 4. Contactos online (actividad en últimas 24h)
        last_24h = now - timedelta(hours=24)
        online_now = (
            Contact.objects.filter(messages__timestamp__gte=last_24h).distinct().count()
        )

        # 5. Ventas totales del período y métricas relacionadas
        sales_metrics = (
            Order.objects.filter(created_at__gte=period_start)
            .exclude(status="CANCELLED")
            .aggregate(total_sales=Sum("total_amount"), total_orders=Count("id"))
        )

        sales_total = float(sales_metrics["total_sales"] or 0.0)
        total_orders = sales_metrics["total_orders"] or 0

        # 6. Ticket promedio
        avg_ticket = 0.0
        if total_orders > 0:
            avg_ticket = sales_total / total_orders

        # 7. Tasa de conversión (pedidos / conversaciones)
        conversion_rate = 0.0
        if total_conversations > 0:
            conversion_rate = (total_orders / total_conversations) * 100

        # 8. Nuevos clientes en el período
        new_clients = Contact.objects.filter(created_at__gte=period_start).count()

        stats = {
            "total_conversations": total_conversations,
            "pending_orders": pending_orders,
            "unread_messages": unread_messages,
            "online_now": online_now,
            "sales_total": round(sales_total, 2),
            "avg_ticket": round(avg_ticket, 2),
            "conversion_rate": round(conversion_rate, 1),
            "new_clients": new_clients,
            "period": period,
        }

        # Cachear por 5 minutos
        cache.set(cache_key, stats, timeout=300)

        return JsonResponse(stats)

    except Exception as e:
        log.error(f"Error in analytics stats: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def get_analytics_trends(request):
    """
    GET /api/analytics/trends/
    Retorna datos de serie temporal para gráficos.

    Query params:
    - period: week, month, year (default: week)
    - metric: sales, conversations, orders (default: sales)

    Returns:
    - labels: Array de etiquetas (días de la semana, días del mes, meses)
    - values: Array de valores numéricos correspondientes
    """
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        period = request.GET.get("period", "week")
        metric = request.GET.get("metric", "sales")

        # Cache key
        cache_key = f"analytics_trends_{period}_{metric}"
        cached_data = cache.get(cache_key)
        if cached_data:
            return JsonResponse(cached_data)

        now = timezone.now()

        # Configurar período y truncado
        if period == "week":
            # Últimos 7 días
            period_start = now - timedelta(days=6)
            period_start = period_start.replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            trunc_func = TruncDate
            label_format = "%a"  # Lun, Mar, Mié...

        elif period == "month":
            # Últimos 30 días
            period_start = now - timedelta(days=29)
            period_start = period_start.replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            trunc_func = TruncDate
            label_format = "%d/%m"  # 01/01, 02/01...

        elif period == "year":
            # Últimos 12 meses
            period_start = now - timedelta(days=365)
            period_start = period_start.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            trunc_func = TruncMonth
            label_format = "%b"  # Ene, Feb, Mar...

        else:
            # Default a semana
            period_start = now - timedelta(days=6)
            period_start = period_start.replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            trunc_func = TruncDate
            label_format = "%a"

        # Obtener datos según métrica
        if metric == "sales":
            # Ventas por período
            data = (
                Order.objects.filter(created_at__gte=period_start)
                .exclude(status="CANCELLED")
                .annotate(period=trunc_func("created_at"))
                .values("period")
                .annotate(value=Sum("total_amount"))
                .order_by("period")
            )

        elif metric == "conversations":
            # Conversaciones únicas por período
            data = (
                Message.objects.filter(timestamp__gte=period_start)
                .annotate(period=trunc_func("timestamp"))
                .values("period")
                .annotate(value=Count("contact", distinct=True))
                .order_by("period")
            )

        elif metric == "orders":
            # Pedidos por período
            data = (
                Order.objects.filter(created_at__gte=period_start)
                .exclude(status="CANCELLED")
                .annotate(period=trunc_func("created_at"))
                .values("period")
                .annotate(value=Count("id"))
                .order_by("period")
            )

        else:
            return JsonResponse({"error": "Invalid metric"}, status=400)

        # Crear diccionario de resultados
        results_dict = {
            item["period"].strftime(label_format): float(item["value"] or 0)
            for item in data
        }

        # Generar todas las etiquetas del período (incluso si no hay datos)
        labels = []
        values = []

        if period in ["week", "month"]:
            # Generar días
            days_count = 7 if period == "week" else 30
            current = period_start

            for i in range(days_count):
                label = current.strftime(label_format)
                labels.append(label)
                values.append(results_dict.get(label, 0))
                current += timedelta(days=1)

        elif period == "year":
            # Generar meses

            current = period_start

            for i in range(12):
                label = current.strftime(label_format)
                labels.append(label)
                values.append(results_dict.get(label, 0))

                # Avanzar al siguiente mes
                if current.month == 12:
                    current = current.replace(year=current.year + 1, month=1)
                else:
                    current = current.replace(month=current.month + 1)

        trend_data = {
            "labels": labels,
            "values": values,
            "metric": metric,
            "period": period,
        }

        # Cachear por 5 minutos
        cache.set(cache_key, trend_data, timeout=300)

        return JsonResponse(trend_data)

    except Exception as e:
        log.error(f"Error in analytics trends: {e}")
        return JsonResponse({"error": str(e)}, status=500)
