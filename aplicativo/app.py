import math
import re
import time
import psycopg2
from flask import Flask, request, jsonify, render_template

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

        # Obtener métricas generales para BM25 (N y avgdl)
        cursor.execute("SELECT COUNT(id), COALESCE(AVG(longitud), 1) FROM documentos_indexados WHERE procesado = TRUE;")
        n_docs_tuple = cursor.fetchone()
        N = float(n_docs_tuple[0]) if n_docs_tuple and n_docs_tuple[0] > 0 else 1.0
        avgdl = float(n_docs_tuple[1]) if n_docs_tuple and n_docs_tuple[1] > 0 else 1.0


        # 1. Consultar el índice inverso para las palabras de búsqueda
        query_sql = "SELECT palabra, documentos FROM indice_docu_uni WHERE palabra = ANY(%s);"
        cursor.execute(query_sql, (palabras,))
        rows = cursor.fetchall()

        # 2. Agrupar la información por documento y calcular IDF
        doc_scores = {}
        word_idf = {}
        all_matched_docs = set()

        for row in rows:
            palabra = row[0]
            documentos = row[1]  # Formato: [{'id_drive': '...', 'frecuencia': X}, ...]
            if not isinstance(documentos, list):
                continue
            
            n_qi = len(documentos)
            # Calcular IDF para la palabra (BM25)
            idf = math.log(((N - n_qi + 0.5) / (n_qi + 0.5)) + 1.0)
            word_idf[palabra] = idf

            for doc in documentos:
                id_drive = doc.get("id_drive")
                frecuencia = doc.get("frecuencia", 0)
                if not id_drive:
                    continue

                all_matched_docs.add(id_drive)

                if id_drive not in doc_scores:
                    doc_scores[id_drive] = {
                        "id_drive": id_drive,
                        "matched_words": [],
                        "term_frequencies": {}
                    }
                doc_scores[id_drive]["matched_words"].append(palabra)
                doc_scores[id_drive]["term_frequencies"][palabra] = frecuencia

        # Obtener longitudes de los documentos recuperados
        doc_lengths = {}
        if all_matched_docs:
            cursor.execute("SELECT id_drive, longitud FROM documentos_indexados WHERE id_drive = ANY(%s);", (list(all_matched_docs),))
            for r in cursor.fetchall():
                doc_lengths[r[0]] = r[1]

        # Calcular score BM25 para cada documento
        k1 = 1.5
        b = 0.75

        for doc_id, doc_data in doc_scores.items():
            bm25_score = 0.0
            doc_len = doc_lengths.get(doc_id, avgdl)
            if doc_len == 0: 
                doc_len = avgdl

            for palabra in doc_data["matched_words"]:
                tf = doc_data["term_frequencies"][palabra]
                idf = word_idf[palabra]
                
                numerator = tf * (k1 + 1)
                denominator = tf + k1 * (1 - b + b * (doc_len / avgdl))
                bm25_score += idf * (numerator / denominator)
                
            doc_data["bm25_score"] = bm25_score

        # 3. Clasificar y ordenar según los requerimientos
        # K es la cantidad de palabras únicas buscadas
        k = len(palabras)
        block_a = []  # Documentos con todas las palabras buscadas
        block_b = []  # Documentos con solo algunas de las palabras buscadas

        for doc in doc_scores.values():
            matched_count = len(doc["matched_words"])
            if matched_count == k:
                block_a.append(doc)
            elif matched_count > 0:
                block_b.append(doc)

        # Ordenar bloque A: Score BM25 de mayor a menor
        block_a.sort(key=lambda x: x["bm25_score"], reverse=True)

        # Ordenar bloque B: por número de coincidencias desc, luego Score BM25 desc
        block_b.sort(key=lambda x: (len(x["matched_words"]), x["bm25_score"]), reverse=True)

        # Combinar bloques: primero los de coincidencia total, luego los de coincidencia parcial
        sorted_docs = block_a + block_b
        total_results = len(sorted_docs)

        # 4. Paginar los resultados en bloques de 10
        limit = 10
        total_pages = math.ceil(total_results / limit)
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        page_docs = sorted_docs[start_idx:end_idx]

        # 5. Recuperar la información detallada de los documentos de la página actual
        results = []
        if page_docs:
            page_ids = [d["id_drive"] for d in page_docs]
            
            # Consultamos la tabla documentos_indexados para obtener metadatos
            details_sql = """
                SELECT id_drive, nombre_original, nombre_compuesto, categoria_principal, url_acceso 
                FROM documentos_indexados 
                WHERE id_drive = ANY(%s);
            """
            cursor.execute(details_sql, (page_ids,))
            db_rows = cursor.fetchall()

            # Mapear metadatos por id_drive
            details_map = {}
            for r in db_rows:
                details_map[r[0]] = {
                    "nombre_original": r[1],
                    "nombre_compuesto": r[2],
                    "categoria_principal": r[3],
                    "url_acceso": r[4]
                }

            # Construir la lista final respetando el orden exacto del ranking
            for doc in page_docs:
                id_drive = doc["id_drive"]
                if id_drive in details_map:
                    details = details_map[id_drive]
                    results.append({
                        "id_drive": id_drive,
                        "nombre_original": details["nombre_original"],
                        "nombre_compuesto": details["nombre_compuesto"],
                        "categoria_principal": details["categoria_principal"],
                        "url_acceso": details["url_acceso"],
                        "matched_words": doc["matched_words"],
                        "bm25_score": round(doc["bm25_score"], 4),
                        "match_type": "all" if len(doc["matched_words"]) == k else "some"
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
