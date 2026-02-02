import re
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
            "3. PROHIBIDO LISTAR TEXTO: Si el usuario pide productos (ej: 'busco pantalones'), NUNCA escribas una lista. DEBES ejecutar 'recommend_products' para mostrar la UI visual.\n"
            "4. RESPUESTAS CORTAS: El cliente quiere IMAGEN, PRECIO y TALLA. No adivines, no uses texto de relleno. Ve al grano.\n"
            "5. FORMATO DE TEXTO: Usa Markdown para tus respuestas. Usa *negrita* para resaltar claves, _cursiva_ para tonos suaves, > para citas y listas - para enumerar.\n"
            "--- PERFIL DEL USUARIO (CRM) ---\n"
            f"{crm_context}\n"
            "--- CONOCIMIENTO DEL NEGOCIO ---\n"
            f"{catalog_context}"
        )

    def _clean_history(self, history):
        """Limpia el historial de alucinaciones antiguas pero mantiene tool calls."""
        if not isinstance(history, list):
            return []

        clean_history = []
        for turn in history:
            try:
                if isinstance(turn, dict):
                    new_parts = []
                    for p in turn.get("parts", []):
                        # Limpieza de alucinaciones antiguas [SISTEMA...]
                        if isinstance(p, dict) and "text" in p:
                            c_text = re.sub(
                                r"\[SISTEMA:.*?\]",
                                "",
                                p["text"],
                                flags=re.IGNORECASE | re.DOTALL,
                            )
                            if c_text.strip():
                                new_parts.append({"text": c_text})
                        # Importante: Mantener function_calls y function_responses
                        elif isinstance(p, dict) and (
                            "function_call" in p or "function_response" in p
                        ):
                            new_parts.append(p)
                        # Objetos genai.types
                        else:
                            new_parts.append(p)

                    if new_parts:
                        clean_history.append({"role": turn["role"], "parts": new_parts})
                else:
                    clean_history.append(turn)
            except Exception:
                clean_history.append(turn)
        return clean_history

    def _run_execution_loop(
        self, sender_id, initial_gemini_input, crm_info, text_body_context=None
    ):
        """
        Núcleo unificado de ejecución: Pensar -> Ejecutar -> Repensar.
        Retorna el TEXTO FINAL de la respuesta.
        """
        # Shallow copy is safer for complex objects like genai types
        gemini_input = list(initial_gemini_input)
        final_text_response = None
        should_stop_conversation_early = (
            False  # Flag para detener si un tool ya respondió todo
        )

        # Maximos turnos de "pensamiento" (Chain of Thought/Tools)
        max_tool_turns = 3

        for _ in range(max_tool_turns):
            try:
                # LLAMADA AL MODELO
                response = self.client.models.generate_content(
                    model="models/gemini-flash-lite-latest",
                    contents=gemini_input,
                    config={
                        "system_instruction": self._build_system_instruction(crm_info),
                        "tools": self._get_gemini_tools(),
                    },
                )

                if not response or not response.candidates:
                    break

                candidate = response.candidates[0]

                # Caso A: El modelo responde con TEXTO FINAL
                has_function_call = False
                for part in candidate.content.parts:
                    if hasattr(part, "function_call") and part.function_call:
                        has_function_call = True
                        break

                # Si no hay llamadas a función, capturamos texto y salimos
                if not has_function_call:
                    final_text_response = response.text
                    break  # EXITO: Fin del bucle

                # Caso B: El modelo pide EJECUTAR TOOL(s)
                # Agregamos la petición del sistema al historial (Model Turn)
                gemini_input.append(candidate.content)

                # Procesar Function Calls
                tool_outputs = []
                for part in candidate.content.parts:
                    if hasattr(part, "function_call") and part.function_call:
                        fname = part.function_call.name
                        fargs = part.function_call.args

                        log.info(f"⚙️ LLM Tool Call Request: {fname}({fargs})")

                        # Ejecutar Tool Real
                        tool_result = {"error": "Tool not found"}
                        if fname in self._tool_map:
                            log.info(f"⚙️ Ejecutando herramienta: {fname}")
                            try:
                                tool_result = self._tool_map[fname].execute(
                                    sender_id, **fargs
                                )
                                if fname == "show_catalog":
                                    should_stop_conversation_early = True
                            except Exception as e:
                                log.error(f"❌ Error ejecutando tool {fname}: {e}")
                                tool_result = {"error": str(e)}

                        # Crear Response Part
                        tool_outputs.append(
                            types.Part.from_function_response(
                                name=fname, response=tool_result
                            )
                        )

                        # CRM Analytics
                        if text_body_context:
                            CRMService.analyze_interaction(
                                phone=sender_id, text=text_body_context, tool_used=fname
                            )

                # Si obtuvimos resultados, los agregamos como un turno de USUARIO (Function Response)
                if tool_outputs:
                    log.debug(
                        f"📤 Enviando {len(tool_outputs)} resultados de tool al modelo."
                    )
                    gemini_input.append({"role": "user", "parts": tool_outputs})
                    # ALERTA: No salimos del loop, volvemos al inicio para que el modelo vea el resultado

                    # ⚠️ SUPPRESSION LOGIC (SINGLE BUBBLE ARCHITECTURE)
                    # Si alguna herramienta ya envió una "interactive_response" (como CatalogTool),
                    # ABORTAMOS el ciclo para que el LLM no añada texto redundante.
                    # 'tool_name_executed' estaba definido en el scope superior (línea 123) y se actualiza en el loop.
                    # Sin embargo, si hubo multiples tools, nos interesa la última o cualquiera que sea terminadora.

                    if should_stop_conversation_early:
                        log.info(
                            "🚫 Deteniendo loop LLM porque show_catalog ya envió la respuesta unificada."
                        )
                        return None  # Retorna None para que NO se envíe nada más por texto plano

                else:
                    break  # Algo raro pasó

            except Exception as e:
                log.error(f"❌ Error CRÍTICO en Loop LLM: {e}")
                break

        return final_text_response

    def _prepare_inputs(self, sender_id, text_body, media_id=None, media_type="image"):
        """Prepara el historial y los inputs para el modelo."""
        # 1. Historial
        history = get_context(sender_id)
        history = self._clean_history(history)

        # 2. Construir User Message Actual
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

        # Fallback vacío
        if not text_body and not media_id:
            text_body = "..."

        if text_body:
            current_parts.append({"text": text_body})

        user_turn = {"role": "user", "parts": current_parts}

        # Contexto limitado
        gemini_input = history[-15:] + [user_turn]

        # 3. CRM Info
        crm_info = ""
        try:
            from botyapp.models import Contact

            contact = Contact.objects.get(phone=sender_id)
            crm_info = (
                f"CLIENTE: {contact.first_name or 'Usuario'}\n"
                f"TAGS: {contact.tags}\n"
                f"SCORE: {contact.lead_score}\n"
                f"ULTIMA INTENCION: {contact.last_intent}\n"
            )
        except Exception:
            pass

        return gemini_input, crm_info, history, user_turn

    def process_message(self, sender_id, text_body, media_id=None, media_type="image"):
        """Punto de entrada principal para procesamiento completo (Fallback/Directo)."""
        gemini_input, crm_info, history, user_turn = self._prepare_inputs(
            sender_id, text_body, media_id, media_type
        )

        # EJECUTAR BUCLE UNIFICADO
        final_text = self._run_execution_loop(
            sender_id, gemini_input, crm_info, text_body
        )

        # Envío y Persistencia
        if final_text:
            text_clean = final_text.replace("CONTEXTO:", "").strip()
            text_clean = re.sub(r"\[SISTEMA:.*?\]", "", text_clean, flags=re.IGNORECASE)

            if text_clean and len(text_clean) > 1:
                send_whatsapp_message(sender_id, text_clean)

            # Persistencia simplificada
            try:
                model_turn = {"role": "model", "parts": [{"text": text_clean}]}
                save_user_data(
                    phone_number=sender_id, context=history + [user_turn, model_turn]
                )

                # CRM Analysis Final
                CRMService.analyze_interaction(
                    phone=sender_id, text=text_body, intent_label="processed_with_loop"
                )
            except Exception as e:
                log.error(f"⚠️ Warn guardando historial: {e}")

        # 6. Timer
        start_timer(sender_id)

    def _generate_smart_response(
        self, sender_id, text_body, media_id=None, media_type="image"
    ):
        """
        Genera respuesta usando el MISMO bucle de tools que process_message.
        Usado por SalesFlow para obtener la respuesta final enriquecida con tools.
        """
        gemini_input, crm_info, _, _ = self._prepare_inputs(
            sender_id, text_body, media_id, media_type
        )

        # EJECUTAR BUCLE UNIFICADO
        final_text = self._run_execution_loop(
            sender_id, gemini_input, crm_info, text_body
        )

        if final_text:
            return final_text
        return "¿En qué puedo ayudarte?"


# Instancia global
llm_engine = LLMEngine(settings.IA_TOKEN)
