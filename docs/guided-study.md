# Modo de estudio guiado

`/study` acompaña el recorrido por **Herder · Gramática alemana para
hispanohablantes**. El índice editorial canónico del manual `core_theory`
determina el orden Tema 1–Tema 51; el
`core_workbook` aporta práctica manual relacionada. La biblioteca complementaria
y Teacher Query explican o contrastan, pero no sustituyen la teoría principal.

## Experiencia

El inicio muestra la sesión activa o pausada, la recomendación determinista, las
dudas abiertas y el acceso a la ruta completa. Sin recorrido previo ofrece
empezar por la primera sección, elegir otra o crear un estudio libre. La
recomendación sigue este orden: sesión activa, sesión pausada, sección en curso,
sección para repaso, sección pausada y primera sección no iniciada.

La ruta completa muestra exactamente 51 temas principales en orden, título español,
título alemán cuando existe, rango impreso derivado y outline desplegable. No muestra
IDs técnicos ni títulos OCR legacy. La búsqueda incluye número de tema, títulos,
subapartados y páginas impresas. Los subapartados son guía interna y no crean estados
de progreso obligatorios.

Cada sesión guarda fuente, versión, sección, páginas PDF e impresa, objetivo,
plan, misión, duración orientativa, tiempo activo, posición final y resultado
subjetivo. Sus estados son `planned`, `active`, `paused`, `completed` y
`abandoned`. El reloj acumula solo intervalos activos; 20, 30, 45 y 60 minutos
son sugerencias y nunca cierran una sesión automáticamente.

Las coordenadas documentales nunca se mezclan:

- `reference_pdf_page`: página del PDF verificado del índice;
- `printed_page`: página impresa del libro;
- `manual_pdf_page`: página del PDF digital estudiado.

Un valor manual desconocido se muestra como pendiente. El ancla confirmada de
`Akkusativ` conserva Tema 22, libro p. 162 y manual PDF p. 89 con
`double_page` y región `unknown`; no se infiere una mitad.

Los estados prácticos de sección no son dominio:

- `not_started`, `in_progress`, `viewed`, `needs_review`,
  `completed_by_user`, `paused`;
- `completed_by_user` significa únicamente «el usuario decidió terminarla»;
- ninguna acción escribe `StudentSkill` o `SkillEvidence`.

El estudio libre se guarda en el historial, pero no mueve la sección activa de
la ruta Herder.

## Misiones

La presentación puede ser directa, laboratorio, ciudad helada, expedición
submarina, operación espacial o una selección mixta determinista. Son marcos
originales breves y opcionales. No cambian objetivo, páginas ni gramática y no
usan marcas, personajes o textos de videojuegos. Cada misión termina con una
producción alemana verificable vinculada al objetivo. El plan local tiene de cuatro
a seis pasos según la duración; abrir `/study` no llama a Qwen.

## Profesor contextual y privacidad

Las acciones rápidas reutilizan Teacher Query con un contexto estructurado
pequeño: sesión, sección, concepto, fuente, páginas, misión e idioma `es-ES`.
No se envía el historial completo. Las notas personales no forman parte del
prompt salvo que el usuario marque explícitamente cuáles quiere usar en esa
consulta. Un fallo del modelo devuelve un error local y deja intacta la sesión.

## Workbook

Una relación manual conserva fuente y versión del workbook, página PDF, página
impresa, ejercicio o rango, región, comentario y estado `candidate`,
`user_confirmed`, `rejected` o `stale`. «No lo sé» registra la decisión sin
cambiar el estado; confirmar y rechazar son reversibles. Las soluciones no se
abren ni se leen automáticamente y la ausencia de vínculo nunca bloquea el
estudio.

## Persistencia y API

Los datos personales residen en la base principal, migración `0006`. La ruta y
los archivos siguen en la SQLite reconstruible de biblioteca. La unión es por
IDs estables y snapshots, no por claves foráneas entre bases. Consulta el
[ADR 0011](adr/0011-guided-study-persistence.md).

La reconciliación consulta la tabla versionada de mappings de biblioteca. Un mapping
`exact` puede presentar el título canónico y asociar de forma reversible estado
práctico, última sesión, dudas y workbook. `probable`, `ambiguous`, `unmatched` y
`rejected` nunca reasignan silenciosamente datos: la sesión histórica conserva su
snapshot y continúa accesible. No hay migración adicional de la base principal.

Rutas principales:

- `GET /api/study/dashboard`, `/path`, `/sections/{id}`, `/history`;
- `POST /api/study/sessions` y
  `POST /api/study/sessions/{id}/transition`;
- `PUT /api/study/sessions/{id}/position`;
- CRUD de `/notes` y `/questions`;
- `POST /api/study/workbook-links` y revisión auditable;
- `GET|PUT /api/study/preferences`;
- `POST /api/study/sessions/{id}/teacher`;
- eliminaciones confirmadas de recursos o de todos los datos de estudio.

Todas las mutaciones requieren `operation_id`. Repetir el mismo contenido es
idempotente; reutilizar el ID con otro contenido responde conflicto. Los errores
conocidos se traducen a 404, 409, 422 o 503 sin trazas ni datos privados.

## Límites deliberados

No hay puntuación, certificación, rachas, castigos, calendario obligatorio,
repetición espaciada, corrección de workbook, soluciones automáticas, audio ni
OCR masivo. Las 53 secciones sugeridas permanecen intactas como legacy y auditables,
pero la ruta visible procede exclusivamente del índice canónico validado.
