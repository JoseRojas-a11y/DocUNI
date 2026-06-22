import sys
import io
import re
import json
import os

# Configurar el path del proyecto para resolver el paquete proceso_batch
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROYECTO_RAIZ = os.path.abspath(os.path.join(BASE_DIR, '..', '..'))
if PROYECTO_RAIZ not in sys.path:
    sys.path.insert(0, PROYECTO_RAIZ)

# Configurar stdout y stderr para usar UTF-8 y evitar errores de codificación en Windows
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass
import threading
import signal
import socket
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from tika import parser
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError

# Importar utilidades de checkpoint
from checkpoint_utils import cargar_checkpoint, guardar_checkpoint, truncar_archivo_por_lineas, obtener_argumentos_reset

# Configurar un timeout global de 3 minutos para evitar que descargas lentas aborten
socket.setdefaulttimeout(180)

os.environ['TIKA_STARTUP_MAX_HEAP_SIZE'] = '6G'

evento_parada = threading.Event()

# Rutas resueltas de manera absoluta respecto a la ubicación de este script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
rutaTokens = os.path.abspath(os.path.join(BASE_DIR, '..', 'token.json'))

rutaCrawled = os.path.abspath(os.path.join(BASE_DIR, '..', 'archivos_planos', 'crawled_documents.jsonl'))
rutaDocIndexados = os.path.abspath(os.path.join(BASE_DIR, '..', 'archivos_planos', 'documentos_indexados.jsonl'))
rutaPaginasDoc = os.path.abspath(os.path.join(BASE_DIR, '..', 'archivos_planos', 'paginas_documento.jsonl'))
rutaIndiceUni = os.path.abspath(os.path.join(BASE_DIR, '..', 'archivos_planos', 'indice_invertido_uni.jsonl'))

# Importación diferida del módulo OCR (solo se carga si se necesita)
ocr_engine = None

def cargar_modulo_ocr():
    """Carga el módulo OCR de forma diferida para evitar penalizar el arranque."""
    global ocr_engine
    if ocr_engine is None:
        print("[*] Cargando módulo OCR (EasyOCR + OpenCV)...")
        try:
            from proceso_batch.procesos import ocr_engine as _ocr
            ocr_engine = _ocr
            print("[*] Módulo OCR cargado exitosamente.")
        except Exception as e:
            print(f"[-] Error crítico al cargar el módulo OCR (ej. modelos no descargados/falta de internet): {e}")
            raise e
    return ocr_engine


def manejador_interrupcion(sig, frame):
    if not evento_parada.is_set():
        print("\n\n[!] SEÑAL DE PARADA DETECTADA (Ctrl+C) [!]")
        print("[!] Cancelando descargas en cola para DocuUNI...")
        evento_parada.set()
    else:
        print("\n[!] FORZANDO CIERRE DE EMERGENCIA.")
        sys.exit(1)

signal.signal(signal.SIGINT, manejador_interrupcion)


def obtener_credenciales():
    """Solo devuelve el objeto de credenciales, no el servicio construido."""
    if not os.path.exists(rutaTokens):
        raise FileNotFoundError("No se encontró token.json. Por favor ejecuta crawler.py primero.")
    return Credentials.from_authorized_user_file(rutaTokens, ['https://www.googleapis.com/auth/drive.readonly'])


def extraer_palabras(texto: str) -> list[str]:
    """Normaliza y tokeniza el texto: minúsculas + solo alfanuméricos en español."""
    if not texto:
        return []
    texto_limpio = texto.lower()
    return re.findall(r'\b[a-záéíóúñ0-9]+\b', texto_limpio)


