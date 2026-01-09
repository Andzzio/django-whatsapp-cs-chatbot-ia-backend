"""
ASGI config for botyWhatsapp project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import botyapp.routing

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "botyWhatsapp.settings")

application = ProtocolTypeRouter(
    {
        "http": get_asgi_application(),
        "websocket": AuthMiddlewareStack(
            URLRouter(botyapp.routing.websocket_urlpatterns)
        ),
    }
)
