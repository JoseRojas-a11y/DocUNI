import sys
import os
import json
from typing import List, Optional

# Configurar stdout y stderr para usar UTF-8 y evitar errores de codificación en Windows
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Importar utilidades de checkpoint
from checkpoint_utils import cargar_checkpoint, guardar_checkpoint, truncar_archivo_por_lineas, obtener_argumentos_reset

# Alcance (Scope) limitado a solo lectura de archivos de Drive
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

# Rutas resueltas de manera absoluta respecto a la ubicación de este script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
rutaTokens = os.path.abspath(os.path.join(BASE_DIR, '..', 'token.json'))
rutaCredentials = os.path.abspath(os.path.join(BASE_DIR, '..', 'credentials.json'))
rutaCrawled = os.path.abspath(os.path.join(BASE_DIR, '..', 'archivos_planos', 'crawled_documents.jsonl'))

# Variables globales para control de bloques y checkpoints
TAMANO_BLOQUE = 50
buffer_bloque = []
processed_folders = set()
crawled_file_ids = set()
total_archivos = 0

def obtener_servicio_drive():
    """Maneja el flujo de autenticación OAuth2 y retorna el cliente de la API."""
    creds = None
    if os.path.exists(rutaTokens):
        creds = Credentials.from_authorized_user_file(rutaTokens, SCOPES)
        
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"[*] Advertencia: No se pudo refrescar el token ({e}). Re-autenticando...")
                if os.path.exists(rutaTokens):
                    try:
                        os.remove(rutaTokens)
                    except Exception:
                        pass
                if not os.path.exists(rutaCredentials):
                    raise FileNotFoundError("Por favor descarga 'credentials.json' desde Google Cloud Console.")
                
                flow = InstalledAppFlow.from_client_secrets_file(rutaCredentials, SCOPES)
                creds = flow.run_local_server(port=0)
        else:
            if not os.path.exists(rutaCredentials):
                raise FileNotFoundError("Por favor descarga 'credentials.json' desde Google Cloud Console.")
            
            flow = InstalledAppFlow.from_client_secrets_file(rutaCredentials, SCOPES)
            creds = flow.run_local_server(port=0)
            
        with open(rutaTokens, 'w') as token:
            token.write(creds.to_json())

    return build('drive', 'v3', credentials=creds)

def guardar_bloque_crawler(f_out):
    """Escribe los documentos en el buffer al archivo plano y actualiza el checkpoint."""
    global buffer_bloque, processed_folders, total_archivos
    if not buffer_bloque:
        return

    print(f"[*] Escribiendo bloque de {len(buffer_bloque)} archivos a disco...")
    for registro in buffer_bloque:
        linea = json.dumps(registro, ensure_ascii=False) + '\n'
        f_out.write(linea)
        total_archivos += 1
    
    f_out.flush()
    
    # Actualizar checkpoint
    checkpoint_data = {
        "processed_folders": list(processed_folders),
        "total_archivos": total_archivos
    }
    guardar_checkpoint("crawler", checkpoint_data)
    
    # Limpiar buffer
    buffer_bloque.clear()

