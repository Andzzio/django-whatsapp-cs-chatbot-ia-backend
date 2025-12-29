from google import genai
from django.conf import settings
from logger import log
from botyapp.services.users import get_context, save_user_data, start_timer
from botyapp.services.tools.catalog_tool import CatalogTool
from botyapp.services.tools.product_tool import RecommendProductsTool
from botyapp.services.tools.contact_tool import ContactTool
from botyapp.services.whatsapp import (
    send_whatsapp_message,
    download_audio,
    download_and_optimize_image,
    get_whatsapp_media_url,
)
from botyapp.services.crm_service import CRMService
from google.genai import types


class LLMEngine:
    def __init__(self, api_key):
        self.client = genai.Client(api_key=api_key)
        self.tools = [
            CatalogTool(),
            RecommendProductsTool(),
            ContactTool(),
        ]
        self._tool_map = {t.name: t for t in self.tools}

    def _get_gemini_tools(self):
        return [t.to_gemini_tool() for t in self.tools]

    def _build_system_instruction(self, crm_context=""):
        # El contexto del catálogo ahora se podría inyectar dinámicamente,
        # pero por compatibilidad traemos la función legacy o la inyectamos aquí.
        # Para Clean Arch, idealmente el engine no depende de 'catalog.py' directamente para el prompt,
        # pero por ahora lo mantendremos funcional.
        from botyapp.services.catalog import get_catalog_context

        catalog_context = get_catalog_context(settings.CATALOG_ID)

        return (
            f"{settings.SYSTEM_PROMPT}\n\n"
            "--- DIRECTRICES DE USO DE HERRAMIENTAS (CRÍTICO) ---\n"
            "1. NO NARRES TUS ACCIONES: Nunca escribas texto como '[SISTEMA:...]' o 'He ejecutado...'.\n"
            "2. USA LA HERRAMIENTA NATIVA: Si aplica, GENERA UN FUNCTION CALL.\n"
            "3. PRIORIDAD AL CATÁLOGO: Si el usuario muestra interés en ver, comprar, buscar, modelos, ropa, prendas o fotos, DEBES llamar a 'show_catalog' INMEDIATAMENTE. No preguntes, solo muestra.\n"
            "--- PERFIL DEL USUARIO (CRM) ---\n"
            f"{crm_context}\n"
            "--- CONOCIMIENTO DEL NEGOCIO ---\n"
            f"{catalog_context}"
        )

    def process_message(self, sender_id, text_body, media_id=None, media_type="image"):
        # 1. Historial
        history = get_context(sender_id)
        if not isinstance(history, list):
            history = []

        # 2. Construir User Message
        current_parts = []
        if media_id:
            media_url = get_whatsapp_media_url(media_id)
            if media_type == "image" and media_url:
                img_bytes = download_and_optimize_image(media_url)
                if img_bytes:
                    current_parts.append(
                        types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
                    )
                    if not text_body:
                        text_body = "Describe esta imagen."
            elif media_type == "audio" and media_url:
                audio_bytes = download_audio(media_url)
                if audio_bytes:
                    current_parts.append(
                        types.Part.from_bytes(data=audio_bytes, mime_type="audio/ogg")
                    )
                    if not text_body:
                        text_body = "Atiende este audio."

        current_parts.append({"text": text_body})
        user_turn = {"role": "user", "parts": current_parts}

        gemini_input = history + [user_turn]
        if len(gemini_input) > 20:
            gemini_input = gemini_input[-20:]

        # 3. Preparar Contexto de Ventas (CRM)
        crm_info = ""
        try:
            from botyapp.models import Contact

            contact = Contact.objects.get(phone=sender_id)
            crm_info = (
                f"CLIENTE: {contact.first_name or 'Usuario'}\n"
                f"TAGS: {contact.tags}\n"
                f"SCORE: {contact.lead_score}\n"
                f"ULTIMA INTENCION: {contact.last_intent}\n"
                "Instrucción de Venta: Si es VIP (>50 puntos) ofrece trato premium. Si tiene 'interes_catalogo', enfócate en cerrar venta."
            )
        except Exception:
            pass

        # 4. Llamada al Modelo
        try:
            response = self.client.models.generate_content(
                model="models/gemini-flash-lite-latest",
                contents=gemini_input,
                config={
                    "system_instruction": self._build_system_instruction(crm_info),
                    "tools": self._get_gemini_tools(),
                },
            )
        except Exception as e:
            log.error(f"❌ Error Gemini Engine: {e}")
            return

        if not response:
            return

        # 4. Procesar Respuesta (Tool Execution vs Text)
        has_execution = False
        tool_name_executed = None

        candidates = response.candidates
        if candidates and candidates[0].content and candidates[0].content.parts:
            for part in candidates[0].content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    fname = part.function_call.name
                    fargs = part.function_call.args

                    if fname in self._tool_map:
                        has_execution = True
                        tool_name_executed = fname
                        log.info(f"⚙️ Ejecutando herramienta: {fname}")
                        self._tool_map[fname].execute(sender_id, **fargs)
                    else:
                        log.warning(f"⚠️ Tool desconocida solicitada: {fname}")

            if response.text and not has_execution:
                final_text = response.text.replace("CONTEXTO:", "").strip()
                if final_text:
                    send_whatsapp_message(sender_id, final_text)

        # 5. Persistencia & CRM INTELLIGENCE 🧠
        try:
            # CRM Analysis
            CRMService.analyze_interaction(
                phone=sender_id, text=text_body, tool_used=tool_name_executed
            )

            model_parts = []
            if response.text:
                model_parts.append({"text": response.text})

            # Si hubo tool call, podriamos guardar un log simulado o el tool use real
            # Por compatibilidad con users.py actual, guardamos algo representativo
            if has_execution:
                # Ojo: users.py ya tiene filtros, pero mantenemos limpio
                pass

            # NOTA: Guardar respuesta del modelo exacta es complejo con function calls
            # en historial de chat simple. Guardamos el texto si existe.
            if model_parts:
                model_turn = {"role": "model", "parts": model_parts}
                save_user_data(
                    phone_number=sender_id, context=gemini_input + [model_turn]
                )
        except Exception as e:
            log.error(f"Error saving context: {e}")

        # 6. Timer
        start_timer(sender_id)


# Instancia global
llm_engine = LLMEngine(settings.IA_TOKEN)
