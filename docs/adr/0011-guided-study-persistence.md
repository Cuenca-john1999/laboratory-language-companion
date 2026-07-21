# ADR 0011: Persistencia no evaluativa del estudio guiado

- Estado: aceptado
- Fecha: 2026-07-22

## Contexto

La ruta de estudio usa secciones y fuentes de la biblioteca educativa. Esa
biblioteca se puede reconstruir a partir de los materiales originales, pero una
sesión, una nota, una duda o una asociación manual con el workbook son datos
personales que no se pueden reconstruir. Al mismo tiempo, terminar una sección
por decisión propia no demuestra dominio curricular.

## Decisión

Los datos personales del modo estudio se guardan en la SQLite principal bajo la
revisión Alembic `0006`. Las filas conservan referencias externas estables y
snapshots mínimos de título, versión y página; no se crean claves foráneas entre
las dos bases SQLite. La biblioteca sigue siendo la autoridad para la ruta
editorial actual y permanece de solo lectura para el servicio de estudio.

El estado práctico (`not_started`, `in_progress`, `viewed`, `needs_review`,
`completed_by_user`, `paused`) es deliberadamente independiente de
`StudentSkill` y `SkillEvidence`. `completed_by_user` solo expresa una decisión
del usuario. El servicio no llama al Learning Engine ni proyecta resultados.

Las mutaciones usan un `operation_id` y `study_events` para idempotencia,
conflictos y reversión de revisiones del workbook. Notas y dudas son privadas;
Teacher Query solo recibe notas escogidas expresamente en esa petición.

## Consecuencias

- Reiniciar navegador, API o Mac no pierde la posición.
- Reconstruir el índice de biblioteca no elimina historial personal.
- Si una sección editorial cambia, la sesión histórica conserva su snapshot y
  la ruta nueva puede mostrar la versión vigente.
- El backup de la base principal incluye también los datos de estudio.
- La eliminación explícita puede purgar notas, dudas, sesiones, estados y
  enlaces sin tocar el progreso evaluado.

## Alternativas descartadas

- `localStorage`: no ofrece continuidad fiable ni backup junto a la aplicación.
- SQLite reconstruible de biblioteca: podría perder datos personales durante
  una reconstrucción.
- Reutilizar `LearningSession`: mezclaría acompañamiento autorreportado con
  evidencia evaluada y dificultaría garantizar la no proyección.
