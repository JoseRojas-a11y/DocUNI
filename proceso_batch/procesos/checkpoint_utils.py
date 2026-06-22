import os
import sys
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHECKPOINT_DIR = os.path.abspath(os.path.join(BASE_DIR, '..', 'checkpoints'))

def obtener_argumentos_reset() -> bool:
    """Verifica si se ha pasado el flag --reset o --from-scratch en los argumentos de terminal."""
    args = [arg.lower() for arg in sys.argv]
    return '--reset' in args or '--from-scratch' in args

def obtener_ruta_checkpoint(nombre_proceso: str) -> str:
    """Retorna la ruta absoluta al archivo de checkpoint para un proceso dado."""
    return os.path.join(CHECKPOINT_DIR, f"{nombre_proceso}_checkpoint.json")

def cargar_checkpoint(nombre_proceso: str, reset: bool = None) -> dict:
    """
    Carga el checkpoint de un proceso si existe y no se ha especificado un reset.
    Si se solicita reset, elimina el archivo de checkpoint actual (si existe) y retorna None.
    """
    if reset is None:
        reset = obtener_argumentos_reset()

    ruta = obtener_ruta_checkpoint(nombre_proceso)
    
    if reset:
        if os.path.exists(ruta):
            try:
                os.remove(ruta)
                print(f"[*] Flag --reset detectado. Checkpoint eliminado para {nombre_proceso}.")
            except Exception as e:
                print(f"[-] No se pudo eliminar el checkpoint para {nombre_proceso}: {e}")
        return None

    if os.path.exists(ruta):
        try:
            with open(ruta, 'r', encoding='utf-8') as f:
                datos = json.load(f)
            print(f"[+] Checkpoint cargado exitosamente para {nombre_proceso}.")
            return datos
        except Exception as e:
            print(f"[-] Error al leer checkpoint para {nombre_proceso}: {e}. Se ignorará.")
            return None
    return None

def guardar_checkpoint(nombre_proceso: str, datos: dict):
    """
    Guarda los datos del checkpoint en un archivo JSON de manera atómica
    (escribe en archivo temporal y luego renombra) para evitar corrupción por caídas de energía o interrupción.
    """
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    ruta = obtener_ruta_checkpoint(nombre_proceso)
    ruta_tmp = ruta + ".tmp"
    
    try:
        with open(ruta_tmp, 'w', encoding='utf-8') as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
        
        # Reemplazo atómico (en Windows puede requerir borrar el destino primero si existe)
        if os.path.exists(ruta):
            os.remove(ruta)
        os.rename(ruta_tmp, ruta)
    except Exception as e:
        print(f"[-] Error crítico al guardar checkpoint para {nombre_proceso}: {e}")
        if os.path.exists(ruta_tmp):
            try:
                os.remove(ruta_tmp)
            except Exception:
                pass

def truncar_archivo_por_lineas(ruta_archivo: str, lineas_a_mantener: int):
    """
    Asegura que el archivo en 'ruta_archivo' exista y lo trunca para mantener
    únicamente las primeras 'lineas_a_mantener' líneas.
    """
    if lineas_a_mantener is None:
        return
        
    os.makedirs(os.path.dirname(ruta_archivo), exist_ok=True)
    
    if not os.path.exists(ruta_archivo):
        with open(ruta_archivo, 'w', encoding='utf-8') as f:
            pass
        return

    try:
        lineas = []
        with open(ruta_archivo, 'r', encoding='utf-8', errors='ignore') as f:
            for _ in range(lineas_a_mantener):
                linea = f.readline()
                if not linea:
                    break
                lineas.append(linea)
                
        with open(ruta_archivo, 'w', encoding='utf-8') as f:
            f.writelines(lineas)
            
        print(f"[+] Archivo plano {os.path.basename(ruta_archivo)} truncado a {lineas_a_mantener} registros.")
    except Exception as e:
        print(f"[-] Error al truncar {os.path.basename(ruta_archivo)} a {lineas_a_mantener} registros: {e}")
        raise e

