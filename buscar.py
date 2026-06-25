import sys
import os
import re
import math
import psycopg2
from db_config import DB_CONFIG

def normalizar_palabra(palabra: str) -> str:
    """
    Aplica normalización avanzada a la palabra para que coincida con el índice:
    - Limita longitud a 150 caracteres.
    - Convierte a minúsculas.
    - Remueve acentos y tildes en español.
    - Corrige errores típicos de OCR (por ejemplo, 'ien' al final de palabras de más de 4 letras se convierte en 'ion').
    - Elimina la 's' final para normalizar plurales en palabras de más de 3 letras.
    """
    if len(palabra) > 150:
        palabra = palabra[:150]

    palabra = palabra.lower()

    tildes = {'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u', 'ü': 'u'}
    for t, r in tildes.items():
        palabra = palabra.replace(t, r)

    if palabra.endswith("ien") and len(palabra) > 4:
        palabra = palabra[:-3] + "ion"

    if palabra.endswith("s") and len(palabra) > 3:
        palabra = palabra[:-1]

    return palabra

def extraer_palabras(texto: str) -> list[str]:
    """
    Normaliza y tokeniza el texto: convierte a minúsculas y filtra 
    para mantener únicamente caracteres alfanuméricos en español.
    """
    if not texto:
        return []
    palabras_crudas = re.findall(r'\b[a-záéíóúñ0-9]+\b', texto.lower())
    return [normalizar_palabra(p) for p in palabras_crudas if p]

def obtener_estadisticas_corpus(cursor) -> tuple[int, float]:
    """
    Consulta a la base de datos el total de líneas (N) y el promedio de palabras 
    por línea (avgdl) de forma global en todo el corpus.
    """
    cursor.execute("SELECT COUNT(*) FROM lineas_documento;")
    N = cursor.fetchone()[0] or 1
    
    cursor.execute("SELECT COUNT(*) FROM indice_invertido_uni;")
    total_tokens = cursor.fetchone()[0] or 0
    
    avgdl = float(total_tokens) / float(N) if N > 0 else 1.0
    return N, avgdl

def obtener_frecuencia_documental(cursor, palabras: list[str]) -> dict[str, int]:
    """
    Obtiene para cada palabra buscada el número de líneas únicas que la contienen (DF).
    Esto es necesario para el cálculo de la métrica IDF de BM25.
    """
    cursor.execute("""
        SELECT palabra, COUNT(DISTINCT id_linea) 
        FROM indice_invertido_uni 
        WHERE palabra = ANY(%s) 
        GROUP BY palabra;
    """, (palabras,))
    
    df_dict = {row[0]: row[1] for row in cursor.fetchall()}
    for p in palabras:
        if p not in df_dict:
            df_dict[p] = 0
    return df_dict

def ejecutar_consulta_sql(cursor, palabras: list[str]) -> list[tuple]:
    """
    Consulta SQL que realiza la búsqueda en el índice invertido.
    Devuelve, para las líneas que contienen al menos una de las palabras buscadas,
    el id_linea, id_drive, numero_linea, texto_linea, nombre_compuesto, url_acceso,
    la frecuencia total calculada al vuelo con COUNT() y el diccionario de posiciones
    por cada palabra en formato JSON.
    """
    query = """
        SELECT 
            i.id_linea,
            l.id_drive,
            l.numero_fila AS numero_linea,
            l.texto_fila AS texto_linea,
            d.nombre_compuesto,
            d.url_acceso,
            COUNT(r.posicion) AS frecuencia_total,
            jsonb_object_agg(i.palabra, i.posiciones) AS posiciones_por_palabra
        FROM (
            SELECT 
                id_linea, 
                palabra, 
                array_agg(posicion ORDER BY posicion) AS posiciones
            FROM indice_invertido_uni
            WHERE palabra = ANY(%s)
            GROUP BY id_linea, palabra
        ) i
        JOIN indice_invertido_uni r ON r.id_linea = i.id_linea AND r.palabra = i.palabra
        JOIN lineas_documento l ON i.id_linea = l.id_linea
        JOIN documentos_indexados d ON l.id_drive = d.id_drive
        GROUP BY i.id_linea, l.id_drive, l.numero_fila, l.texto_fila, d.nombre_compuesto, d.url_acceso
    """
    cursor.execute(query, (palabras,))
    return cursor.fetchall()

