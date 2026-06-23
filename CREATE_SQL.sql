-- ============================================================
-- DocUNI v3.0 - Esquema Relacional Puro (1NF)
-- Índice Invertido Atómico Posicional
-- ============================================================

-- Tabla 1: Metadatos del documento (identidad global)
CREATE TABLE documentos_indexados (
    id_drive          VARCHAR(35) PRIMARY KEY,
    nombre_compuesto  VARCHAR(200) NOT NULL,
    url_acceso        VARCHAR(200) NOT NULL,
    procesado         BOOLEAN NOT NULL DEFAULT FALSE,
    primera_carpeta   VARCHAR(70)
);

-- Tabla 2: Forward Index / Almacén de Texto por Página
-- Almacena el texto fragmentado para aislar el contexto y permitir snippets KWIC
CREATE TABLE lineas_documento (
    id_linea      SERIAL PRIMARY KEY,
    id_drive       VARCHAR(35) NOT NULL REFERENCES documentos_indexados(id_drive) ON DELETE CASCADE,
    numero_fila    INTEGER NOT NULL,
    texto_fila     TEXT NOT NULL,
    UNIQUE (id_drive, numero_fila)
);

-- Tabla 3: Índice Invertido Atómico Posicional
-- Cada fila = una aparición individual de un término en una posición exacta de una página
CREATE TABLE indice_invertido_uni (
    palabra    VARCHAR(150) NOT NULL,
    id_linea  INTEGER NOT NULL REFERENCES lineas_documento(id_linea) ON DELETE CASCADE,
    posicion   INTEGER NOT NULL,
    PRIMARY KEY (palabra, id_pagina, posicion)
);

-- Índice B-Tree crítico para búsquedas rápidas por término
CREATE INDEX idx_palabra_busqueda ON indice_invertido_uni (palabra);

-- Índice para búsquedas de páginas por documento padre
CREATE INDEX idx_paginas_drive ON lineas_documento (id_drive);