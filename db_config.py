import os

# Configuración común de la Base de Datos para DocUNI
DB_CONFIG = {
    "dbname": os.environ.get("DB_NAME", "dbd_planchas"),
    "user": os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": os.environ.get("DB_PORT", "5432")
}
