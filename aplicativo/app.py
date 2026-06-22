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

def extraer_palabras(texto: str) -> list[str]:
    """Normaliza y tokeniza el texto: minúsculas + solo alfanuméricos en español."""
    if not texto:
        return []
    texto_limpio = texto.lower()
    # Mismo regex de extracción que dataIngestion.py para coincidir con la indexación
    return re.findall(r'\b[a-záéíóúñ0-9]+\b', texto_limpio)

@app.route("/")
def index():
    return render_template("index.html")

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
    # Evitar duplicados en las palabras buscadas
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

        # ============================================================
        # PASO 1: Consulta al índice atómico - Cálculo de TF y posiciones
        # ============================================================
        # Para cada palabra buscada, obtenemos las páginas donde aparece,
        # la frecuencia (COUNT) y las posiciones exactas (ARRAY_AGG)
        tf_data = {}  # { palabra: { id_pagina: { "frecuencia": N, "posiciones": [...] } } }

        for palabra in palabras:
            cursor.execute("""
                SELECT 
                    id_pagina, 
                    COUNT(*) AS frecuencia, 
                    ARRAY_AGG(posicion ORDER BY posicion) AS lista_posiciones 
                FROM indice_invertido_uni 
                WHERE palabra = %s 
                GROUP BY id_pagina;
            """, (palabra,))
            rows = cursor.fetchall()
            tf_data[palabra] = {}
            for row in rows:
                tf_data[palabra][row[0]] = {
                    "frecuencia": row[1],
                    "posiciones": row[2]
                }

        # ============================================================
        # PASO 2: Intersección multi-palabra
        # ============================================================
        # Encontrar qué páginas contienen cuáles palabras
        all_page_ids = set()
        page_word_map = {}  # { id_pagina: set(palabras) }

        for palabra, paginas_dict in tf_data.items():
            for id_pagina in paginas_dict:
                all_page_ids.add(id_pagina)
                if id_pagina not in page_word_map:
                    page_word_map[id_pagina] = set()
                page_word_map[id_pagina].add(palabra)

        if not all_page_ids:
            time_taken = time.time() - start_time
            return jsonify({
                "results": [],
                "total_results": 0,
                "page": page,
                "pages": 0,
                "query_words": palabras,
                "time_taken": round(time_taken, 4)
            })

        # ============================================================
        # PASO 3: Obtener métricas para BM25 (N y avgdl)
        # ============================================================
        # N = total de páginas indexadas (usamos páginas como "documentos")
        cursor.execute("SELECT COUNT(*) FROM paginas_documento;")
        N = float(cursor.fetchone()[0]) or 1.0

        # avgdl = longitud promedio de todas las páginas (contando tokens del índice)
        cursor.execute("""
            SELECT COALESCE(AVG(cnt), 1) FROM (
                SELECT id_pagina, COUNT(*) AS cnt 
                FROM indice_invertido_uni 
                GROUP BY id_pagina
            ) sub;
        """)
        avgdl = float(cursor.fetchone()[0]) or 1.0

        # Obtener longitud de cada página candidata
        page_ids_list = list(all_page_ids)
        cursor.execute("""
            SELECT id_pagina, COUNT(*) AS longitud 
            FROM indice_invertido_uni 
            WHERE id_pagina = ANY(%s) 
            GROUP BY id_pagina;
        """, (page_ids_list,))
        page_lengths = {}
        for row in cursor.fetchall():
            page_lengths[row[0]] = row[1]

        # ============================================================
        # PASO 4: Cálculo de BM25 con bonificación de proximidad
        # ============================================================
        k1 = 1.2
        b = 0.75

        # Precalcular IDF para cada palabra
        word_idf = {}
        for palabra in palabras:
            n_qi = len(tf_data.get(palabra, {}))
            idf = math.log(((N - n_qi + 0.5) / (n_qi + 0.5)) + 1.0)
            word_idf[palabra] = idf

        page_scores = []

        for id_pagina in all_page_ids:
            bm25_score = 0.0
            doc_len = page_lengths.get(id_pagina, avgdl)
            if doc_len == 0:
                doc_len = avgdl

            matched_words = list(page_word_map.get(id_pagina, set()))

            for palabra in matched_words:
                tf = tf_data[palabra][id_pagina]["frecuencia"]
                idf = word_idf[palabra]

                numerator = tf * (k1 + 1)
                denominator = tf + k1 * (1 - b + b * (doc_len / avgdl))
                bm25_score += idf * (numerator / denominator)

            # Bonificación de proximidad para frases exactas
            # Si hay múltiples palabras, verificar si las posiciones son consecutivas
            if len(matched_words) > 1 and len(matched_words) == len(palabras):
                proximity_bonus = calcular_bonus_proximidad(
                    matched_words, id_pagina, tf_data
                )
                bm25_score *= proximity_bonus

            page_scores.append({
                "id_pagina": id_pagina,
                "bm25_score": bm25_score,
                "matched_words": matched_words,
                "matched_count": len(matched_words)
            })

        # ============================================================
        # PASO 5: Clasificar y ordenar (Bloque A + Bloque B)
        # ============================================================
        k = len(palabras)
        block_a = []  # Páginas con TODAS las palabras buscadas
        block_b = []  # Páginas con solo ALGUNAS palabras

        for ps in page_scores:
            if ps["matched_count"] == k:
                block_a.append(ps)
            elif ps["matched_count"] > 0:
                block_b.append(ps)

        block_a.sort(key=lambda x: x["bm25_score"], reverse=True)
        block_b.sort(key=lambda x: (x["matched_count"], x["bm25_score"]), reverse=True)

        sorted_pages = block_a + block_b
        total_results = len(sorted_pages)

        # ============================================================
        # PASO 6: Paginación (bloques de 10)
        # ============================================================
        limit = 10
        total_pages = math.ceil(total_results / limit)
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        current_page_items = sorted_pages[start_idx:end_idx]

        # ============================================================
        # PASO 7: Recuperar metadatos + generar Snippets KWIC + Deep Links
        # ============================================================
        results = []
        if current_page_items:
            page_ids_current = [p["id_pagina"] for p in current_page_items]

            # JOIN entre paginas_documento y documentos_indexados
            cursor.execute("""
                SELECT 
                    p.id_pagina,
                    p.id_drive,
                    p.numero_pagina,
                    p.texto_pagina,
                    d.nombre_compuesto,
                    d.url_acceso
                FROM paginas_documento p
                INNER JOIN documentos_indexados d ON p.id_drive = d.id_drive
                WHERE p.id_pagina = ANY(%s);
            """, (page_ids_current,))

            details_map = {}
            for row in cursor.fetchall():
                details_map[row[0]] = {
                    "id_drive": row[1],
                    "numero_pagina": row[2],
                    "texto_pagina": row[3],
                    "nombre_compuesto": row[4],
                    "url_acceso": row[5]
                }

            # Construir la lista final respetando el orden del ranking
            for ps in current_page_items:
                id_pagina = ps["id_pagina"]
                if id_pagina not in details_map:
                    continue

                details = details_map[id_pagina]

                # Generar Snippet KWIC
                primera_posicion = None
                for palabra in ps["matched_words"]:
                    if palabra in tf_data and id_pagina in tf_data[palabra]:
                        posiciones = tf_data[palabra][id_pagina]["posiciones"]
                        if posiciones:
                            if primera_posicion is None or posiciones[0] < primera_posicion:
                                primera_posicion = posiciones[0]

                snippet = generar_snippet_kwic(
                    details["texto_pagina"],
                    primera_posicion,
                    ps["matched_words"]
                )

                # Deep Link con #page=N
                url_destino = details["url_acceso"]
                if details["numero_pagina"]:
                    url_destino = f"{details['url_acceso']}#page={details['numero_pagina']}"

                results.append({
                    "id_pagina": id_pagina,
                    "nombre_compuesto": details["nombre_compuesto"],
                    "numero_pagina": details["numero_pagina"],
                    "snippet": snippet,
                    "url_destino": url_destino,
                    "matched_words": ps["matched_words"],
                    "bm25_score": round(ps["bm25_score"], 4),
                    "match_type": "all" if ps["matched_count"] == k else "some"
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


def calcular_bonus_proximidad(matched_words: list, id_pagina: int, tf_data: dict) -> float:
    """
    Calcula la bonificación multiplicativa por proximidad.
    Si las posiciones relativas de los términos difieren en exactamente 1 unidad
    (frase exacta), se aplica un multiplicador al score BM25.
    """
    if len(matched_words) < 2:
        return 1.0

    # Obtener las posiciones de cada palabra en esta página
    word_positions = {}
    for palabra in matched_words:
        if palabra in tf_data and id_pagina in tf_data[palabra]:
            word_positions[palabra] = tf_data[palabra][id_pagina]["posiciones"]

    if len(word_positions) < 2:
        return 1.0

    # Verificar si existe algún par de posiciones consecutivas entre las palabras
    palabras_lista = list(word_positions.keys())
    pares_consecutivos = 0

    for i in range(len(palabras_lista)):
        for j in range(i + 1, len(palabras_lista)):
            posiciones_a = set(word_positions[palabras_lista[i]])
            posiciones_b = set(word_positions[palabras_lista[j]])

            # Verificar si alguna posición de A está justo antes de alguna de B (o viceversa)
            for pos_a in posiciones_a:
                if (pos_a + 1) in posiciones_b or (pos_a - 1) in posiciones_b:
                    pares_consecutivos += 1
                    break

    # Bonificación proporcional al número de pares adyacentes encontrados
    max_pares = len(palabras_lista) - 1
    if pares_consecutivos > 0:
        # Multiplicador: 1.5x a 2.0x según cuántos pares consecutivos se encuentren
        bonus = 1.0 + (0.5 * (pares_consecutivos / max_pares))
        return min(bonus, 2.0)

    return 1.0


def generar_snippet_kwic(texto_pagina: str, posicion_ancla: int, palabras_clave: list, ventana: int = 10) -> str:
    """
    Genera un snippet KWIC (Key Word In Context) a partir del texto de la página.
    Recorta una ventana de ±ventana palabras alrededor del punto de anclaje
    y resalta las palabras clave con etiquetas <mark>.
    """
    if not texto_pagina:
        return ""

    palabras_texto = texto_pagina.split()

    if not palabras_texto:
        return ""

    # Si no hay posición de anclaje válida, usar el inicio
    if posicion_ancla is None or posicion_ancla < 0:
        posicion_ancla = 0

    # Ajustar si la posición excede el texto
    if posicion_ancla >= len(palabras_texto):
        posicion_ancla = max(0, len(palabras_texto) - 1)

    # Calcular los límites de la ventana
    inicio = max(0, posicion_ancla - ventana)
    fin = min(len(palabras_texto), posicion_ancla + ventana + 1)

    fragmento = palabras_texto[inicio:fin]

    # Normalizar las palabras clave para la comparación
    palabras_clave_lower = set(p.lower() for p in palabras_clave)

    # Resaltar las palabras clave con <mark>
    fragmento_resaltado = []
    for palabra in fragmento:
        palabra_limpia = re.sub(r'[^a-záéíóúñ0-9]', '', palabra.lower())
        if palabra_limpia in palabras_clave_lower:
            fragmento_resaltado.append(f"<mark>{palabra}</mark>")
        else:
            fragmento_resaltado.append(palabra)

    # Construir el snippet con puntos suspensivos si fue recortado
    snippet = " ".join(fragmento_resaltado)
    if inicio > 0:
        snippet = "..." + snippet
    if fin < len(palabras_texto):
        snippet = snippet + "..."

    return snippet


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
