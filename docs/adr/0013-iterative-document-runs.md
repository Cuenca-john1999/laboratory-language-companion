# ADR 0013: Ejecuciones documentales iterativas y cobertura verificable

- Estado: aceptada
- Fecha: 2026-07-30

## Contexto

`processing_jobs` representa una unidad técnica reanudable: tipo, estado, cursor,
intentos y progreso. No fija una `source_version`, su hash, la versión del
pipeline, la configuración completa, el linaje entre pasadas ni snapshots de
cobertura. Convertir cada job legacy en una lectura documental completa
inventaría una intención que nunca existió.

Las ejecuciones documentales necesitan conservar evidencia por versión y por
pasada, permitir etapas e intentos, y seguir siendo auditables cuando aparezcan
versiones posteriores con distinta paginación.

## Decisión

Se introduce `document_processing_runs` como intención de alto nivel:
“procesar esta versión y este hash con este pipeline y esta configuración”.
`processing_jobs` sigue siendo la unidad técnica y admite vínculos nullable con
la ejecución y etapa que lo originan. Los 96 jobs anteriores quedan como
`legacy=1`, sin run sintético.

Una ejecución:

- fija `source_id`, `source_version_id`, `target_hash`, pipeline y hash estable
  de configuración;
- mantiene `parent_run_id` y `base_run_id`;
- usa una máquina de estados explícita y transaccional;
- solo termina cuando sus etapas seguras seleccionadas han terminado;
- conserva etapas, intentos, eventos, incidencias y snapshots anteriores;
- bloquea ejecuciones exclusivas incompatibles sobre la misma versión.

Las páginas viven en `document_pages` con identidad
`(source_version_id, pdf_page_index)`. Los resultados por etapa son append-only
por ejecución e intento en `document_page_stage_results`. No existe
correspondencia automática entre páginas de versiones distintas.

La cobertura se almacena como numeradores, denominador, estado de ejecución y
procedencia. `not_executed` y `no_data` son distintos de cero. Los porcentajes
solo se calculan para presentar una dimensión con denominador conocido. No
existe una métrica global de “comprensión”.

## Etapas de este bloque

El catálogo declara preflight, extracción de texto, OCR, layout, estructura,
extracción de candidatos pedagógicos, revisión, chunking, embeddings, mapeo
canónico, validación, reconciliación de cobertura y preparación para activación.

Solo `preflight.v1` y `coverage-reconciliation.v1` son ejecutables:

- preflight comprueba archivo, hash y metadatos sin OCR, modelos, chunks ni
  embeddings;
- reconciliación deriva cobertura desde artefactos ya almacenados.

El resto aparece como `not_scheduled`; no se simula éxito.

## Recuperación y terminalidad

Al arrancar, una etapa `running` interrumpida pasa a `failed` con el código
seguro `process_restarted`, y su run queda `paused` y reanudable. Nunca se
convierte silenciosamente en éxito.

Cancelar conserva etapas, páginas y snapshots confirmados. Reintentar crea un
nuevo registro de intento. Crear una pasada desde pendientes genera un nuevo
run hijo sobre la misma versión exacta, sin sobrescribir al padre.

## Consecuencias

El modelo añade tablas propias, pero evita duplicar la responsabilidad del
worker técnico. Las futuras implementaciones de OCR o IA deberán crear jobs
hijos y resultados por página; no podrán promover versiones ni modificar el
corpus activo implícitamente.
