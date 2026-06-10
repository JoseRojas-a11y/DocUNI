import sys
import io
import re
import psycopg2
import os
import threading
import signal
import socket 
import time
import random
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed
from tika import parser
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError

# Configurar un timeout global de 3 minutos para evitar que descargas lentas aborten
socket.setdefaulttimeout(180) 

os.environ['TIKA_STARTUP_MAX_HEAP_SIZE'] = '6G'

lock_escritura = threading.Lock()
evento_parada = threading.Event()

# Rutas resueltas de manera absoluta respecto a la ubicación de este script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
rutaTokens = os.path.abspath(os.path.join(BASE_DIR, '..', 'token.json'))
archivo_ingesta = os.path.abspath(os.path.join(BASE_DIR, '..', 'archivos_planos', 'ingesta_intermedia_uni.txt'))

# Añadir la raíz del proyecto al sys.path para poder importar db_config
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, '..', '..')))
from db_config import DB_CONFIG

def manejador_interrupcion(sig, frame):
    if not evento_parada.is_set():
        print("\n\n[!] SEÑAL DE PARADA DETECTADA (Ctrl+C) [!]")
        print("[!] Cancelando descargas en cola para DocuUNI...")
        print("[*] Espera a que los hilos activos terminen...")
        evento_parada.set()
    else:
        print("\n[!] FORZANDO CIERRE DE EMERGENCIA.")
        sys.exit(1)

signal.signal(signal.SIGINT, manejador_interrupcion)

def obtener_credenciales():
    """NUEVO: Solo devuelve el objeto de credenciales, no el servicio construido."""
    return Credentials.from_authorized_user_file(rutaTokens, ['https://www.googleapis.com/auth/drive.readonly'])

def extraer_palabras(texto: str) -> list[str]:
    if not texto:
        return []
    texto_limpio = texto.lower()
    return re.findall(r'\b[a-záéíóúñ0-9]+\b', texto_limpio)

def descargar_contenido_drive(service, file_id: str) -> bytes:
    """Descarga con Retroceso Exponencial para evadir bloqueos de Google Drive."""
    max_intentos = 4 # Aumentamos a 4 intentos
    chunk_size_5mb = 5 * 1024 * 1024  
    
    for intento in range(max_intentos):
        if evento_parada.is_set():
            return None
            
        try:
            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request, chunksize=chunk_size_5mb)
            done = False
            while not done:
                status, done = downloader.next_chunk()
            return fh.getvalue()
            
        except HttpError as error:
            if 'fileNotDownloadable' in str(error) or error.resp.status == 403:
                try:
                    request = service.files().export_media(fileId=file_id, mimeType='application/pdf')
                    fh = io.BytesIO()
                    downloader = MediaIoBaseDownload(fh, request, chunksize=chunk_size_5mb)
                    done = False
                    while not done:
                        status, done = downloader.next_chunk()
                    return fh.getvalue()
                except Exception as e:
                    # Derivamos a la lógica general de reintentos
                    raise e 
            else:
                print(f"[-] Error de API en Drive para {file_id}: {error}")
                return None
                
        except Exception as e:
            error_str = str(e).lower()
            
            # Ampliamos la red de captura para incluir Timeout, EOF, SSL y WinError 10054
            errores_red = ['timed out', 'timeout', 'eof', '10054', 'ssl']
            
            if any(palabra in error_str for palabra in errores_red):
                # Fórmula de Exponential Backoff con Jitter (Aleatoriedad)
                # Intento 0: ~2s | Intento 1: ~4s | Intento 2: ~8s | Intento 3: ~16s
                tiempo_espera = (2 ** (intento + 1)) + random.uniform(0.5, 1.5)
                
                print(f"[*] Interrupción de red en {file_id}. Google Drive cerró la conexión.")
                print(f"    -> Aplicando Backoff: Reintentando en {tiempo_espera:.2f} segundos... ({intento + 1}/{max_intentos})")
                
                time.sleep(tiempo_espera)
                continue # Vuelve al inicio del bucle 'for'
            else:
                # Si es un error diferente (ej. disco lleno, memoria), abortamos
                print(f"[-] Error crítico imprevisto en {file_id}: {e}")
                return None
                
    print(f"[-] Fallo definitivo en {file_id} después de {max_intentos} intentos. Requiere revisión manual.")
    return None

