# Documentación Arquitectónica: Motor de Búsqueda DocuUNI

**Versión:** 3.0 - Arquitectura Relacional Pura (1NF) con Índice Invertido Atómico **Stack Tecnológico:** Python 3.10+, PostgreSQL, Apache Tika, Google Drive API v3. Se incorporan las librerías `easyocr`, `opencv-python`, `Pillow`, y `pdf2image` (para convertir páginas de PDF escaneados en imágenes antes del OCR).

## 1. Visión General del Sistema

DocuUNI implementa un motor de búsqueda basado en una arquitectura de dos capas (Separación de Índice y Almacén). Esta versión se apega estrictamente al paradigma relacional (Primera Forma Normal), modelando el Índice Invertido mediante atomicidad total. Cada aparición individual de un término en un documento representa una única fila en la base de datos. Esta estructura permite operaciones SQL clásicas, minimiza la complejidad de tipos de datos anidados (como JSONB) y delega el cálculo de métricas (como la frecuencia de término - TF) al momento de la consulta (Read-time computation).

## 2. Esquema de Base de Datos (Entity-Relationship)

La base de datos se normaliza en tres tablas principales, garantizando la integridad referencial y eliminando campos multivaluados.

### 2.1. Tabla: `documentos_indexados` (Metadatos)

Almacena la identidad global del archivo.

|Columna|Tipo|Descripción|
|---|---|---|
|`id_drive`|`VARCHAR(36)`|**Primary Key**. Identificador de Google Drive.|
|`nombre_compuesto`|`VARCHAR(200)`|Ruta y nombre original del material.|
|`url_acceso`|`VARCHAR(200)`|Enlace webViewLink original para redirección.|
|`procesado`|`BOOLEAN`|Bandera para controlar la ingesta concurrente.|
|`primera_carpeta`|`VARCHAR(70)`|Indica el nombre de la primera carpeta donde se encuentra el archivo|

### 2.2. Tabla: `paginas_documento` (Forward Index / Almacén de Texto)

Almacena el texto fragmentado para aislar el contexto y permitir la generación de Snippets.

|Columna|Tipo|Descripción|
|---|---|---|
|`id_pagina`|`SERIAL`|**Primary Key**. Identificador único del fragmento.|
|`id_drive`|`VARCHAR(100)`|**Foreign Key**. Referencia al documento padre.|
|`numero_pagina`|`INTEGER`|Número de página real en el archivo físico.|
|`texto_pagina`|`TEXT`|Texto crudo utilizado para recortes (KWIC).|

### 2.3. Tabla: `indice_invertido_uni` (Índice Posicional Atómico)

El núcleo del motor. Registra cada token (palabra) individual en su posición exacta.

|Columna|Tipo|Descripción|
|---|---|---|
|`palabra`|`VARCHAR(150)`|**Primary Key (1/3)**. Término limpio y normalizado.|
|`id_pagina`|`INTEGER`|**Primary Key (2/3)**. **Foreign Key**. Página donde aparece.|
|`posicion`|`INTEGER`|**Primary Key (3/3)**. Índice numérico de la palabra en el texto.|

**Nota sobre la Llave Primaria (PK):** La combinación de `(palabra, id_pagina, posicion)` asegura que una misma palabra no pueda registrarse dos veces en la misma posición exacta de la misma página, garantizando la unicidad atómica.

**Índice Crítico de Búsqueda:** Para agilizar la recuperación y las uniones (JOINs), se recomienda crear un índice B-Tree sobre el término:

```
CREATE INDEX idx_palabra_busqueda ON indice_invertido_uni (palabra);
```

## 3. Flujo de Ingesta y Procesamiento (Pipeline Backend)

El backend implementa un enrutamiento inteligente basado en la naturaleza del archivo binario descargado, unificando los resultados en la capa de persistencia relacional.

```
                  [ Descarga desde Google Drive API ]
                                  │
                    ────── Evaluador MIME ──────
                   │                            │
         [ Archivo Texto Nativo ]       [ Imagen / PDF Escaneado ]
                   │                            │
           ( Apache Tika )               ( Módulo OCR Local )
                   │                     ├─ OpenCV: Filtros & Limpieza
                   │                     └─ EasyOCR: Inferencia Neuronal
                   │                            │
                   └─────► [ Unificación ] ◄────┘
                                  │
                     ( Inserción en paginas_documento )
                                  │
                     ( Tokenización & Posicionamiento )
                                  │
                    ( Bulk Insert en indice_invertido_uni )
```

### Fase 3.1. Extracción e Fragmentación Híbrida

