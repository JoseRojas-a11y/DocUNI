import sys
import os
import psycopg2
import psycopg2.extras

# Rutas resueltas de manera absoluta respecto a la ubicación de este script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
archivo_ingesta = os.path.abspath(os.path.join(BASE_DIR, '..', 'archivos_planos', 'ingesta_intermedia_uni.txt'))

# Añadir la raíz del proyecto al sys.path para poder importar db_config
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, '..', '..')))
from db_config import DB_CONFIG

def actualizar_longitudes_documentos():
    lote_actualizaciones = []
    TAMANIO_LOTE = 5000  
    
    print("[*] Conectando a la base de datos PostgreSQL...")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
    except Exception as e:
        print(f"[-] Error de conexión: {e}")
        return

    print(f"[*] Analizando {archivo_ingesta} para calcular longitudes...")
    
    # Query optimizada para actualizar múltiples filas de golpe
    query_update_masivo = """
        UPDATE documentos_indexados AS d
        SET longitud = v.longitud::integer
        FROM (VALUES %s) AS v(id_drive, longitud)
        WHERE d.id_drive = v.id_drive;
    """

    documentos_procesados = 0
    total_palabras_corpus = 0

    try:
        with open(archivo_ingesta, 'r', encoding='utf-8') as archivo:
            for linea in archivo:
                if not linea.strip():
                    continue
                
                partes = linea.strip().split('|')
                if len(partes) != 2:
                    continue
                    
                id_drive = partes[0]
                texto_crudo = partes[1]
                
                # Calcular la longitud contando los espacios
                # Si el texto está vacío, la longitud es 0
                if texto_crudo:
                    cantidad_palabras = len(texto_crudo.split(' '))
                else:
                    cantidad_palabras = 0
                
                lote_actualizaciones.append((id_drive, cantidad_palabras))
                documentos_procesados += 1
                total_palabras_corpus += cantidad_palabras
                
                # Ejecutar actualización cuando el lote esté lleno
                if len(lote_actualizaciones) >= TAMANIO_LOTE:
                    psycopg2.extras.execute_values(cursor, query_update_masivo, lote_actualizaciones)
                    conn.commit()
                    print(f"    -> Actualizados {documentos_procesados} documentos...")
                    lote_actualizaciones = [] 
            
            # Procesar el remanente
            if lote_actualizaciones:
                psycopg2.extras.execute_values(cursor, query_update_masivo, lote_actualizaciones)
                conn.commit()
                
        print("\n[+] ¡Cálculo y actualización de longitudes completado exitosamente!")
        print(f"[+] Documentos totales evaluados: {documentos_procesados}")
        print(f"[+] Tamaño total del corpus (palabras): {total_palabras_corpus}")
        
        if documentos_procesados > 0:
            promedio = total_palabras_corpus / documentos_procesados
            print(f"[+] Longitud promedio por documento (avgdl): {promedio:.2f}")

    except FileNotFoundError:
        print(f"[-] Error: No se encontró el archivo {archivo_ingesta}.")
    except Exception as e:
        print(f"[-] Error inesperado en la base de datos: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    actualizar_longitudes_documentos()