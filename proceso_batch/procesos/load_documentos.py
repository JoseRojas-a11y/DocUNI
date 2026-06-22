import os
import sys
import json
import psycopg2
import psycopg2.extras
import time

# Configurar stdout y stderr para usar UTF-8 y evitar errores de codificación en Windows
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Importar utilidades de checkpoint
from checkpoint_utils import cargar_checkpoint, guardar_checkpoint, obtener_argumentos_reset

# Rutas resueltas de manera absoluta respecto a la ubicación de este script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
rutaDocIndexados = os.path.abspath(os.path.join(BASE_DIR, '..', 'archivos_planos', 'documentos_indexados.jsonl'))

# Añadir la raíz del proyecto al sys.path para poder importar db_config
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, '..', '..')))
from db_config import DB_CONFIG

def cargar_documentos(tamano_bloque: int = 1000, reset: bool = None):
    if not os.path.exists(rutaDocIndexados):
        print(f"[-] No se encontró {rutaDocIndexados}. Salteando carga de documentos.")
        return 0

    if reset is None:
        reset = obtener_argumentos_reset()

    cp = cargar_checkpoint("load_documentos", reset)
    
    ultimo_bloque = 0
    lineas_leidas_entrada = 0
    total_cargados = 0
    
    if cp:
        ultimo_bloque = cp.get("ultimo_bloque", 0)
        lineas_leidas_entrada = cp.get("lineas_leidas_entrada", 0)
        total_cargados = cp.get("total_cargados", 0)
        print(f"[*] Reanudando carga de documentos desde checkpoint (Bloque {ultimo_bloque}):")
        print(f"    - Registros leídos previamente: {lineas_leidas_entrada}")
        print(f"    - Documentos cargados previamente: {total_cargados}")
    else:
        print(f"[*] Iniciando carga de documentos desde cero...")
        
    print(f"[*] Conectando a la base de datos para cargar documentos...")
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    
    bloque = []
    
    query = """
        INSERT INTO documentos_indexados (id_drive, nombre_compuesto, url_acceso, primera_carpeta)
        VALUES %s
        ON CONFLICT (id_drive) DO UPDATE 
        SET nombre_compuesto = EXCLUDED.nombre_compuesto,
            url_acceso = EXCLUDED.url_acceso,
            primera_carpeta = EXCLUDED.primera_carpeta;
    """
    
    try:
        with open(rutaDocIndexados, 'r', encoding='utf-8') as f:
            # Saltar líneas ya procesadas
            for _ in range(lineas_leidas_entrada):
                if not f.readline():
                    break
                
            while True:
                linea = f.readline()
                if not linea:
                    break
                
                linea_str = linea.strip()
                if not linea_str:
                    continue
                
                try:
                    data = json.loads(linea_str)
                    bloque.append((
                        data["id_drive"],
                        data["nombre_compuesto"],
                        data["url_acceso"],
                        data.get("primera_carpeta")
                    ))
                except Exception as e:
                    print(f"[-] Error parseando línea en load_documentos: {e}")
                    
                if len(bloque) >= tamano_bloque:
                    psycopg2.extras.execute_values(cursor, query, bloque)
                    conn.commit()
                    total_cargados += len(bloque)
                    lineas_leidas_entrada += len(bloque)
                    ultimo_bloque += 1
                    print(f"    [+] Bloque {ultimo_bloque} cargado. Total acumulado: {total_cargados} documentos...")
                    
                    # Guardar checkpoint
                    guardar_checkpoint("load_documentos", {
                        "ultimo_bloque": ultimo_bloque,
                        "lineas_leidas_entrada": lineas_leidas_entrada,
                        "total_cargados": total_cargados
                    })
                    bloque.clear()
            
            # Cargar remanente
            if bloque:
                psycopg2.extras.execute_values(cursor, query, bloque)
                conn.commit()
                total_cargados += len(bloque)
                lineas_leidas_entrada += len(bloque)
                ultimo_bloque += 1
                print(f"    [+] Bloque {ultimo_bloque} (remanente) cargado. Total acumulado: {total_cargados} documentos...")
                
                guardar_checkpoint("load_documentos", {
                    "ultimo_bloque": ultimo_bloque,
                    "lineas_leidas_entrada": lineas_leidas_entrada,
                    "total_cargados": total_cargados
                })
                bloque.clear()
                
    except Exception as e:
        print(f"[-] Error durante la carga de documentos: {e}")
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()
        
    print(f"[*] Carga de documentos completada. Total: {total_cargados} documentos.")
    return total_cargados

if __name__ == '__main__':
    cargar_documentos()
