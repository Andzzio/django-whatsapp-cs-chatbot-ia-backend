from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .models import Contact, Message
from django.conf import settings


@csrf_exempt
def sync_data(request):
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)
    if request.method == "GET":
        response_data = []
        contacts = Contact.objects.all()
        
        for contact in contacts:
            msgs = []
            
            for m in contact.messages.all().order_by("timestamp"):
                msgs.append({
                    "user": "BOTY" if m.is_bot else contact.name,
                    "text": m.text,
                    "time": m.timestamp.strftime("%H:%M"),
                    "is_bot": m.is_bot
                })
            response_data.append({
                "name": contact.name,
                "history": msgs
            })
        return JsonResponse({"contacts": response_data}, safe=False)
    
    return JsonResponse({"error": "Método no permitido"}, status=405)