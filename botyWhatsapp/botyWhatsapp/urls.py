"""
URL configuration for botyWhatsapp project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.urls import path
import botyapp.views
from botyapp import (
    api,
    api_orders,
    api_snippets,
    api_orders_list,
    api_orders_reset,
    api_analytics,
    api_stock,
    api_orders_stock,
    api_products_excel,
)

urlpatterns = [
    # path('admin/', admin.site.urls),
    path("", botyapp.views.health_check, name="health_check"),
    path("webhook/", botyapp.views.whatsapp_webhook, name="whatsapp_webhook_meta"),
    path("api/chat-history/", api.get_chat_history, name="get_chat_history"),
    path("api/sync/", api.sync_data, name="api_sync"),
    path(
        "api/contacts/<str:phone>/toggle-bot/", api.toggle_bot_status, name="toggle_bot"
    ),
    path(
        "api/contacts/<str:phone>/send-message/",
        api.send_message_to_contact,
        name="send_message",
    ),
    path("api/media/<str:media_id>/", api.get_media, name="get_media"),
    path(
        "api/contacts/<str:phone>/send-media/",
        api.send_media_message,
        name="send_media",
    ),
    path(
        "api/contacts/<str:phone>/mark-read/", api.mark_messages_read, name="mark_read"
    ),
    path("api/products/", api.get_products_list, name="get_products"),
    path(
        "api/contacts/<str:phone>/send-product/",
        api.send_product_to_contact,
        name="send_product",
    ),
    path(
        "api/contacts/<str:phone>/send-catalog/",
        api.send_catalog_to_contact,
        name="send_catalog",
    ),
    path(
        "api/generate-embeddings/",
        api.generate_embeddings_endpoint,
        name="generate_embeddings",
    ),
    path(
        "api/messages/<int:msg_id>/delete/",
        botyapp.views.delete_message,
        name="delete_message",
    ),
    path(
        "api/contacts/<str:phone>/create-order/",
        api_orders.create_order,
        name="create_order",
    ),
    # Orders Management
    path("api/orders/", api_orders_list.get_orders_list, name="get_orders"),
    path(
        "api/orders/<int:order_id>/status/",
        api_orders_list.update_order_status,
        name="update_order_status",
    ),
    path(
        "api/orders/reset/",
        api_orders_reset.reset_orders,
        name="reset_orders",
    ),
    # --- DASHBOARD & SNIPPETS V2 ---
    path("api/dashboard-stats/", api.get_dashboard_stats, name="dashboard_stats"),
    path("api/snippets/", api_snippets.get_snippets, name="get_snippets"),
    path("api/snippets/create/", api_snippets.create_snippet, name="create_snippet"),
    path(
        "api/snippets/<int:snippet_id>/",
        api_snippets.delete_snippet,
        name="delete_snippet",
    ),
    # --- ANALYTICS V3 ---
    path(
        "api/analytics/stats/",
        api_analytics.get_analytics_stats,
        name="analytics_stats",
    ),
    path(
        "api/analytics/trends/",
        api_analytics.get_analytics_trends,
        name="analytics_trends",
    ),
    # --- STOCK MANAGEMENT ---
    path(
        "api/products/stock/",
        api_stock.update_stock,
        name="update_product_stock",
    ),
    # --- ORDERS STOCK DEDUCTION ---
    path(
        "api/orders/<int:order_id>/deduct-stock/",
        api_orders_stock.deduct_order_stock,
        name="deduct_order_stock",
    ),
    path(
        "api/orders/<int:order_id>/revert-stock/",
        api_orders_stock.revert_order_stock,
        name="revert_order_stock",
    ),
    # --- ORDER ITEMS MANAGEMENT ---
    path(
        "api/orders/items/<int:item_id>/size/",
        api_orders.assign_item_size,
        name="assign_item_size",
    ),
    # --- EXCEL IMPORT/EXPORT ---
    path(
        "api/products/export/excel/",
        api_products_excel.export_products_excel,
        name="export_products_excel",
    ),
    path(
        "api/products/import/excel/",
        api_products_excel.import_products_excel,
        name="import_products_excel",
    ),
]
