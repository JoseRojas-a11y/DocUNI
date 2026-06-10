# Imagen base oficial de Python
FROM python:3.11-slim

# Evitar que Python escriba archivos .pyc y habilitar buffering de logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Establecer directorio de trabajo
WORKDIR /app

# Copiar el archivo de requerimientos e instalar dependencias
COPY aplicativo/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copiar la configuración global de la base de datos
COPY db_config.py /app/db_config.py

# Copiar los archivos de la aplicación (app.py, templates, static)
COPY aplicativo/ /app/

# Exponer el puerto del servidor Flask
EXPOSE 5000

# Comando para arrancar el servidor
CMD ["python", "app.py"]