def descargar_contenido_drive(service, file_id: str) -> bytes:
    """Descarga con Retroceso Exponencial para evadir bloqueos de Google Drive."""
    max_intentos = 4
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
                    raise e
            else:
                print(f"[-] Error de API en Drive para {file_id}: {error}")
                return None

        except Exception as e:
            error_str = str(e).lower()
            errores_red = ['timed out', 'timeout', 'eof', '10054', 'ssl']

            if any(palabra in error_str for palabra in errores_red):
                tiempo_espera = (2 ** (intento + 1)) + random.uniform(0.5, 1.5)
                print(f"[*] Interrupción de red en {file_id}. Google Drive cerró la conexión.")
                print(f"    -> Aplicando Backoff: Reintentando en {tiempo_espera:.2f} segundos... ({intento + 1}/{max_intentos})")
                time.sleep(tiempo_espera)
                continue
            else:
                print(f"[-] Error crítico imprevisto en {file_id}: {e}")
                return None

    print(f"[-] Fallo definitivo en {file_id} después de {max_intentos} intentos.")
    return None


def fragmentar_texto_por_filas(texto_crudo: str) -> list[str]:
    """
    Fragmenta el texto extraído en filas (líneas) usando el carácter de salto de línea (\n).
    Si hay \n consecutivos, solo se considera como uno (es decir, omitimos líneas vacías).
    """
    if not texto_crudo:
        return []

    filas = texto_crudo.split('\n')
    filas_limpias = [f.strip() for f in filas if f.strip()]
    return filas_limpias


def extraer_texto_con_ocr(contenido_binario: bytes) -> list[str]:
    """
    Ruta B: Extracción por OCR para imágenes y PDFs escaneados.
    """
    ocr = cargar_modulo_ocr()
    paginas_texto = []

    try:
        from pdf2image import convert_from_bytes
        imagenes = convert_from_bytes(contenido_binario, dpi=300)

        for i, imagen_pil in enumerate(imagenes):
            img_procesada = ocr.preprocesar_imagen_pil(imagen_pil)
            texto = ocr.extraer_texto_ocr(img_procesada)
            paginas_texto.append(texto)
            print(f"        -> OCR página {i + 1}/{len(imagenes)} completada")

        if paginas_texto:
            return paginas_texto
    except Exception:
        pass

    try:
        img_procesada = ocr.preprocesar_imagen(contenido_binario)
        texto = ocr.extraer_texto_ocr(img_procesada)
        paginas_texto.append(texto)
    except Exception as e:
        print(f"    [-] Error en OCR de imagen directa: {e}")
        paginas_texto.append("")

    return paginas_texto


def procesar_un_documento_tika(doc: dict, creds) -> dict:
    """
    Ruta A: Intenta procesar un documento utilizando únicamente Apache Tika.
    Si Tika falla (no extrae texto o lanza excepción), no ejecuta OCR en esta fase,
    sino que retorna success=False con necesita_ocr=True.
    """
    if evento_parada.is_set():
        return {"success": False, "error": "Cancelado en cola"}

    id_drive = doc["id_drive"]
    nombre_compuesto = doc["nombre_compuesto"]
    url_acceso = doc["url_acceso"]
    primera_carpeta = doc.get("primera_carpeta")

    hilo_drive_service = build('drive', 'v3', credentials=creds)

    if evento_parada.is_set():
        return {"success": False, "error": "Abortado antes de descarga"}

    contenido_binario = descargar_contenido_drive(hilo_drive_service, id_drive)
    if not contenido_binario:
        return {"success": False, "error": f"Fallo al descargar {nombre_compuesto}"}

    filas_texto = []

    try:
        parsed = parser.from_buffer(contenido_binario)
        texto_crudo = parsed.get("content", "")

        if texto_crudo and texto_crudo.strip():
            filas_texto = fragmentar_texto_por_filas(texto_crudo)
        else:
            raise ValueError("Tika no extrajo texto")

    except Exception as e:
        return {
            "success": False,
            "error": f"Tika falló: {e}",
            "necesita_ocr": True,
            "doc": doc
        }

    filas_list = []
    for numero_fila, texto_fila in enumerate(filas_texto, 1):
        if not texto_fila.strip():
            continue

        # Inyectar el nombre compuesto en la primera fila
        if numero_fila == 1:
            nombre_limpio = nombre_compuesto.replace('_', ' ')
            palabras_nombre = " ".join(extraer_palabras(nombre_limpio))
            texto_fila = palabras_nombre + " " + texto_fila

        filas_list.append({
            "numero_fila": numero_fila,
            "texto_fila": texto_fila
        })

    if not filas_list:
        return {
            "success": False,
            "error": "Texto extraído por Tika vacío tras formatear",
            "necesita_ocr": True,
            "doc": doc
        }

    return {
        "success": True,
        "id_drive": id_drive,
        "nombre_compuesto": nombre_compuesto,
        "url_acceso": url_acceso,
        "primera_carpeta": primera_carpeta,
        "ruta_extraccion": "Tika",
        "filas": filas_list
    }


