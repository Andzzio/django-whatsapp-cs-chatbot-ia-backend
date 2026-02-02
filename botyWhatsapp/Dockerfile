# Usar una imagen base ligera de Python corre en Linux
FROM python:3.11-slim

# Evitar archivos .pyc y buffers en stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Instalar dependencias del sistema necesarias para compilar paquetes de Python
# libpq-dev es para PostgreSQL
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Establecer directorio de trabajo
WORKDIR /app

# Copiar requerimientos e instalar
COPY requirements.txt /app/
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Copiar el código del proyecto
COPY . /app/

# Exponer el puerto 8000
EXPOSE 8000

# Comando por defecto (será sobrescrito o usado por docker-compose)
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
