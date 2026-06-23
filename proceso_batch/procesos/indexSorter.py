import os
import sys
import json
import time

# Configurar stdout y stderr para usar UTF-8 y evitar errores de codificación en Windows
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Rutas resueltas de manera absoluta respecto a la ubicación de este script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
rutaIndiceUniTemp = os.path.abspath(os.path.join(BASE_DIR, '..', 'archivos_planos', 'indice_invertido_uni_temp.jsonl'))
rutaIndiceUni = os.path.abspath(os.path.join(BASE_DIR, '..', 'archivos_planos', 'indice_invertido_uni.jsonl'))

def ordenar_indice():
    if not os.path.exists(rutaIndiceUniTemp):
        print(f"[-] No se encontró {rutaIndiceUniTemp}. Ejecuta indexMaker.py primero.")
        return

    print("============================================================")
    print("DocUNI v3.0 - Index Sorter (Ordenamiento)")
    print("============================================================")
    print(f"[*] Leyendo índice temporal desde: {rutaIndiceUniTemp}")
    print(f"[*] Ordenando y escribiendo índice final en: {rutaIndiceUni}")
    
    t_inicio = time.time()
    
    tokens = []
    
    # 1. Leer todas las líneas y parsear en tuplas (palabra, id_linea, posicion)
    print("[*] Cargando tokens en memoria...")
    with open(rutaIndiceUniTemp, 'r', encoding='utf-8') as f:
        for line in f:
            line_str = line.strip()
            if line_str:
                try:
                    data = json.loads(line_str)
                    tokens.append((
                        data["palabra"],
                        data["id_linea"],
                        data["posicion"]
                    ))
                except Exception as e:
                    print(f"[-] Error parseando línea temporal: {e}")
                    
    print(f"[+] {len(tokens)} tokens cargados en memoria. Ordenando...")
    
    # 2. Ordenar las tuplas. Python sort() ordena tuplas elemento a elemento por defecto:
    # primero palabra (lexicográfico), luego id_linea (numérico), luego posicion (numérico).
    tokens.sort()
    
    print("[*] Escribiendo índice ordenado a disco...")
    # 3. Escribir los tokens ordenados en rutaIndiceUni
    os.makedirs(os.path.dirname(rutaIndiceUni), exist_ok=True)
    with open(rutaIndiceUni, 'w', encoding='utf-8') as f_out:
        for tok in tokens:
            data_out = {
                "palabra": tok[0],
                "id_linea": tok[1],
                "posicion": tok[2]
            }
            f_out.write(json.dumps(data_out, ensure_ascii=False) + '\n')
            
    duracion = time.time() - t_inicio
    print("============================================================")
    print(f"[*] ¡Ordenamiento de índice finalizado en {duracion:.2f}s!")
    print(f"  - Total de tokens ordenados y escritos: {len(tokens)}")
    print("============================================================")

if __name__ == '__main__':
    ordenar_indice()
