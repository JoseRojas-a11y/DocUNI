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
rutaIndiceUni = os.path.abspath(os.path.join(BASE_DIR, '..', 'archivos_planos', 'indice_invertido_uni.jsonl'))

TAMANO_BLOQUE = 500  # Procesar de a 500 páginas por bloque

def extraer_palabras(texto: str) -> list[str]:
    """Normaliza y tokeniza el texto: minúsculas + solo alfanuméricos en español."""
    if not texto:
        return []
    texto_limpio = texto.lower()
    return re.findall(r'\b[a-záéíóúñ0-9]+\b', texto_limpio)

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

def leer_bloque_paginas(f_in, tamano_bloque: int) -> list[dict]:
    """Lee un bloque de páginas del archivo de entrada y las parsea de JSON."""
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
                print(f"[-] Error parseando línea de página: {e}")
    return lineas

def construir_indice():
    if not os.path.exists(rutaPaginasDoc):
        print(f"[-] No se encontró {rutaPaginasDoc}. Ejecuta dataIngestion.py primero.")
        return

    print("============================================================")
    print("DocUNI v3.0 - Index Maker con Checkpoint y Bloques")
    print("============================================================")
    print(f"[*] Leyendo filas de documentos desde: {rutaPaginasDoc}")
    print(f"[*] Escribiendo índice invertido en: {rutaIndiceUni}")
    
    os.makedirs(os.path.dirname(rutaIndiceUni), exist_ok=True)
    
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
        truncar_archivo_por_lineas(rutaIndiceUni, lineas_escritas_indice)
    else:
        print("[*] Iniciando indexador desde cero...")
        truncar_archivo_por_lineas(rutaIndiceUni, 0)
        
    t_inicio = time.time()
    
    with open(rutaPaginasDoc, 'r', encoding='utf-8') as f_in, \
         open(rutaIndiceUni, 'a', encoding='utf-8') as f_out:
        
        # Saltar las filas ya procesadas
        for _ in range(lineas_leidas_entrada):
            if not f_in.readline():
                break
            
        while True:
            bloque = leer_bloque_paginas(f_in, TAMANO_BLOQUE)
            if not bloque:
                break
                
            lineas_leidas_entrada += len(bloque)
            tokens_bloque = []
            
            for pag in bloque:
                try:
                    id_drive = pag["id_drive"]
                    numero_fila = pag["numero_fila"]
                    texto_fila = pag["texto_fila"]
                    
                    tokens_pos = tokenizar_y_posicionar(texto_fila)
                    for palabra, posicion in tokens_pos:
                        tok_data = {
                            "p": palabra,
                            "d": id_drive,
                            "n": numero_fila,
                            "pos": posicion
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
    print(f"[*] ¡Generación de índice inverso finalizada en {duracion:.2f}s!")
    print(f"  - Total de filas procesadas: {total_filas}")
    print(f"  - Total de tokens indexados:   {total_tokens}")
    print("============================================================")

if __name__ == '__main__':
    construir_indice()