def procesar_un_documento_ocr(doc: dict, creds) -> dict:
    """
    Ruta B: Procesa un documento descargándolo y pasándolo por el motor OCR local (EasyOCR + OpenCV).
    """
    if evento_parada.is_set():
        return {"success": False, "error": "Cancelado en cola"}

    id_drive = doc["id_drive"]
    nombre_compuesto = doc["nombre_compuesto"]
    url_acceso = doc["url_acceso"]
    primera_carpeta = doc.get("primera_carpeta")

    hilo_drive_service = build('drive', 'v3', credentials=creds)

    if evento_parada.is_set():
        return {"success": False, "error": "Abortado antes de descarga"}

    contenido_binario = descargar_contenido_drive(hilo_drive_service, id_drive)
    if not contenido_binario:
        return {"success": False, "error": f"Fallo al descargar {nombre_compuesto}"}

    paginas_texto = extraer_texto_con_ocr(contenido_binario)
    filas_texto = fragmentar_texto_por_filas("\n".join(paginas_texto))

    filas_list = []
    for numero_fila, texto_fila in enumerate(filas_texto, 1):
        if not texto_fila.strip():
            continue

        # Inyectar el nombre compuesto en la primera fila
        if numero_fila == 1:
            nombre_limpio = nombre_compuesto.replace('_', ' ')
            palabras_nombre = " ".join(extraer_palabras(nombre_limpio))
            texto_fila = palabras_nombre + " " + texto_fila

        filas_list.append({
            "numero_fila": numero_fila,
            "texto_fila": texto_fila
        })

    if not filas_list:
        return {"success": False, "error": "Texto extraído vacío tras OCR"}

    return {
        "success": True,
        "id_drive": id_drive,
        "nombre_compuesto": nombre_compuesto,
        "url_acceso": url_acceso,
        "primera_carpeta": primera_carpeta,
        "ruta_extraccion": "OCR",
        "filas": filas_list
    }


def leer_bloque(f_in, tamano_bloque: int) -> list[dict]:
    """Lee un bloque de líneas del archivo de entrada y las parsea de JSON."""
    lineas = []
    for _ in range(tamano_bloque):
        linea = f_in.readline()
        if not linea:
            break
        linea_str = linea.strip()
        if linea_str:
            try:
                lineas.append(json.loads(linea_str))
            except Exception as e:
                print(f"[-] Error parsing JSON line: {e}")
    return lineas


