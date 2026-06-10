import sys
import os
from typing import List, Optional
import psycopg2  # Reemplazar por supabase si usas su cliente nativo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Alcance (Scope) limitado a solo lectura de archivos de Drive
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

# Rutas resueltas de manera absoluta respecto a la ubicación de este script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
rutaTokens = os.path.abspath(os.path.join(BASE_DIR, '..', 'token.json'))
rutaCredentials = os.path.abspath(os.path.join(BASE_DIR, '..', 'credentials.json'))

# Añadir la raíz del proyecto al sys.path para poder importar db_config
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, '..', '..')))
from db_config import DB_CONFIG

def obtener_servicio_drive():
    """Maneja el flujo de autenticación OAuth2 y retorna el cliente de la API."""
    creds = None
    # El archivo token.json almacena los tokens de acceso y refresco del usuario
    if os.path.exists(rutaTokens):
        creds = Credentials.from_authorized_user_file(rutaTokens, SCOPES)
        
    # Si no hay credenciales válidas disponibles, deja que el usuario inicie sesión.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(rutaCredentials):
                raise FileNotFoundError("Por favor descarga 'credentials.json' desde Google Cloud Console.")
            
            flow = InstalledAppFlow.from_client_secrets_file(rutaCredentials, SCOPES)
            creds = flow.run_local_server(port=0)
            
        # Guarda las credenciales para la próxima ejecución
        with open(rutaTokens, 'w') as token:
            token.write(creds.to_json())

    return build('drive', 'v3', credentials=creds)

def guardar_en_db(id_drive: str, nombre_orig: str, nombre_comp: str, cat_principal: str, url: str):
    """Inserta o actualiza el registro en la base de datos."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        query = """
            INSERT INTO documentos_indexados (id_drive, nombre_original, nombre_compuesto, categoria_principal, url_acceso)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id_drive) DO UPDATE 
            SET nombre_compuesto = EXCLUDED.nombre_compuesto,
                categoria_principal = EXCLUDED.categoria_principal,
                url_acceso = EXCLUDED.url_acceso;
        """
        cursor.execute(query, (id_drive, nombre_orig, nombre_comp, cat_principal, url))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[-] Error al guardar en BD el archivo {nombre_orig}: {e}")

def recorrer_carpeta(service, folder_id: str, primera_carpeta: Optional[str] = None, ruta_actual: List[str] = []):
    """
    Recorre de forma recursiva (DFS) las carpetas de Google Drive.
    Mantiene el estado de la ruta y extrae los metadatos de los archivos.
    """
    print(f"[>] Explorando carpeta ID: {folder_id} | Ruta acumulada: {' / '.join(ruta_actual)}")
    page_token = None

    while True:
        try:
            # Consultamos los elementos hijos directos de la carpeta actual
            query = f"'{folder_id}' in parents and trashed = false"
            fields = "nextPageToken, files(id, name, mimeType, webViewLink)"
            
            response = service.files().list(
                q=query,
                fields=fields,
                pageToken=page_token,
                pageSize=100
            ).execute()
            
            items = response.get('files', [])
            
            for item in items:
                item_id = item['id']
                item_name = item['name']
                item_type = item['mimeType']
                
                if item_type == 'application/vnd.google-apps.folder':
                    # Si estamos en el primer nivel debajo de la raíz, esta es la "primera carpeta"
                    nueva_primera_carpeta = item_name if primera_carpeta is None else primera_carpeta
                    
                    # Agregamos la carpeta actual al stack de la ruta
                    nueva_ruta = ruta_actual + [item_name]
                    
                    # Llamada recursiva hacia la subcarpeta
                    recorrer_carpeta(service, item_id, nueva_primera_carpeta, nueva_ruta)
                    
                else:
                    # Es un archivo (libro, pdf, etc.)
                    # Si el archivo está directo en la raíz sin subcarpetas, la categoría es 'Raíz'
                    cat_final = primera_carpeta if primera_carpeta else "Raíz"
                    
                    # Construimos el nombre compuesto uniendo las carpetas y el nombre original
                    # Reemplazamos espacios por guiones bajos para estandarizar
                    componentes_nombre = ruta_actual + [item_name]
                    nombre_compuesto = "_".join(componentes_nombre).replace(" ", "_")
                    
                    url_acceso = item.get('webViewLink', '')
                    
                    print(f"[+] Archivo encontrado: {item_name}")
                    print(f"    -> Categoría Principal: {cat_final}")
                    print(f"    -> Nombre Compuesto: {nombre_compuesto}")
                    
                    # Persistencia
                    guardar_en_db(item_id, item_name, nombre_compuesto, cat_final, url_acceso)
            
            page_token = response.get('nextPageToken', None)
            if not page_token:
                break
                
        except HttpError as error:
            print(f"[-] Ocurrió un error en la API de Drive: {error}")
            break

if __name__ == '__main__':
    try:
        # ID de la carpeta general en tu Google Drive de donde partirá el flujo
        ID_CARPETA_RAIZ = '1EY6Bm0NXTm85VkVLIC7T4-lilubKDQDV'
        
        print("[*] Iniciando autenticación OAuth2...")
        drive_service = obtener_servicio_drive()
        
        print("[*] Conexión establecida. Iniciando escaneo de archivos...")
        recorrer_carpeta(drive_service, folder_id=ID_CARPETA_RAIZ)
        
        print("[*] ¡Proceso de indexación completado con éxito!")
    except Exception as e:
        print(f"[-] Error crítico en la ejecución: {e}")