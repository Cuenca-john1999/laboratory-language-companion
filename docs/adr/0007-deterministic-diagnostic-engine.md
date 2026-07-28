# ADR 0007: Motor diagnóstico determinista y transaccional

- Estado: aceptado
- Fecha: 2026-07-13

## Contexto

La revisión `0005` añadió `DiagnosticSession`, `DiagnosticTask`,
`DiagnosticResponse` y `DiagnosticResult`, con respuestas y resultados
append-only. Faltaba convertir el diseño de
[`ADR 0006`](0006-adaptive-diagnostic.md) en un motor ejecutable que pudiera
pausar, reanudar, seleccionar, evaluar y completar una sesión sin depender de un
LLM.

DeutschOS usa SQLite local para un único alumno. Dos peticiones concurrentes no
deben presentar dos tareas, perder contadores ni insertar dos evaluaciones del
mismo intento. Una repetición después de perder la respuesta del proceso debe
devolver la misma entidad lógica —en su proyección actual— y no repetir el
efecto.

La persistencia `0005` no separa una entrega de su evaluación: una fila
`DiagnosticResponse` requiere resultado, score nullable, evaluador y versión.
Tampoco existe una tabla append-only de transiciones de sesión. Añadir esas
entidades habría ampliado `#006C` y exigido otra migración.

## Decisión

Se implementa `diagnostic-engine.v1` como un paquete interno con dos capas:

1. funciones puras para máquinas de estados, selección, scoring y agregación;
2. un servicio transaccional que reconstruye estado y persiste las decisiones
   en las entidades de `0005`.

Todos los contratos de entrada y salida son esquemas Pydantic estrictos. El
motor usa un `CandidateProvider` inyectable y no contiene un banco de preguntas.
Los candidatos de producción deberán ser versionados; los actuales son fixtures
finitos destinados a pruebas.

### Estados explícitos

La vista simplificada `created/active/paused/completed/abandoned/failed` se
mapea a los estados persistidos del diseño: `not_started`, `onboarding`,
`calibrating`, `assessing`, `reviewing`, `paused`, `time_limited`,
`completing`, `completed`, `cancelled`, `abandoned` y `error`.

Cada transición permitida es una arista explícita. Las repeticiones razonables
son idempotentes y las demás combinaciones se rechazan. Pausa y reanudación usan
`active_seconds`, `resumed_at` y `paused_from_status`, por lo que sobreviven a un
reinicio y no cuentan el tiempo pausado.

Un marcador efímero de instancia evita imputar el periodo con la API apagada a
una sesión que quedó activa. No detecta inactividad con la misma instancia viva;
la interfaz posterior deberá pausar o emitir actividad explícita.

### Selección reproducible

El selector prioriza la primera cobertura de seis ejes textuales esenciales y
después las contradicciones pendientes, antes de profundizar. Comienza en
dificultad 1, permite 2 como único fallback
inicial, sube tras dos aciertos independientes sin ayuda, mantiene con respuesta
parcial o ayudada, y baja tras un error claro. `not_evaluable` no cambia la
dificultad.

La selección excluye identidades y equivalencias ya presentadas, modalidades
futuras, candidatos no autoevaluables y prerrequisitos no satisfechos. Limita
cada eje a cinco tareas, cada contradicción a un desempate, evita tres tipos
iguales consecutivos y resuelve cualquier empate mediante un orden total
estable, sin azar.

### Scoring cerrado

La evaluación determinista admite coincidencia exacta, respuestas aceptadas y
tokens ordenados. Usa resultados `incorrect`, `partial`, `correct_with_help`,
`correct_without_help` y `not_evaluable`, con scores 0, 0,4, 0,7, 1 y nulo.
`incorrect` se persiste como el valor compatible `failure`.

Una respuesta vacía, en español, con instrucción no comprendida o sin rúbrica
determinista suficiente no se convierte en error: queda `not_evaluable`. La
ayuda impide clasificar una coincidencia como correcta sin ayuda.

### Agregación conservadora

Los resultados se calculan por eje, nunca globalmente. Score de tarea, confianza
del evaluador y confianza agregada permanecen separados. La agregación exige
confianza del evaluador mínima 0,6, reduce el peso de respuestas ayudadas,
premia moderadamente dificultad y variedad, y penaliza contradicciones y muestras
insuficientes.

Una sola respuesta no produce score estimado ni banda observada. Para persistir,
cada eje se separa por `skill_id` opcional y conserva una cadena append-only por
esa dimensión. `A1` requiere una habilidad cuya membresía curricular versionada
declare `A1`, tres evidencias positivas, dos aciertos sin ayuda, confianza 0,7 y
score 0,65. Una habilidad curricular `A0` solo puede producir `pre-A1`; la
dificultad interna nunca se convierte por sí sola en CEFR. No existe CEFR global
ni certificación. Audio y voz quedan `not_assessed`.

