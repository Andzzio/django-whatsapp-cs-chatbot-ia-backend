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
from botyapp.api import (
    sync_data,
    toggle_bot_status,
    send_message_to_contact,
    get_media,
    send_media_message,
    mark_messages_read,
    get_products_list,
    send_product_to_contact,
    generate_embeddings_endpoint,
)

urlpatterns = [
    # path('admin/', admin.site.urls),
    path("", botyapp.views.health_check, name="health_check"),
    path("webhook/", botyapp.views.whatsapp_webhook, name="whatsapp_webhook_meta"),
    path("api/sync/", sync_data, name="api_sync"),
    path("api/contacts/<str:phone>/toggle-bot/", toggle_bot_status, name="toggle_bot"),
    path(
        "api/contacts/<str:phone>/send-message/",
        send_message_to_contact,
        name="send_message",
    ),
    path("api/media/<str:media_id>/", get_media, name="get_media"),
    path("api/contacts/<str:phone>/send-media/", send_media_message, name="send_media"),
    path("api/contacts/<str:phone>/mark-read/", mark_messages_read, name="mark_read"),
    path("api/products/", get_products_list, name="get_products"),
    path(
        "api/contacts/<str:phone>/send-product/",
        send_product_to_contact,
        name="send_product",
    ),
    path(
        "api/generate-embeddings/",
        generate_embeddings_endpoint,
        name="generate_embeddings",
    ),
]
