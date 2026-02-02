from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from .models import Snippet
import json


@csrf_exempt
def get_snippets(request):
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method == "GET":
        # Retornamos snippets del token (en este caso usamos el token de auth como identificador de "cuenta" si quisieramos multi-tenant)
        # O simplemente retornamos todos. El usuario pidió "sincronizados y guardados por user token".
        # Usaremos DASH_TOKEN como el identificador del usuario.

        snippets = Snippet.objects.filter(token=settings.DASH_TOKEN).order_by(
            "shortcut"
        )
        data = [
            {"id": s.id, "shortcut": s.shortcut, "content": s.content} for s in snippets
        ]
        return JsonResponse({"snippets": data})
    return JsonResponse({"error": "Method not allowed"}, status=405)


@csrf_exempt
def create_snippet(request):
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method == "POST":
        try:
            data = json.loads(request.body)
            shortcut = data.get("shortcut")
            content = data.get("content")

            if not shortcut or not content:
                return JsonResponse(
                    {"error": "Shortcut and content required"}, status=400
                )

            # Upsert
            snippet, created = Snippet.objects.update_or_create(
                token=settings.DASH_TOKEN,
                shortcut=shortcut,
                defaults={"content": content},
            )

            return JsonResponse(
                {
                    "status": "success",
                    "snippet": {
                        "id": snippet.id,
                        "shortcut": snippet.shortcut,
                        "content": snippet.content,
                    },
                }
            )
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Method not allowed"}, status=405)


@csrf_exempt
def delete_snippet(request, snippet_id):
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method == "DELETE":
        try:
            Snippet.objects.filter(id=snippet_id, token=settings.DASH_TOKEN).delete()
            return JsonResponse({"status": "deleted"})
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Method not allowed"}, status=405)
