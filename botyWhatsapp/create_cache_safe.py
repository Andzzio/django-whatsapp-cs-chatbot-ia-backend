import os
import sys
import django
from django.core.management import call_command

# Configurar entorno Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "botyWhatsapp.settings")
django.setup()

def create_cache_table():
    print("🔄 Verificando tabla de caché...")
    try:
        call_command('createcachetable')
        print("✅ Tabla de caché creada exitosamente.")
    except Exception as e:
        error_msg = str(e).lower()
        # Si el error dice que ya existe, lo ignoramos
        if "already exists" in error_msg or "ya existe" in error_msg:
            print("ℹ️ La tabla de caché ya existe. Omitiendo.")
        else:
            print(f"⚠️ Error al crear tabla de caché: {e}")
            # No lanzamos error para no detener el deploy
            
if __name__ == "__main__":
    create_cache_table()
