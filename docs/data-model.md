# Modelo de datos

`StudentProfile` contiene contexto editable del único estudiante. `Skill` define capacidades y `StudentSkill` guarda estimación, confianza y agenda sin confundirlas con certificación CEFR. `LearningSession` agrupa actividad y `ExerciseAttempt` conserva evidencias. `Mistake` mantiene recurrencia, estado y revisión. `VocabularyItem` representa el término; `StudentVocabulary` separa reconocimiento, producción escrita y hablada.

SQLite es configurable con `DEUTSCHOS_DATABASE_URL`. Todo cambio de esquema debe entrar mediante una revisión Alembic; nunca se debe recrear ni sobrescribir silenciosamente la base del usuario.

`Curriculum` identifica una versión A0–A1. `CurriculumSkill` relaciona las
habilidades estables con orden, referencia de contenido, dificultad, ejercicios
y umbrales de dominio. `SkillPrerequisite` modela el grafo dentro de la misma
versión; no se mezclan prerrequisitos de currículos distintos. Las ocho
habilidades genéricas de 0003 permanecen fuera del currículo activo para no
eliminar datos legacy.

`SkillEvidence` es el ledger inmutable del Learning Engine. Enlaza
`Skill → SkillEvidence → ExerciseAttempt → LearningSession`, usa un UUID único
para idempotencia y conserva instantáneas antes/después. Una corrección es otra
evidencia que referencia a la sustituida; nunca un `UPDATE`. `StudentSkill` no
es evidencia original: es la proyección versionada de las evidencias efectivas
y puede reconstruirse. Valores, dominios y confianzas están restringidos entre
0 y 1 en código y SQLite.

`DailyPlan` conserva cada decisión diaria para el perfil único, la fecha local,
entradas, objetivo, selección, motivos y versiones. `DailyPlanBlock` guarda la
secuencia y duración; sus minutos suman el total del plan por regla de servicio.

La revisión `0005` añade persistencia diagnóstica separada del Learning Engine:

- `DiagnosticSession` conserva versión, currículo, límites, estado reanudable,
  tiempos y la relación con una sesión repetida;
- `DiagnosticTask` conserva exactamente la tarea textual seleccionada o
  presentada, su plantilla, ejes, dificultad, contenido y rúbrica;
- `DiagnosticResponse` representa una evaluación versionada de una entrega. Un
  mismo `submission_id` puede tener revisiones sucesivas enlazadas mediante
  `supersedes_response_id` sin modificar la respuesta original;
- `DiagnosticResult` conserva una conclusión por eje, modalidad y `skill_id`
  opcional. Cada combinación `(axis, skill_id)` forma su propia cadena; sus
  correcciones son append-only mediante `supersedes_result_id`.

`DiagnosticResponse` y `DiagnosticResult` tienen triggers SQLite que rechazan
`UPDATE` y `DELETE`. Cada elemento solo puede tener una corrección directa y la
base valida que una corrección conserve la entrega o dimensión original. Las
puntuaciones y confianzas se restringen a 0–1; `not_evaluable` exige score nulo.
Una tarea deja de poder volver a `selected`, cambiar su contenido o borrarse
después de ser presentada; sus transiciones posteriores de estado siguen
disponibles para el motor determinista.
La modalidad de tareas es únicamente texto. Escucha, habla, pronunciación y
fluidez oral solo pueden aparecer como resultados sin evidencia suficiente.
Las bandas `pre-A1`/`A1` requieren una habilidad concreta, al menos dos
evidencias positivas y confianza mínima de 0,6. El motor consulta la referencia
de la membresía `CurriculumSkill`: `A0` solo puede producir `pre-A1`, y `A1`
exige además sus umbrales conservadores. La dificultad interna no sustituye esa
referencia curricular.

Las claves foráneas hacia perfil, currículo y habilidades usan `RESTRICT`.
Borrar una sesión vacía o con tareas aún no presentadas elimina esas tareas en
cascada. Una tarea presentada, una respuesta o un resultado bloquean el borrado
destructivo y la sesión se conserva como completada, cancelada o abandonada. Un
futuro flujo explícito de privacidad deberá definir la purga física auditada; no
se realiza silenciosamente desde esta capa.

La revisión no contiene relaciones con `StudentSkill` ni `SkillEvidence` y no
puede declarar dominio o programar repasos.

El motor determinista de texto usa este esquema sin una migración adicional.
`DiagnosticSession.selection_state` contiene únicamente metadatos operativos
versionados: recibos de idempotencia, códigos de transición y referencias a
tareas; nunca respuestas, prompts completos ni secretos. `random_seed` actúa
como clave de creación dentro del servicio serializado. Las selecciones se
materializan y presentan en una sola transacción, y la entrega con su primera
evaluación determinista también es atómica porque `DiagnosticResponse` reúne
ambos conceptos en la revisión `0005`.

El historial inmutable sigue residiendo en `DiagnosticResponse` y
`DiagnosticResult`. El registro de transiciones de sesión guardado en JSON es
trazabilidad operativa reanudable, no un ledger protegido por trigger. Si se
necesita una auditoría DB append-only de cada transición o separar entrega y
evaluación asíncrona, hará falta una migración futura explícita.

Todos los instantes representan UTC. SQLite los conserva sin offset y el
adaptador ORM los devuelve conscientes de zona; la API emite ISO 8601 con
`Z`/`+00:00`. Solo `DailyPlan.plan_date` es una fecha civil calculada con
`DEUTSCHOS_TIMEZONE` (por defecto `Europe/Berlin`).
