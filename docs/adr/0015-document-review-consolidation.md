# ADR 0015: Revisión documental y consolidación estructural

## Estado

Aceptada para #009ING5.

## Decisión

La revisión se modela como una capa formal sobre la evidencia de #009ING4. Los
candidatos observados no se actualizan para expresar decisiones editoriales:
`document_candidate_review_state` conserva la proyección vigente y
`document_candidate_decisions` el ledger append-only. Las reversiones son nuevas
decisiones inversas. Las ejecuciones, lotes y snapshots fijan versión, hash,
regla, configuración, actor y métricas antes/después.

La consolidación materializa `document_consolidated_topics` y
`document_consolidated_nodes` sin crear contenido pedagógico. Cada libro tiene
51 slots temáticos y como máximo un candidato principal por tema y ejecución;
los alternativos permanecen enlazados. Índices, cabeceras, pies y duplicados no
se eliminan: quedan rechazados o superseded con evidencia explícita.

Las relaciones ejercicio–solución se apoyan solo cuando coinciden tema, nivel e
identificador de ítem de forma unívoca. Las cardinalidades ambiguas permanecen
en revisión. Las páginas sin texto solo reciben una clasificación técnica
basada en artefactos existentes; no se ejecutan visión, OCR ni LLM.

## Readiness y límites

P0 o temas principales sin resolver bloquean readiness. P1/P2 y revisión visual
pendiente permiten `structurally_ready_with_issues`; cero incidencias no es un
requisito artificial. Readiness es informativo y nunca activa una versión.
Esta capa tampoco crea chunks, embeddings, conocimiento o progreso del alumno.

La migración de biblioteca v10 añade únicamente estas tablas e índices. Su
rollback elimina la capa derivada y conserva fuentes, versiones, páginas,
bloques, candidatos, comparaciones, chunks, embeddings y FTS.
