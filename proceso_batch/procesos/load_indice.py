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
        print(f"[*] Reanudando carga de índice invertido desde checkpoint (Bloque {ultimo_bloque}):")
        print(f"    - Registros leídos previamente: {lineas_leidas_entrada}")
        print(f"    - Tokens cargados previamente en BD: {total_cargados}")
        print(f"    - Registros leídos totales previamente: {total_leidos}")
    else:
        print(f"[*] Iniciando carga de índice invertido desde cero...")

    print(f"[*] Conectando a la base de datos para cargar índice...")
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    
    bloque = []
    
    try:
        with open(rutaIndiceUni, 'r', encoding='utf-8') as f:
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
                    bloque.append(data)
                    total_leidos += 1
                except Exception as e:
                    print(f"[-] Error parseando línea en load_indice: {e}")
                    
                if len(bloque) >= tamano_bloque:
                    cargados = procesar_y_cargar_bloque(cursor, conn, bloque)
                    total_cargados += cargados
                    lineas_leidas_entrada += len(bloque)
                    ultimo_bloque += 1
                    print(f"    [+] Bloque {ultimo_bloque} procesado | Leídos: {total_leidos} | Insertados en BD: {total_cargados}...")
                    
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
                cargados = procesar_y_cargar_bloque(cursor, conn, bloque)
                total_cargados += cargados
                lineas_leidas_entrada += len(bloque)
                ultimo_bloque += 1
                print(f"    [+] Bloque {ultimo_bloque} (remanente) procesado | Leídos: {total_leidos} | Insertados en BD: {total_cargados}...")
                
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

def procesar_y_cargar_bloque(cursor, conn, bloque) -> int:
    """Resuelve id_pagina para cada token en el bloque y los inserta en la base de datos."""
    if not bloque:
        return 0

    # Extraer combinaciones únicas de (id_drive, numero_pagina) en el bloque
    unique_pairs = list(set((item["d"], item["n"]) for item in bloque))
    
    mapping = {}
    
    # Consultar id_pagina en sub-lotes de 5,000 para evitar desbordar límites de placeholders
    SUB_BATCH_SIZE = 5000
    for i in range(0, len(unique_pairs), SUB_BATCH_SIZE):
        sub_list = unique_pairs[i:i+SUB_BATCH_SIZE]
        
        cursor.execute(
            """
            SELECT id_pagina, id_drive, numero_pagina 
            FROM paginas_documento 
            WHERE (id_drive, numero_pagina) IN %s
            """,
            (tuple(sub_list),)
        )
        
        for id_pag, id_dr, num_pag in cursor.fetchall():
            mapping[(id_dr, num_pag)] = id_pag

    # Mapear tokens a las llaves foráneas reales de base de datos
    insert_rows = []
    mismatch_count = 0
    
    for item in bloque:
        key = (item["d"], item["n"])
        if key in mapping:
            insert_rows.append((
                item["p"],
                mapping[key],
                item["pos"]
            ))
        else:
            mismatch_count += 1

    if mismatch_count > 0:
        print(f"    [!] Advertencia: {mismatch_count} tokens no pudieron mapearse a una página existente en la BD.")

    if not insert_rows:
        return 0

    # Bulk Insert con ON CONFLICT para evitar fallas por duplicados
    query = """
        INSERT INTO indice_invertido_uni (palabra, id_pagina, posicion)
        VALUES %s
        ON CONFLICT (palabra, id_pagina, posicion) DO NOTHING;
    """
    psycopg2.extras.execute_values(cursor, query, insert_rows)
    conn.commit()
    
    return len(insert_rows)

if __name__ == '__main__':
    cargar_indice()
