import json
import os
from collections import Counter, defaultdict

# Rutas resueltas de manera absoluta respecto a la ubicación de este script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
archivo_ingesta = os.path.abspath(os.path.join(BASE_DIR, '..', 'archivos_planos', 'ingesta_intermedia_uni.txt'))
archivo_indice = os.path.abspath(os.path.join(BASE_DIR, '..', 'archivos_planos', 'indice_invertido_final.jsonl'))

def construir_indice_plano():
    
    # default_dict crea automáticamente una lista vacía si la palabra no existe
    # Estructura: { "sistemas": [ {"id_drive": "X", "frec": 12}, ... ], "software": [...] }
    indice_maestro = defaultdict(list)
    
    print(f"[*] Leyendo y agrupando datos de {archivo_ingesta}...")
    
    try:
        with open(archivo_ingesta, 'r', encoding='utf-8') as archivo:
            for numero_linea, linea in enumerate(archivo, 1):
                partes = linea.strip().split('|')
                if len(partes) != 2:
                    continue
                    
                id_drive, texto_crudo = partes
                
                palabras = texto_crudo.split(' ')
                
                # Contamos frecuencias en este documento específico
                frecuencias = Counter(palabras)
                
                # Alimentamos el índice maestro
                for palabra, frec in frecuencias.items():
                    if len(palabra) > 150:
                        palabra = palabra[:150]
                    if palabra:  # Evitar strings vacíos
                        indice_maestro[palabra].append({
                            "id_drive": id_drive,
                            "frecuencia": frec
                        })
                        
                if numero_linea % 1000 == 0:
                    print(f"    -> Procesados {numero_linea} documentos...")
                    
    except FileNotFoundError:
        print(f"[-] Error: No se encontró el archivo {archivo_ingesta}")
        return

    print(f"[*] Procesamiento en RAM completado. Ordenando y guardando en archivo plano...")
    
    # Guardamos en formato JSONL
    with open(archivo_indice, 'w', encoding='utf-8') as f_out:
        for palabra, lista_docs in indice_maestro.items():
            
            # OPTIMIZACIÓN: Ordenar los documentos de mayor a menor frecuencia
            # Esto hará que al buscar la palabra, los docs más relevantes ya vengan primero
            lista_docs.sort(key=lambda x: x["frecuencia"], reverse=True)
            
            registro = {
                "palabra": palabra,
                "documentos": lista_docs
            }
            
            # Escribir el objeto JSON como una sola línea en el archivo de texto
            f_out.write(json.dumps(registro) + "\n")
            
    print(f"[+] ¡Índice plano construido con éxito!")
    print(f"[+] Total de palabras únicas indexadas: {len(indice_maestro)}")
    print(f"[+] Archivo generado: {archivo_indice}")

if __name__ == '__main__':
    construir_indice_plano()