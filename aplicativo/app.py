import math
import re
import time
import psycopg2
from flask import Flask, request, jsonify, render_template

# ============================================================
# DocUNI v3.0 - Motor de Búsqueda con Índice Atómico Posicional
# ============================================================
# Implementa:
# - Consulta al índice invertido atómico (1NF)
# - Intersección multi-palabra (semántica AND estricta)
# - BM25 con bonificación de proximidad para frases exactas
# - Snippets KWIC (Key Word In Context)
# - Deep Links con #page=N
# ============================================================

app = Flask(__name__, template_folder="templates", static_folder="static")

@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

from db_config import DB_CONFIG

def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)

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

@app.route("/")
def index():
    return render_template("index.html")

N_CACHE = None
AVGDL_CACHE = None

def get_corpus_stats(cursor):
    global N_CACHE, AVGDL_CACHE
    if N_CACHE is None or AVGDL_CACHE is None:
        cursor.execute("SELECT COUNT(*) FROM lineas_documento;")
        N_CACHE = cursor.fetchone()[0] or 1
        cursor.execute("SELECT COUNT(*) FROM indice_invertido_uni;")
        total_tokens = cursor.fetchone()[0] or 0
        AVGDL_CACHE = float(total_tokens) / float(N_CACHE) if N_CACHE > 0 else 1.0
    return N_CACHE, AVGDL_CACHE

def calcular_menor_distancia(posiciones_por_palabra: dict, palabras: list[str]) -> int:
    """
    Calcula la distancia absoluta mínima que separa a las palabras de la consulta 
    en la línea actual utilizando sus arreglos de posiciones.
    """
    if len(palabras) < 2:
        return 0
        
    import itertools
    posiciones_listas = [posiciones_por_palabra.get(p, []) for p in palabras]
    
    if any(not lst for lst in posiciones_listas):
        return 99999
        
    min_span = float('inf')
    for combo in itertools.product(*posiciones_listas):
        span = max(combo) - min(combo)
        if span < min_span:
            min_span = span
            
    distancia = min_span - (len(palabras) - 1)
    return int(distancia)

def generar_snippet_kwic(texto_linea: str, posiciones_por_palabra: dict) -> str:
    """
    Genera un fragmento de texto al estilo Google (KWIC):
    - Extrae las posiciones reales mapeándolas con re.finditer.
    - Recorta para mostrar un máximo de 8 palabras antes de la primera coincidencia 
      y 8 palabras después de la última coincidencia en la línea.
    - Envuelve los términos de búsqueda con etiquetas HTML <b>palabra</b>.
    """
    matches = list(re.finditer(r'\b[a-záéíóúñ0-9]+\b', texto_linea, re.IGNORECASE))
    if not matches:
        return texto_linea
        
    matched_token_indices = []
    for pos_lista in posiciones_por_palabra.values():
        matched_token_indices.extend(pos_lista)
        
    if not matched_token_indices:
        return texto_linea
        
    min_token_idx = max(0, min(matched_token_indices))
    max_token_idx = min(len(matches) - 1, max(matched_token_indices))
    
    start_token_idx = max(0, min_token_idx - 8)
    end_token_idx = min(len(matches) - 1, max_token_idx + 8)
    
    start_char = matches[start_token_idx].start()
    end_char = matches[end_token_idx].end()
    
    snippet_raw = texto_linea[start_char:end_char]
    
    highlight_ranges = []
    for pos in matched_token_indices:
        if 0 <= pos < len(matches):
            m = matches[pos]
            highlight_ranges.append((m.start() - start_char, m.end() - start_char))
            
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

@app.route("/api/search")
def search():
    start_time = time.time()
    query_str = request.args.get("q", "").strip()
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        page = 1

    if page < 1:
        page = 1

    palabras = extraer_palabras(query_str)
    palabras = list(set(palabras))

    if not palabras:
        time_taken = time.time() - start_time
        return jsonify({
            "results": [],
            "total_results": 0,
            "page": page,
            "pages": 0,
            "query_words": [],
            "time_taken": round(time_taken, 4)
        })

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        N, avgdl = get_corpus_stats(cursor)

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
            HAVING COUNT(DISTINCT i.palabra) = %s
        """
        cursor.execute(query, (palabras, len(palabras)))
        candidatos = cursor.fetchall()

        if not candidatos:
            time_taken = time.time() - start_time
            return jsonify({
                "results": [],
                "total_results": 0,
                "page": page,
                "pages": 0,
                "query_words": palabras,
                "time_taken": round(time_taken, 4)
            })

        k1 = 1.2
        b = 0.75
        
        idf_dict = {}
        for palabra in palabras:
            df = df_dict.get(palabra, 0)
            idf = math.log(((N - df + 0.5) / (df + 0.5)) + 1.0)
            idf_dict[palabra] = idf

        ranking = []
        for fila in candidatos:
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
            
            if distancia <= 3:
                multiplicador_proximidad = 1.0 + (1.0 / (distancia + 1))
            else:
                multiplicador_proximidad = 1.0
                
            score_total = score_bm25 * multiplicador_proximidad
            
            ranking.append({
                "id_linea": id_linea,
                "nombre_compuesto": nombre_compuesto,
                "numero_linea": numero_linea,
                "texto_linea": texto_linea,
                "url_acceso": url_acceso,
                "score_total": score_total,
                "posiciones": posiciones_por_palabra
            })

        ranking.sort(key=lambda x: x["score_total"], reverse=True)
        total_results = len(ranking)

        limit = 10
        total_pages = math.ceil(total_results / limit)
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        current_page_items = ranking[start_idx:end_idx]

        results = []
        for item in current_page_items:
            snippet = generar_snippet_kwic(item["texto_linea"], item["posiciones"])
            deep_link = f"{item['url_acceso']}#line={item['numero_linea']}"
            
            results.append({
                "id_linea": item["id_linea"],
                "nombre_compuesto": item["nombre_compuesto"],
                "numero_linea": item["numero_linea"],
                "snippet": snippet,
                "url_destino": deep_link,
                "matched_words": list(item["posiciones"].keys()),
                "bm25_score": round(item["score_total"], 4),
                "match_type": "all"
            })

        time_taken = time.time() - start_time
        return jsonify({
            "results": results,
            "total_results": total_results,
            "page": page,
            "pages": total_pages,
            "query_words": palabras,
            "time_taken": round(time_taken, 4)
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