1. **Descarga del Recurso:** El sistema descarga el flujo de bytes (buffer) del archivo empleando la `Google Drive API v3`.
    
2. **Enrutamiento por Tipo MIME:**
    
    - **Ruta A (Documentos Digitales Nativos):** Si el archivo es un PDF con capa de texto, DOCX o TXT, `Apache Tika` parsea el buffer y extrae el texto respetando los saltos de página XML nativos.
        
    - **Ruta B (Imágenes y Documentos Escaneados):** Si el archivo es una imagen (`.png`, `.jpg`, `.jpeg`) o un PDF del cual Apache Tika no puede extraer texto (escaneado), se activa el submódulo óptico local:
        
        - **Conversión (para PDFs escaneados):** La librería `pdf2image` renderiza cada página del documento en un mapa de bits independiente en memoria.
            
        - **Preprocesamiento Óptico (OpenCV):** Cada imagen pasa por una rutina de normalización para mitigar el ruido visual. Se convierte a escala de grises y se aplica un desenfoque mediano para optimizar los bordes de los caracteres:
            
            Python
            
            ```
            import cv2
            import numpy as np
            
            def preprocesar_imagen(imagen_bytes):
                # Convertir el buffer de bytes a una matriz OpenCV
                nparr = np.frombuffer(imagen_bytes, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
                # Reducción de ruido mediante filtro de mediana
                img_limpia = cv2.medianBlur(img, 3)
                return img_limpia
            ```
            
        - **Inferencia de Redes Neuronales (EasyOCR):** La imagen preprocesada es evaluada por el lector de EasyOCR, configurado específicamente para el idioma español. Para optimizar el rendimiento por lotes, el objeto `Reader` se instancia de forma global una única vez al levantar el servicio:
            
            Python
            
            ```
            import easyocr
            
            # Instanciación única global (se recomienda activar GPU=True si hay hardware Nvidia disponible)
            reader = easyocr.Reader(['es'], gpu=False)
            
            def extraer_texto_ocr(imagen_procesada):
                # Extracción directa del texto crudo consolidado
                lineas_texto = reader.readtext(imagen_procesada, detail=0)
                return "\\n".join(lineas_texto)
            ```
            
3. **Registro de Almacén:** Sin importar la ruta de extracción (Tika u OCR), el texto consolidado de la página se inserta en `paginas_documento`, obteniendo inmediatamente el ID autogenerado:
    
    SQL
    
    ```
    INSERT INTO paginas_documento (id_drive, numero_pagina, texto_pagina) 
    VALUES (%s, %s, %s) RETURNING id_pagina;
    ```
    

### Fase 3.2. Tokenización y Mapeo Posicional

Para cada `id_pagina` insertado:

1. El texto se normaliza por completo en Python (conversión a minúsculas, remoción estricta de signos de puntuación y caracteres especiales, preservando caracteres alfanuméricos en español).
    
2. Se realiza una división por espacios para generar el vector ordenado de tokens (ej. `["arquitectura", "relacional", "pura"]`).
    
3. Se itera secuencialmente sobre el vector para construir las tuplas estructuradas que capturan la posición exacta: `("arquitectura", id_pagina, 0)`, `("relacional", id_pagina, 1)`, etc.
    

### Fase 3.3. Inserción Masiva por Lotes (Bulk Insert)

Dado que modelar el índice a nivel atómico posicional genera millones de registros, las inserciones individuales (`INSERT INTO ... VALUES ...`) degradarían críticamente el rendimiento de PostgreSQL. Es mandatorio agrupar las tuplas e inyectarlas mediante inserciones en bloques (Bulk Inserts) gestionando los conflictos de unicidad:

Python

```
import psycopg2
from psycopg2.extras import execute_values

def guardar_lote_indice(cursor, lote_tuplas_atomicas):
    query_insert = \"\"\"
        INSERT INTO indice_invertido_uni (palabra, id_pagina, posicion) 
        VALUES %s 
        ON CONFLICT (palabra, id_pagina, posicion) DO NOTHING
    \"\"\"
    execute_values(cursor, query_insert, lote_tuplas_atomicas)
```

## 4. Algoritmo de Extracción (Information Retrieval en Read-Time)

Al delegar la agregación al momento de la consulta, la base de datos se mantiene ligera y reactiva.

### Paso 1: Cálculo de Frecuencia (TF) Local al Vuelo

Cuando se ejecuta la búsqueda de un término atómico (ej. "sistemas"), el motor calcula en tiempo real la frecuencia de término utilizando agrupaciones estructuradas sobre las posiciones físicas:

