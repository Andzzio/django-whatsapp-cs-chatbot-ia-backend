import requests
import json


def load_env():
    env_vars = {}
    try:
        # Usar ruta absoluta al archivo .env encontrado
        dotenv_path = "/home/andzzio/Documentos/proyectos/BOTY/BotyWhatsapp/.env"
        with open(dotenv_path, "r") as f:
            print(f"📂 Cargando .env desde: {dotenv_path}")
            for line in f:
                if line.strip() and not line.startswith("#") and "=" in line:
                    key, value = line.strip().split("=", 1)
                    env_vars[key] = value.strip().strip("'").strip('"')
    except Exception as e:
        print(f"⚠️ No se pudo leer .env: {e}")
    return env_vars


env = load_env()
WHATSAPP_API_TOKEN = env.get("WHATSAPP_API_TOKEN")
ID_NUMERO = env.get("ID_NUMERO")
OWNER_PHONE_NUMBER = env.get("OWNER_PHONE_NUMBER")


def test_send_message():
    print("--- INICIANDO TEST DE CONEXIÓN WHATSAPP (Dep-Free) ---")

    if not WHATSAPP_API_TOKEN or not ID_NUMERO or not OWNER_PHONE_NUMBER:
        print(
            "❌ Faltan variables en .env (WHATSAPP_API_TOKEN, ID_NUMERO, OWNER_PHONE_NUMBER)"
        )
        return

    print(f"ID Número: {ID_NUMERO}")
    print(f"Token (primeros 10 chars): {WHATSAPP_API_TOKEN[:10]}...")
    # Sanitizar número para el test
    clean_phone = str(OWNER_PHONE_NUMBER).replace("+", "").replace(" ", "").strip()
    print(f"Enviando a (OWNER): {clean_phone}")

    url = f"https://graph.facebook.com/v21.0/{ID_NUMERO}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }

    # Mensaje de prueba
    data = {
        "messaging_product": "whatsapp",
        "to": clean_phone,
        "type": "text",
        "text": {
            "body": "🔔 TEST CRITICO: Verificación de Credenciales y Formato.",
        },
    }

    try:
        print("\nEnviando solicitud a Meta...")
        response = requests.post(url, headers=headers, json=data, timeout=15)

        print(f"Status Code: {response.status_code}")

        try:
            res_json = response.json()
            print(f"Respuesta JSON:\n{json.dumps(res_json, indent=2)}")
        except Exception:
            print(f"Respuesta Raw: {response.text}")

        if response.status_code == 200:
            print("\n✅ ÉXITO: El token funciona y el mensaje salió.")
        else:
            print("\n❌ FALLO: Meta rechazó la solicitud.")

    except Exception as e:
        print(f"\n❌ ERROR DE CONEXIÓN: {e}")


if __name__ == "__main__":
    test_send_message()