def calcular_menor_distancia(posiciones_por_palabra: dict, palabras: list[str]) -> int:
    """
    Calcula la distancia absoluta mínima que separa a las palabras de la consulta 
    en la línea actual utilizando sus arreglos de posiciones (considerando solo las palabras presentes).
    
    Explicación de la lógica de proximidad (Fórmula de Proximidad):
    1. Si hay menos de 2 palabras en la consulta, la distancia siempre es 0.
    2. Si están al menos dos palabras de la consulta presentes, extraemos las listas de posiciones asociadas.
    3. Usamos combinaciones cartesianas (itertools.product) para evaluar todos los posibles
       conjuntos de posiciones formados al elegir una posición para cada palabra presente.
    4. Para cada combinación, el "span" o ventana de la frase es la diferencia entre la 
       posición máxima y la mínima encontrada: span = max(posiciones) - min(posiciones).
    5. El menor span posible para 'k' palabras diferentes es 'k - 1' (cuando aparecen adyacentes).
    6. Por lo tanto, definimos la distancia absoluta adicional entre las palabras como:
       distancia = min_span - (k - 1).
       - Si están juntas (adyacentes), la distancia es 0.
       - Si hay palabras intermedias, la distancia aumenta de forma lineal.
    """
    if len(palabras) < 2:
        return 0
        
    palabras_presentes = [p for p in palabras if p in posiciones_por_palabra and posiciones_por_palabra[p]]
    if len(palabras_presentes) < 2:
        return 99999
        
    import itertools
    posiciones_listas = [posiciones_por_palabra[p] for p in palabras_presentes]
        
    min_span = float('inf')
    for combo in itertools.product(*posiciones_listas):
        span = max(combo) - min(combo)
        if span < min_span:
            min_span = span
            
    distancia = min_span - (len(palabras_presentes) - 1)
    return int(distancia)

def calcular_relevancia(
    lineas_candidatas: list, 
    df_dict: dict, 
    N: int, 
    avgdl: float, 
    palabras: list[str]
) -> list[dict]:
    """
    Aplica el algoritmo de ranking híbrido BM25 + Bono de Proximidad a los candidatos.
    """
    k1 = 1.2
    b = 0.75
    resultados = []
    
    idf_dict = {}
    for palabra in palabras:
        df = df_dict.get(palabra, 0)
        idf = math.log(((N - df + 0.5) / (df + 0.5)) + 1.0)
        idf_dict[palabra] = idf

    for fila in lineas_candidatas:
        id_linea, id_drive, numero_linea, texto_linea, nombre_compuesto, url_acceso, frec_total, posiciones_por_palabra = fila
        
        score_bm25 = 0.0
        doc_len = len(extraer_palabras(texto_linea))
        if doc_len == 0:
            doc_len = 1
            
        for palabra in palabras:
            pos_lista = posiciones_por_palabra.get(palabra, [])
            tf = len(pos_lista)
            idf = idf_dict.get(palabra, 0.0)
            
            numerador = tf * (k1 + 1)
            denominador = tf + k1 * (1.0 - b + b * (doc_len / avgdl))
            score_bm25 += idf * (numerador / denominador)
            
        distancia = calcular_menor_distancia(posiciones_por_palabra, palabras)
        
        # --- EXPLICACIÓN DE LA FÓRMULA DE BONO ---
        # Si la distancia absoluta adicional entre las palabras buscadas es <= 3:
        # Aplicamos un multiplicador lineal de bonificación significativa sobre el Score BM25:
        #   multiplicador = 1.0 + (1.0 / (distancia + 1))
        # Esto resulta en:
        #   - Distancia = 0 -> Bono de 2.0x (100% de aumento).
        #   - Distancia = 1 -> Bono de 1.5x (50% de aumento).
        #   - Distancia = 2 -> Bono de 1.33x (33% de aumento).
        #   - Distancia = 3 -> Bono de 1.25x (25% de aumento).
        # Si la distancia > 3, el multiplicador es 1.0 (sin bono).
        if distancia <= 3:
            multiplicador_proximidad = 1.0 + (1.0 / (distancia + 1))
        else:
            multiplicador_proximidad = 1.0
            
        score_total = score_bm25 * multiplicador_proximidad
        
        resultados.append({
            "id_linea": id_linea,
            "id_drive": id_drive,
            "numero_linea": numero_linea,
            "texto_linea": texto_linea,
            "nombre_compuesto": nombre_compuesto,
            "url_acceso": url_acceso,
            "score_bm25": score_bm25,
            "distancia": distancia,
            "multiplicador": multiplicador_proximidad,
            "score_total": score_total,
            "posiciones": posiciones_por_palabra
        })
        
    resultados.sort(key=lambda x: x["score_total"], reverse=True)
    return resultados

