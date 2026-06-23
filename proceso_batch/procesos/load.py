import time
import sys

# Configurar stdout y stderr para usar UTF-8 y evitar errores de codificación en Windows
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from checkpoint_utils import obtener_argumentos_reset
from load_documentos import cargar_documentos
from load_lineas import cargar_lineas
from load_indice import cargar_indice

def main():
    print("============================================================")
    print("DocUNI v3.0 - Orquestador de Carga Masiva (Load)")
    print("============================================================")
    
    reset = obtener_argumentos_reset()
    if reset:
        print("[*] Flag --reset detectado. Iniciando toda la carga desde cero.")
    
    t_inicio = time.time()
    
    # 1. Cargar documentos
    t_docs = time.time()
    docs_cargados = cargar_documentos(reset=reset)
    d_docs = time.time() - t_docs
    print(f"[OK] Carga de documentos finalizada en {d_docs:.2f}s.\n")
    
    # 2. Cargar páginas
    t_pags = time.time()
    pags_cargadas = cargar_lineas(reset=reset)
    d_pags = time.time() - t_pags
    print(f"[OK] Carga de páginas finalizada en {d_pags:.2f}s.\n")
    
    # 3. Cargar índice invertido
    t_ind = time.time()
    tokens_cargados = cargar_indice(reset=reset)
    d_ind = time.time() - t_ind
    print(f"[OK] Carga de índice finalizada en {d_ind:.2f}s.\n")
    
    duracion_total = time.time() - t_inicio
    print("============================================================")
    print("RESUMEN DE CARGA MASIVA:")
    print(f"  - Documentos indexados: {docs_cargados}")
    print(f"  - Páginas procesadas:   {pags_cargadas}")
    print(f"  - Tokens indexados:     {tokens_cargados}")
    print(f"  - Tiempo total de carga: {duracion_total:.2f} segundos")
    print("============================================================")

if __name__ == '__main__':
    main()