def marcar_como_procesado(id_drive: str):
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        query = "UPDATE documentos_indexados SET procesado = TRUE WHERE id_drive = %s;"
        cursor.execute(query, (id_drive,))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[-] Error en BD: {e}")

# NUEVO: Recibe 'creds' en lugar de 'drive_service'
def procesar_un_documento(doc, creds):
    if evento_parada.is_set():
        return f"[-] Cancelado en cola: {doc[1]}"
    
    id_drive, nombre_compuesto = doc
    
    # NUEVO: El hilo construye su PROPIO servicio de Drive aislado de los demás hilos
    hilo_drive_service = build('drive', 'v3', credentials=creds)
    
    nombre_limpio = nombre_compuesto.replace('_', ' ')
    palabras_nombre = extraer_palabras(nombre_limpio)
    
    if evento_parada.is_set():
        return f"[-] Abortado antes de descargar: {doc[1]}"

    contenido_binario = descargar_contenido_drive(hilo_drive_service, id_drive)
    
    if contenido_binario:
        parsed = parser.from_buffer(contenido_binario)
        texto_crudo = parsed.get("content", "")
        palabras_contenido = extraer_palabras(texto_crudo)
        
        bloque_palabras = " ".join(palabras_nombre + palabras_contenido)
        linea_texto = f"{id_drive}|{bloque_palabras}\n"
        
        with lock_escritura:
            with open(archivo_ingesta, 'a', encoding='utf-8') as archivo_plano:
                archivo_plano.write(linea_texto)
                archivo_plano.flush()
                
            marcar_como_procesado(id_drive) 
            
        return f"[+] {nombre_compuesto} procesado con éxito."
    return f"[-] Falló la descarga de {nombre_compuesto}."

def motor_ingesta_uni():
    print("[*] Inicializando y calentando servidor Apache Tika para DocuUNI...")
    from tika import initVM
    initVM()
    
    # NUEVO: Calentamiento de Tika. Lo forzamos a procesar un buffer vacío
    # para que la máquina virtual de Java arranque completamente antes de los hilos.
    parser.from_buffer(b"Warmup")
    print("[*] Tika Server listo y corriendo.")

    # Obtenemos las credenciales para pasarlas a los hilos
    creds = obtener_credenciales()
    
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id_drive, nombre_compuesto 
        FROM documentos_indexados 
        WHERE procesado = FALSE
    """)
    documentos = cursor.fetchall()
    cursor.close()
    conn.close()
    
    if not documentos:
        print("[*] No hay documentos pendientes por procesar.")
        return

    MAX_HILOS = 15
    print(f"[*] Iniciando pool de {MAX_HILOS} hilos concurrentes aislados...")

    executor = ThreadPoolExecutor(max_workers=MAX_HILOS)
    
    # NUEVO: Enviamos 'creds' a la función del hilo
    futuros = [executor.submit(procesar_un_documento, doc, creds) for doc in documentos]
    
    for futuro in as_completed(futuros):
        if evento_parada.is_set() and "Cancelado" in str(futuro.result()):
            continue
        try:
            print(futuro.result())
        except Exception as e:
            print(f"[-] Un hilo generó una excepción: {e}")
            
    print("\n[*] Cerrando conexiones y limpiando hilos...")
    executor.shutdown(wait=True, cancel_futures=evento_parada.is_set())
    
    if evento_parada.is_set():
        print("[*] Proceso abortado de forma segura.")
    else:
        print("[*] Lote completado al 100%.")

if __name__ == '__main__':
    motor_ingesta_uni()