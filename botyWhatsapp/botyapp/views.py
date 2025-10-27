from django.shortcuts import render
import json
import requests
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from google import genai

IA_KEY = settings.IA_TOKEN

client = genai.Client(api_key=IA_KEY)

def send_whatsapp_message(receptor_wsp_id, text_answer):
    headers={
        "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }
    data={
        "messaging_product": "whatsapp",
        "to": receptor_wsp_id,
        "type": "text",
        "text": {
            "body" : text_answer,
        }
    }
    try:
        response = requests.post(settings.WHATSAPP_URL, headers=headers, data=json.dumps(data))
        response.raise_for_status()
        print(f"Mensaje enviado exitosamente: {response.json()}")
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error al enviar mensaje de Whatsapp {e}")
        return None
@csrf_exempt
def whatsapp_webhook(request):
    if request.method == "GET":
        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")
        
        if mode and token:
            if mode == "subscribe" and token == settings.VERIFY_TOKEN:
                print("WEBHOOK_verified")
                return HttpResponse(challenge, status=200)
            else:
                return HttpResponse("Verification token mismatch", status=403)
    elif request.method == "POST":
        try:
            data = json.loads(request.body.decode("utf-8"))
            if "object" in data and "entry" in data:
                for entry in data["entry"]:
                    for change in entry.get("changes", []):
                        if change.get("field") == "messages":
                            value = change.get("value", {})
                            
                            if "messages" in value:
                                print(f"📨 Datos del mensaje: {json.dumps(value, indent=2)}")
                                for message_event in value.get("messages", []):
                                    if message_event.get("type") == "text":
                                    
                                        sender_id = message_event["from"]
                                        text_body = message_event["text"]["body"].lower().strip()

                                        response = client.models.generate_content(
                                        model="models/gemini-robotics-er-1.5-preview",
                                        contents=text_body,
                                        )
                                    
                                        #if "hola" in text_body or "saludo" in text_body:
                                         #   respuesta = "¡Hola! Soy el bot prototipo Boty - Shurumba" 
                                        #else:
                                          #s  respuesta = f"Disculpa no entendí tu mensaje {text_body}"
                                        send_whatsapp_message(sender_id, response.text)
                                        print(f"Mensaje enviado:{response.text}")
                        else:
                            print(f"Campo recibido: {change.get('field')}")
            else:
                print(f" Estructura inesperada: {data}")
            return JsonResponse({"status": "ok"}, status=200)
        except json.JSONDecodeError:
            print(f"Error JSON: {e}")
            return HttpResponse("Invalid JSON", status=400)
        except Exception as e:
            print(f"Error procesando el POST:{e}")
            return HttpResponse("Internal Server Error", status=500)
        
    return HttpResponse("Not Found..", status=404)
                            
                                
                                       
