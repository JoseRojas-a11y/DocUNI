import os
import sys
import json
import re
import time

# Configurar stdout y stderr para usar UTF-8 y evitar errores de codificación en Windows
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Importar utilidades de checkpoint
from checkpoint_utils import cargar_checkpoint, guardar_checkpoint, truncar_archivo_por_lineas, obtener_argumentos_reset

# Rutas resueltas de manera absoluta respecto a la ubicación de este script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
rutaPaginasDoc = os.path.abspath(os.path.join(BASE_DIR, '..', 'archivos_planos', 'paginas_documento.jsonl'))
rutaIndiceUniTemp = os.path.abspath(os.path.join(BASE_DIR, '..', 'archivos_planos', 'indice_invertido_uni_temp.jsonl'))

TAMANO_BLOQUE = 50000  # Procesar de a 50000 lineas por bloque

def normalizar_palabra(palabra: str) -> str:
    """Aplica normalización avanzada a la palabra (tildes, plurales, errores de OCR, y límite de 150 caracteres)."""
    # 1. Limitar longitud a 150 caracteres
    if len(palabra) > 150:
        palabra = palabra[:150]

    # 2. Convertir a minúsculas
    palabra = palabra.lower()

    # 3. Remover acentos/tildes
    tildes = {'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u', 'ü': 'u'}
    for t, r in tildes.items():
        palabra = palabra.replace(t, r)

    # 4. Corregir errores típicos de OCR (e.g. reunién -> reunion)
    if palabra.endswith("ien") and len(palabra) > 4:
        palabra = palabra[:-3] + "ion"

    # 5. Normalizar plurales (eliminar la 's' final en palabras de longitud > 3)
    if palabra.endswith("s") and len(palabra) > 3:
        palabra = palabra[:-1]

    return palabra


def extraer_palabras(texto: str) -> list[str]:
    """Normaliza y tokeniza el texto: minúsculas + solo alfanuméricos en español."""
    if not texto:
        return []
    palabras_crudas = re.findall(r'\b[a-záéíóúñ0-9]+\b', texto.lower())
    return [normalizar_palabra(p) for p in palabras_crudas if p]

def tokenizar_y_posicionar(texto: str) -> list[tuple]:
    """Normaliza y extrae las palabras y sus posiciones."""
    palabras = extraer_palabras(texto)
    tuplas = []
    for posicion, palabra in enumerate(palabras):
        if len(palabra) > 150:
            palabra = palabra[:150]
        if palabra:
            tuplas.append((palabra, posicion))
    return tuplas

def construir_indice():
    if not os.path.exists(rutaPaginasDoc):
        print(f"[-] No se encontró {rutaPaginasDoc}. Ejecuta dataIngestion.py primero.")
        return

    print("============================================================")
    print("DocUNI v3.0 - Index Maker (Generación de Fichas Temporales)")
    print("============================================================")
    print(f"[*] Leyendo filas de documentos desde: {rutaPaginasDoc}")
    print(f"[*] Escribiendo índice temporal en: {rutaIndiceUniTemp}")
    
    os.makedirs(os.path.dirname(rutaIndiceUniTemp), exist_ok=True)
    
    reset = obtener_argumentos_reset()
    cp = cargar_checkpoint("indexMaker", reset)
    
    ultimo_bloque = 0
    lineas_leidas_entrada = 0
    lineas_escritas_indice = 0
    total_filas = 0
    total_tokens = 0
    
    if cp:
        ultimo_bloque = cp.get("ultimo_bloque", 0)
        lineas_leidas_entrada = cp.get("lineas_leidas_entrada", 0)
        lineas_escritas_indice = cp.get("lineas_escritas_indice", 0)
        total_filas = cp.get("total_filas", 0)
        total_tokens = cp.get("total_tokens", 0)
        print(f"[*] Reanudando generación desde checkpoint (Bloque {ultimo_bloque}):")
        print(f"    - Filas leídas de entrada: {lineas_leidas_entrada}")
        print(f"    - Tokens (índice) escritos de salida: {lineas_escritas_indice}")
        print(f"    - Filas indexadas previamente: {total_filas}")
        print(f"    - Tokens indexados previamente: {total_tokens}")
        
        # Truncar archivo de salida por cantidad de registros
        truncar_archivo_por_lineas(rutaIndiceUniTemp, lineas_escritas_indice)
    else:
        print("[*] Iniciando indexador desde cero...")
        truncar_archivo_por_lineas(rutaIndiceUniTemp, 0)
        
    t_inicio = time.time()
    
    try:
        from proceso_batch.procesos.dataIngestion import iterar_lineas_archivos_rotados
    except ImportError:
        from dataIngestion import iterar_lineas_archivos_rotados

    generator = iterar_lineas_archivos_rotados(rutaPaginasDoc, lineas_leidas_entrada)

    with open(rutaIndiceUniTemp, 'a', encoding='utf-8') as f_out:
        while True:
            bloque = []
            for _ in range(TAMANO_BLOQUE):
                try:
                    linea = next(generator)
                    linea_str = linea.strip()
                    if linea_str:
                        bloque.append(json.loads(linea_str))
                except StopIteration:
                    break
                except Exception as e:
                    print(f"[-] Error parseando línea de página: {e}")

            if not bloque:
                break
                
            inicio_bloque_id = lineas_leidas_entrada + 1
            lineas_leidas_entrada += len(bloque)
            tokens_bloque = []
            
            for idx_pag, pag in enumerate(bloque):
                try:
                    id_linea = inicio_bloque_id + idx_pag
                    texto_fila = pag["texto_fila"]
                    
                    tokens_pos = tokenizar_y_posicionar(texto_fila)
                    for palabra, posicion in tokens_pos:
                        tok_data = {
                            "palabra": palabra,
                            "id_linea": id_linea,
                            "posicion": posicion
                        }
                        tokens_bloque.append(tok_data)
                    total_filas += 1
                except Exception as e:
                    print(f"[-] Error procesando fila en bloque: {e}")
            
            # Escribir todos los tokens del bloque
            if tokens_bloque:
                for tok in tokens_bloque:
                    f_out.write(json.dumps(tok, ensure_ascii=False) + '\n')
                f_out.flush()
                total_tokens += len(tokens_bloque)
                lineas_escritas_indice += len(tokens_bloque)
                
            ultimo_bloque += 1
            print(f"    [+] Bloque {ultimo_bloque} procesado. Total filas: {total_filas} | Total tokens: {total_tokens}")
            
            # Guardar checkpoint
            checkpoint_data = {
                "ultimo_bloque": ultimo_bloque,
                "lineas_leidas_entrada": lineas_leidas_entrada,
                "lineas_escritas_indice": lineas_escritas_indice,
                "total_filas": total_filas,
                "total_tokens": total_tokens
            }
            guardar_checkpoint("indexMaker", checkpoint_data)
            
            # Liberar RAM
            tokens_bloque.clear()
            bloque.clear()
            
    duracion = time.time() - t_inicio
    print("============================================================")
    print(f"[*] ¡Generación de índice temporal finalizada en {duracion:.2f}s!")
    print(f"  - Total de filas procesadas: {total_filas}")
    print(f"  - Total de tokens indexados:   {total_tokens}")
    print("============================================================")

if __name__ == '__main__':
    construir_indice()
