# ADR 0014: Comparación de versiones por página

## Estado

Aceptada para #009ING3.

## Decisión

`document_pages` continúa siendo la identidad única de una página PDF dentro de
una `source_version`. La comparación no crea otra tabla de páginas. Añade:

- `document_page_artifacts`, una extensión 1:1 regenerable con fingerprints de
  texto, render y geometría;
- `document_version_comparisons`, que fija source, dos version IDs, ambos hashes,
  algoritmo y configuración;
- `document_page_correspondences` y su tabla ordenada de páginas para representar
  1:1, 1:N, N:1, N:M y ausencia de equivalente;
- eventos append-only y planes de transferencia no ejecutables.

No se reutilizan `page_quality` ni `page_extraction_variants` como identidad:
ambas describen resultados de extracción y calidad, no la página estable.
Tampoco se convierte una comparación en OCR ni en un
`document_processing_run`; son dominios y transiciones diferentes.

## Algoritmo y límites

`page-match.v1` usa primero hashes exactos, después una ventana ordinal
configurable y señales separadas de texto, imagen, geometría, número impreso y
orden. La búsqueda normal es O(páginas × ventana), con saltos fuera de ventana
solo para hashes exactos. Las divisiones y combinaciones usan hashes de regiones
izquierda/derecha. La relación de aspecto por sí sola nunca confirma un pliego.

La normalización textual conserva contenido Unicode NFC, `ß`, diéresis, saltos
de línea y columnas. Solo elimina caracteres invisibles y espacios técnicos.
No corrige, traduce ni ejecuta OCR. Una capa ausente se informa como “Sin texto
disponible” y no implica que la página esté vacía.

`auto_supported` queda reservado a texto y render idénticos, geometría igual,
misma fuente y ausencia de conflicto. Las demás propuestas requieren revisión.
Las decisiones son append-only; los ajustes crean otra relación y dejan la
anterior `superseded`.

## Transferencia

El plan calculado siempre contiene `executable: false`. Chunks y embeddings se
regeneran si cambia texto o paginación; coordenadas se invalidan si cambia la
geometría; temas y conceptos solo se proponen. Ningún endpoint activa versiones,
copia texto o ejecuta el plan.
