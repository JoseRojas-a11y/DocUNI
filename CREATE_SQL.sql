CREATE TABLE documentos_indexados (
    id SERIAL PRIMARY KEY,
    id_drive VARCHAR(100) UNIQUE NOT NULL,
    nombre_original VARCHAR(255) NOT NULL,
    nombre_compuesto VARCHAR(500) NOT NULL,
    categoria_principal VARCHAR(150) NOT NULL,
    url_acceso TEXT NOT NULL,
    fecha_indexacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    procesado BOOLEAN NOT NULL DEFAULT FALSE,
    longitud INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE indice_docu_uni (
    id SERIAL PRIMARY KEY,
    palabra VARCHAR(150) UNIQUE NOT NULL,
    documentos JSONB NOT NULL
);

-- Este índice especial en Postgres permite buscar rápidamente dentro del JSONB
CREATE INDEX idx_indice_palabra ON indice_docu_uni(palabra);
CREATE INDEX idx_jsonb_documentos ON indice_docu_uni USING GIN (documentos);