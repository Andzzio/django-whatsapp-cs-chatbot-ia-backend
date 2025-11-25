from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .models import Contact, Message
from django.conf import settings
import pytz
import json
from django.utils import timezone
from .views import send_whatsapp_message

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
                    "time": m.timestamp.astimezone(pytz.timezone('America/Lima')).strftime("%H:%M"),
                    "is_bot": m.is_bot
                })
            response_data.append({
                "name": contact.name,
                "phone": contact.phone,
                "is_bot_active": contact.is_bot_active,
                "history": msgs
            })
        return JsonResponse({"contacts": response_data}, safe=False)
    
    return JsonResponse({"error": "Método no permitido"}, status=405)

@csrf_exempt
def toggle_bot_status(request, phone):
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)
    if request.method == "POST":
        try:
            contact = Contact.objects.get(phone=phone)
        except Contact.DoesNotExist:
            return JsonResponse({"error": "Contact not found"}, status=404)
        data = json.loads(request.body)
        is_active = data.get("is_active")
        contact.is_bot_active = is_active
        if is_active == False:
            contact.bot_disabled_at = timezone.now()
        else:
            contact.bot_disabled_at = None
        contact.save()
        return JsonResponse({"status": "success", "is_bot_active": contact.is_bot_active})

@csrf_exempt
def send_message_to_contact(request, phone):
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)
    if request.method == "POST":
        try:
            contact = Contact.objects.get(phone=phone)
            data = json.loads(request.body)
            text = data.get("text")
            if not text or text.strip() == "":
                return JsonResponse({"error": "Text Empty"}, status=400)
            send_whatsapp_message(phone, text)
            return JsonResponse({"status": "success", "message": "sent"})
        except Contact.DoesNotExist:
            return JsonResponse({"error": "Contact not found"}, status=404)