def motor_ingesta_uni():
    """Motor principal de ingesta: lee crawled_documents.jsonl por bloques, procesa y escribe a archivos planos."""
    if not os.path.exists(rutaCrawled):
        print(f"[-] No se encontró {rutaCrawled}. Ejecuta el crawler primero.")
        return

    print("============================================================")
    print("DocUNI v3.0 - Motor de Ingesta con Checkpoint y Bloques (Tika + OCR)")
    print("============================================================")

    reset = obtener_argumentos_reset()
    cp = cargar_checkpoint("dataIngestion", reset)

    ultimo_bloque = 0
    lineas_leidas_entrada = 0
    lineas_escritas_docs = 0
    lineas_escritas_pags = 0
    total_procesados = 0
    total_fallidos = 0
    
    # Nuevas variables para el control de fases
    fase = "tika"
    documentos_para_ocr = []
    ocr_procesados_index = 0

    if cp:
        ultimo_bloque = cp.get("ultimo_bloque", 0)
        lineas_leidas_entrada = cp.get("lineas_leidas_entrada", 0)
        lineas_escritas_docs = cp.get("lineas_escritas_docs", 0)
        lineas_escritas_pags = cp.get("lineas_escritas_pags", 0)
        total_procesados = cp.get("total_procesados", 0)
        total_fallidos = cp.get("total_fallidos", 0)
        fase = cp.get("fase", "tika")
        documentos_para_ocr = cp.get("documentos_para_ocr", [])
        ocr_procesados_index = cp.get("ocr_procesados_index", 0)
        print(f"[*] Reanudando ingesta desde checkpoint (Fase: {fase.upper()}):")
        print(f"    - Registros de entrada leídos: {lineas_leidas_entrada}")
        print(f"    - Registros de salida docs escritos: {lineas_escritas_docs}")
        print(f"    - Registros de salida pags escritos: {lineas_escritas_pags}")
        print(f"    - Documentos procesados exitosamente: {total_procesados}")
        print(f"    - Documentos diferidos para OCR: {len(documentos_para_ocr)} (Procesados: {ocr_procesados_index})")
        
        # Truncar archivos de salida por cantidad de registros
        truncar_archivo_por_lineas(rutaDocIndexados, lineas_escritas_docs)
        truncar_archivo_por_lineas(rutaPaginasDoc, lineas_escritas_pags)
    else:
        print("[*] Iniciando ingesta desde cero...")
        truncar_archivo_por_lineas(rutaDocIndexados, 0)
        truncar_archivo_por_lineas(rutaPaginasDoc, 0)

    creds = obtener_credenciales()

    # Asegurar que la carpeta de destino existe
    os.makedirs(os.path.dirname(rutaDocIndexados), exist_ok=True)

    TAMANO_BLOQUE = 10
    MAX_HILOS = 10
    t_inicio = time.time()

    # FASE 1: Procesamiento con Tika
    if fase == "tika":
        print("[*] INICIANDO FASE 1: Extracción con Apache Tika...")
        print("[*] Inicializando y calentando servidor Apache Tika para DocuUNI v3.0...")
        from tika import initVM
        initVM()
        parser.from_buffer(b"Warmup")
        print("[*] Tika Server listo y corriendo.")

        print(f"[*] Procesando documentos en bloques de {TAMANO_BLOQUE} con {MAX_HILOS} hilos...")
        
        with open(rutaCrawled, 'r', encoding='utf-8') as f_in:
            # Saltar las líneas ya procesadas
            for _ in range(lineas_leidas_entrada):
                if not f_in.readline():
                    break

            while not evento_parada.is_set():
                bloque = leer_bloque(f_in, TAMANO_BLOQUE)
                if not bloque:
                    break  # Fin de archivo

                lineas_leidas_entrada += len(bloque)
                print(f"\n[*] Cargando bloque de {len(bloque)} documentos...")
                
                resultados = []
                with ThreadPoolExecutor(max_workers=MAX_HILOS) as executor:
                    futuros = {executor.submit(procesar_un_documento_tika, doc, creds): doc for doc in bloque}
                    
                    for futuro in as_completed(futuros):
                        try:
                            res = futuro.result()
                            resultados.append(res)
                        except Exception as e:
                            doc = futuros[futuro]
                            print(f"[-] Excepción de ejecución en Tika para {doc.get('nombre_compuesto')}: {e}")
                            resultados.append({
                                "success": False, 
                                "error": str(e), 
                                "necesita_ocr": True, 
                                "doc": doc
                            })

                print("[*] Bloque completado. Escribiendo resultados a disco...")
                
                nuevos_docs = 0
                nuevas_pags = 0
                
                with open(rutaDocIndexados, 'a', encoding='utf-8') as f_docs, \
                     open(rutaPaginasDoc, 'a', encoding='utf-8') as f_pags:
                    
                    for res in resultados:
                        if not res.get("success"):
                            if res.get("necesita_ocr"):
                                print(f"    [Tika Fallido] Se difiere a OCR: {res['doc']['nombre_compuesto']} -> {res.get('error')}")
                                documentos_para_ocr.append(res["doc"])
                            else:
                                print(f"    [-] Error en: {res.get('nombre_compuesto')} -> {res.get('error')}")
                                total_fallidos += 1
                            continue

                        id_drive = res["id_drive"]
                        nombre_compuesto = res["nombre_compuesto"]
                        url_acceso = res["url_acceso"]

                        doc_meta = {
                            "id_drive": id_drive,
                            "nombre_compuesto": nombre_compuesto,
                            "url_acceso": url_acceso,
                            "primera_carpeta": res.get("primera_carpeta")
                        }
                        f_docs.write(json.dumps(doc_meta, ensure_ascii=False) + '\n')
                        nuevos_docs += 1

                        for fila in res["filas"]:
                            fila_data = {
                                "id_drive": id_drive,
                                "numero_fila": fila["numero_fila"],
                                "texto_fila": fila["texto_fila"]
                            }
                            f_pags.write(json.dumps(fila_data, ensure_ascii=False) + '\n')
                            nuevas_pags += 1

                        total_procesados += 1
                        print(f"    [+] {nombre_compuesto} ({res['ruta_extraccion']}) → {len(res['filas'])} filas.")
                    
                    f_docs.flush()
                    f_pags.flush()

                # Guardar el checkpoint exitoso al finalizar el bloque actual
                ultimo_bloque += 1
                lineas_escritas_docs += nuevos_docs
                lineas_escritas_pags += nuevas_pags
                
                checkpoint_data = {
                    "ultimo_bloque": ultimo_bloque,
                    "lineas_leidas_entrada": lineas_leidas_entrada,
                    "lineas_escritas_docs": lineas_escritas_docs,
                    "lineas_escritas_pags": lineas_escritas_pags,
                    "total_procesados": total_procesados,
                    "total_fallidos": total_fallidos,
                    "fase": fase,
                    "documentos_para_ocr": documentos_para_ocr,
                    "ocr_procesados_index": ocr_procesados_index
                }
                guardar_checkpoint("dataIngestion", checkpoint_data)

                # Forzar liberación de RAM de las estructuras del bloque
                resultados.clear()
                bloque.clear()

        # Si no hubo parada, pasamos a la siguiente fase
        if not evento_parada.is_set():
            fase = "ocr"
            ocr_procesados_index = 0
            checkpoint_data = {
                "ultimo_bloque": ultimo_bloque,
                "lineas_leidas_entrada": lineas_leidas_entrada,
                "lineas_escritas_docs": lineas_escritas_docs,
                "lineas_escritas_pags": lineas_escritas_pags,
                "total_procesados": total_procesados,
                "total_fallidos": total_fallidos,
                "fase": fase,
                "documentos_para_ocr": documentos_para_ocr,
                "ocr_procesados_index": ocr_procesados_index
            }
            guardar_checkpoint("dataIngestion", checkpoint_data)
            print(f"\n[*] FASE 1 COMPLETADA. Se detectaron {len(documentos_para_ocr)} documentos pendientes de OCR.")

    # FASE 2: Procesamiento con OCR
    if fase == "ocr" and not evento_parada.is_set():
        pendientes = len(documentos_para_ocr) - ocr_procesados_index
        if pendientes > 0:
            print(f"\n[*] INICIANDO FASE 2: Extracción con OCR para {pendientes} documentos pendientes...")
            try:
                # Cargar el OCR una sola vez para toda la fase
                cargar_modulo_ocr()
                ocr_disponible = True
            except Exception as e:
                print(f"[-] Motor OCR no disponible (falta de conexión o modelos). Se marcarán los {pendientes} documentos como fallidos.")
                ocr_disponible = False
            
            # Procesar en bloques el arreglo de pendientes
            while ocr_procesados_index < len(documentos_para_ocr) and not evento_parada.is_set():
                limite_superior = min(ocr_procesados_index + TAMANO_BLOQUE, len(documentos_para_ocr))
                bloque_ocr = documentos_para_ocr[ocr_procesados_index:limite_superior]
                
                if not ocr_disponible:
                    print(f"    [-] Omitiendo bloque OCR ({ocr_procesados_index + 1} a {limite_superior}) por falta de motor OCR.")
                    total_fallidos += len(bloque_ocr)
                    ocr_procesados_index += len(bloque_ocr)
                    checkpoint_data = {
                        "ultimo_bloque": ultimo_bloque,
                        "lineas_leidas_entrada": lineas_leidas_entrada,
                        "lineas_escritas_docs": lineas_escritas_docs,
                        "lineas_escritas_pags": lineas_escritas_pags,
                        "total_procesados": total_procesados,
                        "total_fallidos": total_fallidos,
                        "fase": fase,
                        "documentos_para_ocr": documentos_para_ocr,
                        "ocr_procesados_index": ocr_procesados_index
                    }
                    guardar_checkpoint("dataIngestion", checkpoint_data)
                    continue
                
                print(f"\n[*] Cargando bloque OCR ({ocr_procesados_index + 1} a {limite_superior} de {len(documentos_para_ocr)})...")
                
                resultados = []
                with ThreadPoolExecutor(max_workers=MAX_HILOS) as executor:
                    futuros = {executor.submit(procesar_un_documento_ocr, doc, creds): doc for doc in bloque_ocr}
                    
                    for futuro in as_completed(futuros):
                        try:
                            res = futuro.result()
                            resultados.append(res)
                        except Exception as e:
                            doc = futuros[futuro]
                            print(f"[-] Excepción de ejecución en OCR para {doc.get('nombre_compuesto')}: {e}")
                            resultados.append({
                                "success": False, 
                                "error": str(e), 
                                "nombre_compuesto": doc.get("nombre_compuesto")
                            })

                print("[*] Bloque OCR completado. Escribiendo resultados a disco...")
                
                nuevos_docs = 0
                nuevas_pags = 0
                
                with open(rutaDocIndexados, 'a', encoding='utf-8') as f_docs, \
                     open(rutaPaginasDoc, 'a', encoding='utf-8') as f_pags:
                    
                    for res in resultados:
                        if not res.get("success"):
                            print(f"    [-] Error en OCR: {res.get('nombre_compuesto')} -> {res.get('error')}")
                            total_fallidos += 1
                            continue

                        id_drive = res["id_drive"]
                        nombre_compuesto = res["nombre_compuesto"]
                        url_acceso = res["url_acceso"]

                        doc_meta = {
                            "id_drive": id_drive,
                            "nombre_compuesto": nombre_compuesto,
                            "url_acceso": url_acceso,
                            "primera_carpeta": res.get("primera_carpeta")
                        }
                        f_docs.write(json.dumps(doc_meta, ensure_ascii=False) + '\n')
                        nuevos_docs += 1

                        for fila in res["filas"]:
                            fila_data = {
                                "id_drive": id_drive,
                                "numero_fila": fila["numero_fila"],
                                "texto_fila": fila["texto_fila"]
                            }
                            f_pags.write(json.dumps(fila_data, ensure_ascii=False) + '\n')
                            nuevas_pags += 1

                        total_procesados += 1
                        print(f"    [+] {nombre_compuesto} ({res['ruta_extraccion']}) → {len(res['filas'])} filas.")
                    
                    f_docs.flush()
                    f_pags.flush()

                # Guardar el checkpoint exitoso al finalizar el bloque actual de OCR
                ocr_procesados_index += len(bloque_ocr)
                lineas_escritas_docs += nuevos_docs
                lineas_escritas_pags += nuevas_pags
                
                checkpoint_data = {
                    "ultimo_bloque": ultimo_bloque,
                    "lineas_leidas_entrada": lineas_leidas_entrada,
                    "lineas_escritas_docs": lineas_escritas_docs,
                    "lineas_escritas_pags": lineas_escritas_pags,
                    "total_procesados": total_procesados,
                    "total_fallidos": total_fallidos,
                    "fase": fase,
                    "documentos_para_ocr": documentos_para_ocr,
                    "ocr_procesados_index": ocr_procesados_index
                }
                guardar_checkpoint("dataIngestion", checkpoint_data)

                # Liberar memoria
                resultados.clear()
                bloque_ocr.clear()
        else:
            print("\n[*] FASE 2: No hay documentos pendientes para procesamiento OCR.")

    duracion = time.time() - t_inicio
    print(f"\n[*] Ingesta terminada en {duracion:.2f}s.")
    print(f"    -> Procesados exitosamente: {total_procesados}")
    print(f"    -> Fallidos: {total_fallidos}")


if __name__ == '__main__':
    motor_ingesta_uni()