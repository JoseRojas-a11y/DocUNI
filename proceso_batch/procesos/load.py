import json
import os
import sys
import psycopg2
import psycopg2.extras

# Rutas resueltas de manera absoluta respecto a la ubicación de este script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
archivo_jsonl = os.path.abspath(os.path.join(BASE_DIR, '..', 'archivos_planos', 'indice_invertido_final.jsonl'))

# Añadir la raíz del proyecto al sys.path para poder importar db_config
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, '..', '..')))
from db_config import DB_CONFIG

def cargar_indice_masivo():
    lote_datos = []
    # Tamaño del lote (Batch Size). 2000 a 5000 es el punto óptimo para rendimiento
    TAMANIO_LOTE = 2000  
    
    print("[*] Conectando a la base de datos PostgreSQL...")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
    except Exception as e:
        print(f"[-] Error crítico de conexión: {e}")
        return

    print(f"[*] Iniciando la lectura del archivo: {archivo_jsonl}")
    
    query_insert = """
        INSERT INTO indice_docu_uni (palabra, documentos)
        VALUES %s
        ON CONFLICT (palabra) 
        DO UPDATE SET documentos = EXCLUDED.documentos;
    """

    contador_palabras = 0
    
    try:
        with open(archivo_jsonl, 'r', encoding='utf-8') as archivo:
            for linea in archivo:
                if not linea.strip():
                    continue
                
                # Parsear la línea JSONL
                registro = json.loads(linea)
                palabra = registro["palabra"]
                documentos_vector = registro["documentos"]
                
                # Convertimos el vector (lista de dicts) a un string JSON 
                # para que el conector de Postgres lo identifique correctamente como JSONB
                documentos_jsonb = json.dumps(documentos_vector)
                
                # Agregamos la tupla al lote actual
                lote_datos.append((palabra, documentos_jsonb))
                contador_palabras += 1
                
                # Cuando el lote se llena, se ejecuta la inserción masiva
                if len(lote_datos) >= TAMANIO_LOTE:
                    psycopg2.extras.execute_values(cursor, query_insert, lote_datos)
                    conn.commit() # Confirmamos la transacción del lote
                    lote_datos = [] # Vaciamos el lote para el siguiente bloque
                    print(f"    -> Indexadas {contador_palabras} palabras...")
            
            # Insertar los registros que quedaron en el último lote incompleto
            if lote_datos:
                psycopg2.extras.execute_values(cursor, query_insert, lote_datos)
                conn.commit()
                
        print(f"\n[+] ¡Migración masiva completada exitosamente!")
        print(f"[+] Se han subido un total de {contador_palabras} términos únicos al índice.")

    except FileNotFoundError:
        print(f"[-] Error: No se encontró el archivo '{archivo_jsonl}'. Asegúrate de haber ejecutado la fase anterior.")
    except json.JSONDecodeError as e:
        print(f"[-] Error de formato en el archivo JSONL: {e}")
        conn.rollback()
    except Exception as e:
        print(f"[-] Error inesperado durante la carga masiva: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    cargar_indice_masivo()