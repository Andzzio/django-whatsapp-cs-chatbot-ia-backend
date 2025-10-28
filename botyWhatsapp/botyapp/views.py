from django.shortcuts import render
import json
import requests
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from google import genai
from google.genai.types import Tool, FunctionDeclaration, Schema, Type
from . import services

CATALOGO_TOOL = Tool(
    function_declarations=[
        FunctionDeclaration(
            name="consultar_catalogo",
            description="Busca el precio, stock, descripción, o tallas de un producto específico o palabra clave en el catálogo de la empresa. Debe usarse siempre que el usuario pregunte por detalles del producto.",
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "nombre_producto": Schema(
                        type=Type.STRING,
                        description="Nombre exacto o palabra clave a buscar. Debe estar corregido ortográficamente y, si es necesario, traducido al idioma del catálogo (ej. 'dining chair')."
                    )
                },
                required=["nombre_producto"],
            ),
        )
    ]
)

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
                                            model="models/gemini-2.5-flash-lite",
                                            contents=text_body,
                                            config=
                                            {
                                                "system_instruction": settings.SYSTEM_PROMPT,
                                                "tools": [CATALOGO_TOOL]
                                            },
                                        
                                        )
                                        if response.function_calls:
                                            print("SE ESTÁ EJECUTANDO LA LLAMADA DE CONSULTA DE BASE DE DATOS")
                                            funcion_solicitada_object = response.candidates[0].content.parts[0].function_call
                                            call = response.function_calls[0]
                                            nombre_a_buscar = call.args.get("nombre_producto")
                                            
                                            datos_db = services.consultar_catalogo(nombre_a_buscar)
                                            
                                            
                                            full_context_contents = [
                                          
                                                {"role": "user", "parts": [{"text": text_body}]},
                                              
                                                {"role": "model", "parts": [{"functionCall": funcion_solicitada_object}]},

                                               
                                                {"role": "function", "parts": [
                                                    {"functionResponse": 
                                                        {"name": "consultar_catalogo", "response": datos_db}
                                                    }
                                                    ]}
                                                ]
                                            respuesta_final = client.models.generate_content(
                                                model="models/gemini-2.5-flash-lite",
                                                contents=full_context_contents,
                                                config=
                                                {
                                                    "system_instruction": settings.SYSTEM_PROMPT,
                                                    "tools": [CATALOGO_TOOL]
                                                },
                                                
                                            )
                                            response_text = respuesta_final.text
                                        else:
                                            print("SE ESTÁ EJECUTANDO EL MODO SIN CONSULTA DE BASE DE DATOS")
                                            response_text = response.text
                                        
                                        send_whatsapp_message(sender_id, response_text)
                                        print(f"Mensaje enviado:{response_text}")
                                        return JsonResponse({"status": "ok"}, status=200)
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
                            
                                
                                       
