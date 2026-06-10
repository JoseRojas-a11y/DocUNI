-- Cargar datos masivos en la tabla documentos_indexados
COPY documentos_indexados(id, id_drive, nombre_original, nombre_compuesto, categoria_principal, url_acceso, fecha_indexacion, procesado, longitud)
FROM '/docker-entrypoint-initdb.d/documentos_data'
WITH (FORMAT csv, HEADER false, DELIMITER ',');

-- Crear tabla temporal para corregir el formato JSONB de documentos (reemplazar comillas simples por dobles)
CREATE TEMP TABLE temp_indice_docu_uni (
    id INT,
    palabra VARCHAR(150),
    documentos TEXT
);

-- Cargar datos masivos en la tabla temporal como TEXT
COPY temp_indice_docu_uni(id, palabra, documentos)
FROM '/docker-entrypoint-initdb.d/indice_docu_data'
WITH (FORMAT csv, HEADER false, DELIMITER ',');

-- Insertar los datos en la tabla real convirtiendo las comillas y casteando a JSONB
INSERT INTO indice_docu_uni (id, palabra, documentos)
SELECT id, palabra, REPLACE(documentos, '''', '"')::jsonb
FROM temp_indice_docu_uni;

-- Eliminar tabla temporal
DROP TABLE temp_indice_docu_uni;

-- Sincronizar las secuencias seriales para evitar colisiones en futuros registros
SELECT setval('documentos_indexados_id_seq', COALESCE((SELECT MAX(id) FROM documentos_indexados), 1));
SELECT setval('indice_docu_uni_id_seq', COALESCE((SELECT MAX(id) FROM indice_docu_uni), 1));
