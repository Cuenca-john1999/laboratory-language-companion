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

Todos los instantes representan UTC. SQLite los conserva sin offset y el
adaptador ORM los devuelve conscientes de zona; la API emite ISO 8601 con
`Z`/`+00:00`. Solo `DailyPlan.plan_date` es una fecha civil calculada con
`DEUTSCHOS_TIMEZONE` (por defecto `Europe/Berlin`).
