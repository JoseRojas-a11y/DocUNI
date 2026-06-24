import os
import sys
import json
import psycopg2
import psycopg2.extras

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
rutaPaginasDoc = os.path.abspath(os.path.join(BASE_DIR, '..', 'archivos_planos', 'paginas_documento.jsonl'))

# Añadir la raíz del proyecto al sys.path para poder importar db_config
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, '..', '..')))
from db_config import DB_CONFIG

# AUMENTO DE BLOQUE: Pasamos de 1,000 a 10,000 para cargas masivas (bulk insert)
def cargar_lineas(tamano_bloque: int = 10000, reset: bool = None):
    if not os.path.exists(rutaPaginasDoc):
        print(f"[-] No se encontró {rutaPaginasDoc}. Salteando carga de páginas.")
        return 0

    if reset is None:
        reset = obtener_argumentos_reset()

    cp = cargar_checkpoint("load_paginas", reset)
    
    ultimo_bloque = 0
    lineas_leidas_entrada = 0
    total_cargados = 0
    
    if cp:
        ultimo_bloque = cp.get("ultimo_bloque", 0)
        lineas_leidas_entrada = cp.get("lineas_leidas_entrada", 0)
        total_cargados = cp.get("total_cargados", 0)
        print(f"[*] Reanudando carga desde checkpoint (Bloque {ultimo_bloque}):")
        print(f"    - Registros leídos previamente: {lineas_leidas_entrada}")
        print(f"    - Filas cargadas previamente: {total_cargados}")
    else:
        print(f"[*] Iniciando carga de filas desde cero...")
        
    print(f"[*] Conectando a la base de datos para cargar filas...")
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    
    bloque = []
    
    # QUERY SIMPLIFICADA: Eliminado el ON CONFLICT porque la tabla está vacía.
    # Esto acelera el INSERT significativamente.
    query = """
        INSERT INTO lineas_documento (id_drive, numero_fila, texto_fila)
        VALUES %s
    """
    
    try:
        try:
            from proceso_batch.procesos.dataIngestion import iterar_lineas_archivos_rotados
        except ImportError:
            from dataIngestion import iterar_lineas_archivos_rotados

        for linea in iterar_lineas_archivos_rotados(rutaPaginasDoc, lineas_leidas_entrada):
            linea_str = linea.strip()
            if not linea_str:
                continue
            
            try:
                data = json.loads(linea_str)
                bloque.append((
                    data["id_drive"],
                    data["numero_fila"],
                    data["texto_fila"]
                ))
            except Exception as e:
                print(f"[-] Error parseando línea en load_paginas: {e}")
                
            if len(bloque) >= tamano_bloque:
                psycopg2.extras.execute_values(cursor, query, bloque)
                conn.commit()
                total_cargados += len(bloque)
                lineas_leidas_entrada += len(bloque)
                ultimo_bloque += 1
                print(f"    [+] Bloque {ultimo_bloque} cargado. Total acumulado: {total_cargados} filas...")
                
                # Guardar checkpoint
                guardar_checkpoint("load_paginas", {
                    "ultimo_bloque": ultimo_bloque,
                    "lineas_leidas_entrada": lineas_leidas_entrada,
                    "total_cargados": total_cargados
                })
                bloque.clear()
        
        # CORRECCIÓN DE INDENTACIÓN: El remanente DEBE estar fuera del bucle 'for'.
        # Si está dentro, forzará la subida de datos fila por fila.
        if bloque:
            psycopg2.extras.execute_values(cursor, query, bloque)
            conn.commit()
            total_cargados += len(bloque)
            lineas_leidas_entrada += len(bloque)
            ultimo_bloque += 1
            print(f"    [+] Bloque {ultimo_bloque} (remanente) cargado. Total acumulado: {total_cargados} filas...")
            
            guardar_checkpoint("load_paginas", {
                "ultimo_bloque": ultimo_bloque,
                "lineas_leidas_entrada": lineas_leidas_entrada,
                "total_cargados": total_cargados
            })
            bloque.clear()
                
    except Exception as e:
        print(f"[-] Error durante la carga de filas: {e}")
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()
        
    print(f"[*] Carga de filas completada. Total: {total_cargados} filas.")
    return total_cargados

if __name__ == '__main__':
    cargar_lineas()