# Learning Engine v2

El primer núcleo funcional de Milestone 1 es local, determinista y totalmente
independiente de Ollama. El código decide qué estudiar, qué prerrequisitos se
cumplen, cómo cambia el dominio y cuándo toca repasar. Un modelo de lenguaje
podrá generar el enunciado de una actividad ya seleccionada, pero no puede
alterar esas decisiones ni escribir directamente en `StudentSkill`.

## Currículo `a0-a1.v1`

La migración `0004` conserva las ocho habilidades genéricas anteriores como
datos legacy y añade un currículo activo A0–A1 de catorce habilidades. Cada
membresía guarda orden, dificultad 1–5, referencia de contenido A0/A1,
ejercicios compatibles y cuatro umbrales internos. Los prerrequisitos son
relaciones explícitas entre habilidades del mismo currículo.

La API presenta A0 como `pre-A1`, una referencia del contenido y no una
certificación del estudiante. No hay contenido A2/B1 ni una etiqueta CEFR
global.

Una habilidad cumple su criterio interno únicamente cuando alcanza todos sus
umbrales de dominio, confianza, evidencias efectivas y racha correcta sin
ayuda. Solo entonces desbloquea habilidades dependientes. La versión inicial
usa por defecto 0.80, 0.60, seis evidencias y racha dos.

## Evaluaciones y dominio

`POST /api/learning/attempts` no acepta una puntuación libre. Recibe un resultado
categórico y `learning-engine-v2` deriva el valor:

| Resultado              | Valor interno | Siguiente repaso              |
| ---------------------- | ------------: | ----------------------------- |
| `failure`              |           0.0 | 1 día                         |
| `partial`              |           0.4 | 2 días                        |
| `correct_with_help`    |           0.7 | 3 días                        |
| `correct_without_help` |           1.0 | 3/7/14/30/60 días según racha |

La primera evidencia establece el dominio en su valor. Después:

```text
mastery'   = round(0.65 × mastery + 0.35 × outcome_score, 4)
confidence = min(0.95, effective_evidence_count / 10)
```

El resultado sin ayuda incrementa la racha; cualquier otro la reinicia. Un
fallo incrementa también los lapsos. Todas las salidas están acotadas a 0–1 y
SQLite vuelve a imponer esos límites.

La transacción crea `LearningSession`, `ExerciseAttempt`, `SkillEvidence` y la
proyección `StudentSkill` de forma atómica. El UUID `submission_id` es la clave
de idempotencia: repetir exactamente la solicitud devuelve la evidencia
existente; reutilizarlo con otro contenido devuelve HTTP 409. El tipo de
ejercicio debe ser compatible con la habilidad activa.

Las operaciones que leen y después actualizan agregados mutables se serializan
en SQLite con `BEGIN IMMEDIATE`. Así, dos intentos simultáneos distintos no
pueden sobrescribir silenciosamente el progreso calculado por el otro. Si otra
transacción mantiene el bloqueo más allá del tiempo de espera, la API responde
HTTP 503 con `Retry-After: 1`; el cliente puede repetir la misma solicitud sin
duplicarla gracias a `submission_id`.

## Ledger y correcciones

`SkillEvidence` es append-only. Guarda el resultado, valor derivado, fuente,
versión y estados antes/después (dominio, confianza, contador, racha, lapsos y
fecha de repaso). Los triggers creados en 0003 continúan impidiendo `UPDATE` y
`DELETE`.

Para corregir una evaluación se registra otra con UUID nuevo y
`corrects_submission_id`. La nueva fila referencia y sustituye lógicamente la
anterior. Una restricción impide dos sustituciones directas de la misma fila;
para rectificar otra vez se sustituye la corrección más reciente. El motor
reproduce cronológicamente las evidencias no sustituidas y reconstruye
`StudentSkill`, por lo que corregir no aumenta artificialmente el contador. El
registro original nunca desaparece.

## Daily Planner

`POST /api/learning/daily-plan` recibe 10–120 minutos y motivación 1–5. Consulta
perfil, preferencias, estados, historial de ejercicios, repasos pendientes y
currículo; no consulta al proveedor de modelos.

Las reglas versionadas son:

1. colocar primero los repasos vencidos, ordenados por vencimiento y debilidad;
2. continuar una habilidad iniciada antes de acumular contenido nuevo;
3. introducir como máximo una habilidad nueva y solo con prerrequisitos
   dominados;
4. con motivación 1–2 usar intensidad baja y evitar contenido nuevo, salvo el
   arranque inicial cuando aún no existe ninguna habilidad activa;
5. alternar tipos respecto al historial y al bloque anterior, favoreciendo
   escucha y diálogo cuando sean compatibles;
6. crear entre dos y seis bloques de al menos cinco minutos cuya suma sea
   exactamente el tiempo solicitado, incluido el caso de diez minutos.

El plan guarda fecha local, objetivo, intensidad, habilidad principal, repasos,
posible habilidad nueva, bloques, motivos y versiones. Cada generación se
persiste; no sobrescribe planes anteriores. `GET /api/learning/today` recupera
el último plan de la fecha local configurada y devuelve 404 si aún no existe.

## Endpoints

- `GET /api/learning/curriculum`: currículo activo versionado.
- `GET /api/learning/today`: último plan persistido del día.
- `POST /api/learning/daily-plan`: genera y persiste un plan.
- `GET /api/learning/reviews`: habilidades cuyo repaso ya venció.
- `GET /api/learning/skills`: currículo con estado, elegibilidad y
  prerrequisitos pendientes.
- `POST /api/learning/attempts`: registra o corrige evidencia idempotente.

## Fuera de alcance

Este núcleo no implementa voz, extracción automática de errores, calificación
por LLM, clases completas, diagnóstico, simulador Goethe, agentes, aprendizaje
automático ni adaptación avanzada.
