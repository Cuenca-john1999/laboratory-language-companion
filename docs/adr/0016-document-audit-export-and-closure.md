# ADR 0016: Exportación auditable y cierre documental

## Estado

Aceptado.

## Contexto

La consolidación estructural deja decisiones automáticas y humanas trazables,
pero una revisión externa necesita un intercambio verificable que no dependa de
confiar en un JSON modificado. Además, la preparación estructural y la aptitud
para una futura lectura con IA son estados distintos. Las versiones Herder
candidatas deben poder cerrarse documentalmente sin activarlas, sin OCR y sin
generar representaciones semánticas.

## Decisión

Se adopta `audit-package.v1`, un ZIP con JSON UTF-8 canónico, manifest de archivos,
hashes SHA-256, identidad de fuente/versión/PDF, procedencia, readiness y un hash
lógico. Hay tres alcances: `full`, `review_only` y `targeted`. Los artefactos
visuales son derivados pequeños, explícitos y opcionales; el PDF y SQLite nunca
forman parte del paquete. Los hashes acreditan integridad, no autenticidad.

Las respuestas externas usan `audit-decisions.v1`. Cada decisión declara target,
estado previo esperado, acción permitida, evidencia, autor y fecha. El importador
valida primero identidad, hashes, esquema, claves y vigencia; después ofrece un
dry-run que no escribe. Apply es explícito, atómico y registra eventos append-only
con método `external_audit`. No se aceptan instrucciones arbitrarias ni una
identificación basada solo en página, texto, ordinal o nombre de archivo.

La persistencia v11 registra metadata de exports, imports y snapshots; los ZIP
permanecen fuera de Git y de SQLite. El recálculo posterior se limita a las
proyecciones derivadas afectadas. Un snapshot de cierre fija revisiones, cobertura,
readiness y hash, y es inmutable: cualquier revisión posterior crea otro.

Se separa `structural_readiness` de `ai_readiness`. Los estados de IA son
`not_ready_for_ai`, `ready_for_ai_with_issues`, `ready_for_ai` y
`blocked_for_ai`. Estar listo para IA no activa la versión y no exige chunks ni
embeddings. La activación queda reservada para la futura familia HER.

## Consecuencias

- Una auditoría se puede reproducir y verificar frente al PDF fuente aportado por
  separado.
- Los imports stale, incompatibles, ambiguos o parcialmente fallidos no mutan el
  corpus en modo atómico.
- La historia automática anterior no se sobrescribe.
- El cierre documental no ejecuta OCR, Vision, modelos locales, chunking,
  embeddings ni activación.
- `audit-package.v1` y `audit-decisions.v1` requieren una nueva versión explícita
  para cambios incompatibles.