### Parada acotada

El objetivo es configurable entre 12 y 16 tareas, con 14 por defecto. Existen
límites absolutos de 20 tareas y 1.500 segundos activos. La sesión puede terminar
antes cuando cada eje prioritario posee al menos dos evidencias, cobertura
mínima, confianza suficiente y ninguna contradicción pendiente. También termina
de forma parcial al agotar candidatos.

### Transacciones e idempotencia

Toda mutación SQLite comienza con `BEGIN IMMEDIATE` antes de leer estado mutable
y termina en un único commit. Un error provoca rollback completo y un bloqueo
prolongado produce un error reintentable.

La creación usa un `request_id`; las operaciones de sesión y selección usan
`operation_id`; las entregas usan `submission_id` y `evaluation_id`; los
resultados usan UUIDv5 deterministas. Reutilizar una identidad con otro payload
es un conflicto. Las correcciones se anexan como nuevas revisiones y no
modifican filas históricas.

Un replay compatible devuelve la misma entidad lógica en su proyección actual y
no repite el efecto. No pretende congelar el recibo exacto de una lectura pasada.

Selección e inserción/presentación son atómicas. Entrega y evaluación
determinista también son una sola operación atómica, porque el esquema actual no
representa una entrega pendiente.

### Persistencia sin migración

No se añade una migración posterior a `0005`. El estado necesario para reanudar
ya está en las cuatro entidades diagnósticas. Los recibos idempotentes y un
registro práctico de transiciones se conservan en `selection_state`.

Ese registro JSON no se considera un ledger inmutable: puede reescribirse y no
tiene triggers equivalentes a los de respuestas y resultados. Si la auditoría
futura exige inmutabilidad frente a escritores directos, deberá añadirse una
tabla de eventos en otra decisión y migración.

El servicio no importa el Learning Engine, LM Studio o capas HTTP y no escribe
`StudentSkill` ni `SkillEvidence`.

## Alternativas consideradas

### Selección aleatoria con semilla

Se rechazó porque un orden total estable es más sencillo de auditar y no necesita
persistir el estado interno de un generador. La semilla de sesión se conserva
para identidad, no introduce aleatoriedad en esta versión.

### Decidir itinerario o score con un LLM

Se rechazó porque impediría reproducir resultados, fallaría con LM Studio apagado y
mezclaría generación lingüística con control de dominio. El LLM queda fuera de
esta versión.

### Guardar directamente cada respuesta en `SkillEvidence`

Se rechazó porque contaminaría el progreso normal, varias dimensiones no tienen
una habilidad curricular equivalente y una muestra diagnóstica no debe declarar
dominio.

### Añadir una tabla de entregas y otra de eventos

Separar entrega/evaluación y crear un ledger de transiciones sería más robusto
para evaluación asíncrona y auditoría hostil. Se pospone porque el motor actual
es síncrono, local y determinista; `0005` permite cumplir este alcance sin tocar
la base real ni introducir una migración prematura.

### Mantener las reglas dentro del servicio SQLAlchemy

Se rechazó porque acoplaría decisiones pedagógicas a transacciones y haría más
difícil probar selección, scoring y agregación como funciones reproducibles.

## Consecuencias

### Positivas

- El motor funciona completamente offline y con LM Studio apagado.
- La misma entrada produce la misma tarea y estimación.
- Los límites y transiciones impiden bucles y cierres incoherentes.
- Pausa, reinicio e idempotencia conservan el estado local.
- Respuestas y resultados mantienen historia append-only.
- El progreso del Learning Engine permanece intacto.
- No fue necesario migrar ni abrir la base real del usuario.

### Costes y limitaciones

- La entrega y su evaluación inicial no pueden separarse en dos commits.
- `selection_state` ofrece trazabilidad, no inmutabilidad de ledger.
- No existe todavía un banco pedagógico de producción.
- Las respuestas libres quedan sin evaluar en esta fase.
- La unicidad de creación presupone el uso del servicio serializado.
- La misma instancia de API no distingue todavía tiempo de respuesta de una
  pestaña inactiva; la futura interfaz deberá pausar o registrar actividad.
- El comportamiento se ha diseñado específicamente para SQLite local y un solo
  alumno.

## Fuera de alcance

- endpoints y frontend;
- LM Studio, LLM y generación o evaluación libre;
- audio, voz y pronunciación;
- proyección a `StudentSkill` o `SkillEvidence`;
- cambios de currículo;
- CEFR global o certificación;
- migraciones posteriores a `0005`.

La descripción operativa y las reglas exactas están en
[`docs/diagnostic-engine.md`](../diagnostic-engine.md).