def generar_snippet_kwic(texto_linea: str, posiciones_por_palabra: dict) -> str:
    """
    Genera un fragmento de texto al estilo Google (KWIC):
    - Extrae las posiciones reales mapeándolas con re.finditer.
    - Recorta para mostrar un máximo de 8 palabras antes de la primera coincidencia 
      y 8 palabras después de la última coincidencia en la línea.
    - Envuelve los términos de búsqueda con etiquetas HTML <b>palabra</b>.
    """
    # Encontrar todas las palabras alfanuméricas con su índice de caracteres
    matches = list(re.finditer(r'\b[a-záéíóúñ0-9]+\b', texto_linea, re.IGNORECASE))
    if not matches:
        return texto_linea
        
    # Recolectar todos los índices de token que coinciden
    matched_token_indices = []
    for pos_lista in posiciones_por_palabra.values():
        matched_token_indices.extend(pos_lista)
        
    if not matched_token_indices:
        return texto_linea
        
    min_token_idx = max(0, min(matched_token_indices))
    max_token_idx = min(len(matches) - 1, max(matched_token_indices))
    
    # Ventana de recorte: 8 tokens antes y después
    start_token_idx = max(0, min_token_idx - 8)
    end_token_idx = min(len(matches) - 1, max_token_idx + 8)
    
    # Obtener rangos de caracteres
    start_char = matches[start_token_idx].start()
    end_char = matches[end_token_idx].end()
    
    snippet_raw = texto_linea[start_char:end_char]
    
    # Colectar los rangos de caracteres a resaltar en snippet_raw
    highlight_ranges = []
    for pos in matched_token_indices:
        if 0 <= pos < len(matches):
            m = matches[pos]
            highlight_ranges.append((m.start() - start_char, m.end() - start_char))
            
    # Ordenar rangos de forma descendente para no desajustar los índices al insertar etiquetas
    highlight_ranges.sort(key=lambda r: r[0], reverse=True)
    
    snippet_chars = list(snippet_raw)
    for start, end in highlight_ranges:
        snippet_chars.insert(end, "</b>")
        snippet_chars.insert(start, "<b>")
        
    snippet = "".join(snippet_chars)
    
    if start_char > 0:
        snippet = "..." + snippet
    if end_char < len(texto_linea):
        snippet = snippet + "..."
        
    return snippet

def realizar_busqueda(query_str: str):
    """
    Ejecuta el pipeline completo de búsqueda a partir de la consulta ingresada.
    """
    palabras = extraer_palabras(query_str)
    palabras = list(set(palabras))
    
    if not palabras:
        print("\n[-] Búsqueda vacía. Ingrese palabras válidas.")
        return
        
    print(f"\n[*] Buscando: {palabras}...")
    
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        N, avgdl = obtener_estadisticas_corpus(cursor)
        df_dict = obtener_frecuencia_documental(cursor, palabras)
        candidatos = ejecutar_consulta_sql(cursor, palabras)
        
        if not candidatos:
            print("[-] No se encontraron resultados para los términos de búsqueda.")
            return
            
        ranking = calcular_relevancia(candidatos, df_dict, N, avgdl, palabras)
        
        print(f"\n[+] Resultados encontrados: {len(ranking)} | Mostrando el Top 10:")
        print("=" * 80)
        
        top_10 = ranking[:10]
        for idx, item in enumerate(top_10, start=1):
            snippet = generar_snippet_kwic(item["texto_linea"], item["posiciones"])
            deep_link = f"{item['url_acceso']}#line={item['numero_linea']}"
            
            print(f"{idx}. TÍTULO: {item['nombre_compuesto']}")
            print(f"   SNIPPET: {snippet}")
            print(f"   ENLACE:  {deep_link}")
            print(f"   METRICAS: Score Total: {item['score_total']:.4f} (BM25: {item['score_bm25']:.4f}, Distancia: {item['distancia']}, Bono: {item['multiplicador']:.2f}x)")
            print("-" * 80)
            
    except Exception as e:
        print(f"[-] Error en el proceso de búsqueda: {e}")
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conn' in locals() and conn:
            conn.close()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        realizar_busqueda(query)
    else:
        print("=" * 60)
        print(" DocUNI v3.0 - Motor de Búsqueda 1NF por Líneas (Consola)")
        print("=" * 60)
        while True:
            try:
                query = input("\nIngrese su búsqueda (o presione Ctrl+C para salir): ").strip()
                if not query:
                    continue
                realizar_busqueda(query)
            except KeyboardInterrupt:
                print("\n\n[*] Saliendo del buscador. ¡Hasta luego!")
                break