SQL

```
SELECT 
    id_pagina, 
    COUNT(*) AS frecuencia, 
    ARRAY_AGG(posicion ORDER BY posicion) AS lista_posiciones 
FROM indice_invertido_uni 
WHERE palabra = 'sistemas' 
GROUP BY id_pagina;
```

_Justificación Arquitectónica:_ Esto elimina la necesidad de almacenar contadores estáticos o estructuras precalculadas, recalculando el peso contextual basándose estrictamente en las filas existentes en la base de datos.

### Paso 2: Intersección Multi-palabra (Semántica AND estricta)

Para consultas compuestas por múltiples términos (ej. "sistemas" y "software"), la base de datos ejecuta un filtrado por conjuntos sobre la misma tabla atómica empleando la cláusula `HAVING COUNT(DISTINCT palabra)` para garantizar que la página resultante contenga la totalidad de los criterios de búsqueda:

SQL

```
SELECT id_pagina
FROM indice_invertido_uni
WHERE palabra IN ('sistemas', 'software')
GROUP BY id_pagina
HAVING COUNT(DISTINCT palabra) = 2;
```

## 5. Algoritmo de Ranking y Generación de Resultados

Una vez identificadas las páginas candidatas mediante la intersección relacional, el backend ejecuta la ordenación y el formateo de cara al usuario.

### Paso 1: Puntuación Matemática Okapi BM25

El sistema calcula la relevancia estadística de cada documento candidates en la capa de Python utilizando la fórmula probabilística Okapi BM25:

$$Score(D, Q) = \sum_{i=1}^{n} \text{IDF}(q_i) \cdot \frac{f(q_i, D) \cdot (k_1 + 1)}{f(q_i, D) + k_1 \cdot \left(1 - b + b \cdot \frac{|D|}{\text{avgdl}}\right)}$$

Donde:

- $f(q_i, D)$ representa la frecuencia del término calculada en el paso anterior.
    
- $|D|$ es la longitud de la página actual (número total de palabras) extraída dinámicamente o calculada mediante un conteo sobre el Forward Index.
    
- $\text{avgdl}$ es la longitud promedio de todas las páginas registradas en el sistema.
    
- $k_1$ (típicamente $1.2$) y $b$ (típicamente $0.75$) son constantes de ajuste de saturación de parámetros.
    

**Optimización por Proximidad y Frase Exacta:** El motor analiza la `lista_posiciones` recuperada de la tabla `indice_invertido_uni`. Si las posiciones relativas de los términos consultados difieren en exactamente una unidad (ej. posición $10$ para "sistemas" y posición $11$ para "software"), Python inyecta una bonificación multiplicativa directa al score final de BM25, priorizando frases exactas sobre apariciones dispersas.

### Paso 2: Extracción Dinámica de Snippets (Forward Lookup)

Con las 10 páginas con mayor puntuación ordenadas de forma descendente, el sistema realiza una consulta a la tabla estructurada de almacenamiento de texto (`paginas_documento`):

1. **Punto de Anclaje:** Utiliza el primer elemento entero de la `lista_posiciones` de la palabra clave para fijar la coordenada del texto.
    
2. **Recorte de Ventana Excéntrica (KWIC - Key Word In Context):** Reconstruye una subcadena de texto tomando el entorno contextual correspondiente a un rango seguro de palabras, por ejemplo, `[-10 palabras : +10 palabras]` alrededor del punto de anclaje.
    
3. **Resaltado de Sintaxis:** Envuelve dinámicamente las palabras clave del término de búsqueda utilizando etiquetas semánticas HTML o Markdown (ej. `sistemas`) para una visualización clara en la interfaz de usuario.
    

### Paso 3: Ensamblaje del Enlace de Acceso Profundo (Deep Link)

Se ejecuta un `JOIN` relacional optimizado por la llave primaria entre `paginas_documento` y `documentos_indexados` a través del campo `id_drive` para heredar el metadato del enlace.

El JSON estructurado final entregado a la interfaz contiene:

- **Título:** Atributo `nombre_compuesto` limpio extraído de los metadatos.
    
- **Snippet:** El fragmento contextual KWIC generado en la fase anterior con los términos destacados.
    
- **URL de Destino:** Enlace web parametrizado unificando el visor de Drive con la página física exacta: `url_acceso + "#page=" + numero_pagina`. En caso de que la fuente original haya sido una imagen directa indexada mediante EasyOCR, el parámetro apunta por defecto a la página `1`, garantizando una experiencia de usuario robusta, uniforme e independiente del formato de origen.