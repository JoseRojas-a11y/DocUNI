import os
import sys
import json
import psycopg2
import psycopg2.extras
from itertools import islice # Importación clave para máxima velocidad de lectura

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
rutaIndiceUni = os.path.abspath(os.path.join(BASE_DIR, '..', 'archivos_planos', 'indice_invertido_uni.jsonl'))

# Añadir la raíz del proyecto al sys.path para poder importar db_config
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, '..', '..')))
from db_config import DB_CONFIG

def cargar_indice(tamano_bloque: int = 50000, reset: bool = None):
    if not os.path.exists(rutaIndiceUni):
        print(f"[-] No se encontró {rutaIndiceUni}. Salteando carga de índice.")
        return 0

    if reset is None:
        reset = obtener_argumentos_reset()

    cp = cargar_checkpoint("load_indice", reset)
    
    ultimo_bloque = 0
    lineas_leidas_entrada = 0
    total_cargados = 0
    total_leidos = 0
    
    if cp:
        ultimo_bloque = cp.get("ultimo_bloque", 0)
        lineas_leidas_entrada = cp.get("lineas_leidas_entrada", 0)
        total_cargados = cp.get("total_cargados", 0)
        total_leidos = cp.get("total_leidos", 0)
        print(f"[*] Reanudando carga de índice invertido (Bloque {ultimo_bloque}):")
        print(f"    - Registros leídos previamente: {lineas_leidas_entrada}")
        print(f"    - Tokens cargados previamente en BD: {total_cargados}")
    else:
        print(f"[*] Iniciando carga de índice invertido desde cero...")

    print(f"[*] Conectando a la base de datos para cargar índice...")
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    
    bloque = []
    
    try:
        with open(rutaIndiceUni, 'r', encoding='utf-8') as f:
            
            # OPTIMIZACIÓN 1: islice delega el salto de líneas a C, siendo instantáneo.
            lineas_restantes = islice(f, lineas_leidas_entrada, None)
            
            # OPTIMIZACIÓN 2: Iterar directamente sobre el objeto es más rápido que f.readline()
            for linea in lineas_restantes:
                linea_str = linea.strip()
                if not linea_str:
                    continue
                
                try:
                    data = json.loads(linea_str)
                    
                    # OPTIMIZACIÓN 3: Crear la tupla inmediatamente evita iterar 2 veces
                    bloque.append((
                        data["palabra"],
                        data["id_linea"],
                        data["posicion"]
                    ))
                    total_leidos += 1
                except Exception as e:
                    print(f"[-] Error parseando línea en load_indice: {e}")
                    
                if len(bloque) >= tamano_bloque:
                    procesar_y_cargar_bloque(cursor, conn, bloque)
                    total_cargados += len(bloque)
                    lineas_leidas_entrada += len(bloque)
                    ultimo_bloque += 1
                    print(f"    [+] Bloque {ultimo_bloque} procesado | Insertados en BD: {total_cargados}...")
                    
                    # Guardar checkpoint
                    guardar_checkpoint("load_indice", {
                        "ultimo_bloque": ultimo_bloque,
                        "lineas_leidas_entrada": lineas_leidas_entrada,
                        "total_cargados": total_cargados,
                        "total_leidos": total_leidos
                    })
                    bloque.clear()
            
            # Cargar remanente
            if bloque:
                procesar_y_cargar_bloque(cursor, conn, bloque)
                total_cargados += len(bloque)
                lineas_leidas_entrada += len(bloque)
                ultimo_bloque += 1
                print(f"    [+] Bloque {ultimo_bloque} (remanente) procesado | Insertados en BD: {total_cargados}...")
                
                guardar_checkpoint("load_indice", {
                    "ultimo_bloque": ultimo_bloque,
                    "lineas_leidas_entrada": lineas_leidas_entrada,
                    "total_cargados": total_cargados,
                    "total_leidos": total_leidos
                })
                bloque.clear()
                
    except Exception as e:
        print(f"[-] Error durante la carga de índice invertido: {e}")
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()
        
    print(f"[*] Carga de índice completada. Total insertados en BD: {total_cargados} de {total_leidos} leídos.")
    return total_cargados

def procesar_y_cargar_bloque(cursor, conn, bloque):
    """Inserta las tuplas directamente en la base de datos."""
    if not bloque:
        return

    # OPTIMIZACIÓN 4: Eliminado ON CONFLICT DO NOTHING
    query = """
        INSERT INTO indice_invertido_uni (palabra, id_linea, posicion)
        VALUES %s
    """
    psycopg2.extras.execute_values(cursor, query, bloque)
    conn.commit()

if __name__ == '__main__':
    cargar_indice()