def recorrer_carpeta(service, folder_id: str, f_out, primera_carpeta: Optional[str] = None, ruta_actual: List[str] = []):
    """
    Recorre de forma recursiva (DFS) las carpetas de Google Drive.
    Utiliza un buffer de bloques para escribir en f_out y salta carpetas completadas.
    """
    global processed_folders, crawled_file_ids, buffer_bloque
    
    if folder_id in processed_folders:
        print(f"[*] Omitiendo carpeta ID {folder_id} (ya procesada completamente).")
        return

    print(f"[>] Explorando carpeta ID: {folder_id} | Ruta acumulada: {' / '.join(ruta_actual)}")
    page_token = None

    while True:
        try:
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
                    nueva_primera_carpeta = item_name if primera_carpeta is None else primera_carpeta
                    nueva_ruta = ruta_actual + [item_name]
                    # Recursión
                    recorrer_carpeta(service, item_id, f_out, nueva_primera_carpeta, nueva_ruta)
                else:
                    # Si ya está indexado antes del checkpoint, omitir
                    if item_id in crawled_file_ids:
                        continue
                        
                    componentes_nombre = ruta_actual + [item_name]
                    nombre_compuesto = "_".join(componentes_nombre).replace(" ", "_")
                    url_acceso = item.get('webViewLink', '')
                    
                    registro = {
                        "id_drive": item_id,
                        "nombre_compuesto": nombre_compuesto,
                        "url_acceso": url_acceso,
                        "primera_carpeta": primera_carpeta
                    }
                    
                    buffer_bloque.append(registro)
                    crawled_file_ids.add(item_id)
                    
                    if len(buffer_bloque) >= TAMANO_BLOQUE:
                        guardar_bloque_crawler(f_out)
            
            page_token = response.get('nextPageToken', None)
            if not page_token:
                break
                
        except HttpError as error:
            print(f"[-] Ocurrió un error en la API de Drive: {error}")
            break

    # Al terminar la carpeta actual completamente, la agregamos a procesadas
    processed_folders.add(folder_id)

if __name__ == '__main__':
    try:
        ID_CARPETA_RAIZ = '1EY6Bm0NXTm85VkVLIC7T4-lilubKDQDV'
        
        print("============================================================")
        print("DocUNI v3.0 - Rastreador (Crawler) con Checkpoint y Bloques")
        print("============================================================")
        
        reset = obtener_argumentos_reset()
        cp = cargar_checkpoint("crawler", reset)
        
        if cp:
            processed_folders = set(cp.get("processed_folders", []))
            total_archivos = cp.get("total_archivos", 0)
            print(f"[*] Reanudando crawler desde checkpoint:")
            print(f"    - Carpetas ya procesadas: {len(processed_folders)}")
            print(f"    - Archivos previamente rastreados: {total_archivos}")
            
            # Truncar archivo de salida a total_archivos (cantidad de registros)
            truncar_archivo_por_lineas(rutaCrawled, total_archivos)
            
            # Cargar ids de archivos rastreados en memoria para evitar duplicados en la reanudación
            if os.path.exists(rutaCrawled) and total_archivos > 0:
                with open(rutaCrawled, 'r', encoding='utf-8', errors='ignore') as f:
                    for _ in range(total_archivos):
                        line = f.readline()
                        if not line:
                            break
                        try:
                            reg = json.loads(line.strip())
                            crawled_file_ids.add(reg["id_drive"])
                        except Exception:
                            pass
                print(f"    - Cargados {len(crawled_file_ids)} IDs únicos en memoria para evitar duplicados.")
        else:
            print("[*] Iniciando rastreo completo desde cero...")
            processed_folders = set()
            total_archivos = 0
            # Crear/sobreescribir el archivo a vacío
            truncar_archivo_por_lineas(rutaCrawled, 0)
            
        print("[*] Iniciando autenticación OAuth2...")
        drive_service = obtener_servicio_drive()
        
        print(f"[*] Conexión establecida. Escribiendo resultados en: {rutaCrawled}")
        
        # Abrimos en modo append de texto
        with open(rutaCrawled, 'a', encoding='utf-8') as f_out:
            recorrer_carpeta(drive_service, folder_id=ID_CARPETA_RAIZ, f_out=f_out)
            # Guardar cualquier remanente en el buffer
            guardar_bloque_crawler(f_out)
        
        print("============================================================")
        print(f"[*] ¡Proceso de rastreo finalizado con éxito!")
        print(f"    - Total de archivos indexados: {total_archivos}")
        print("============================================================")
    except Exception as e:
        print(f"[-] Error crítico en la ejecución del crawler: {e}")
        sys.exit(1)