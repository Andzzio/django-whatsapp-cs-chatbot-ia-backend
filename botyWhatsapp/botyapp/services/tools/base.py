from abc import ABC, abstractmethod
from google.genai import types


class BaseTool(ABC):
    """
    Clase base abstracta para todas las herramientas del bot.
    Garantiza que todas las tools tengan una estructura consistente.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Nombre único de la herramienta (ej: 'show_catalog')"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Descripción para el LLM sobre qué hace la herramienta y cuándo usarla"""
        pass

    @property
    @abstractmethod
    def parameters_schema(self) -> types.Schema:
        """Esquema de parámetros esperado por la herramienta (formato Gemini/Google)"""
        pass

    @abstractmethod
    def execute(self, **kwargs):
        """
        Lógica de ejecución de la herramienta.
        Debe devolver un resultado o ejecutar una acción (side-effect).
        """
        pass

    def to_gemini_tool(self) -> types.Tool:
        """Convierte la definición de la herramienta al formato de Gemini SDK"""
        return types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name=self.name,
                    description=self.description,
                    parameters=self.parameters_schema,
                )
            ]
        )
