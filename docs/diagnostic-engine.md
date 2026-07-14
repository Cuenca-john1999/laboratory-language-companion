# Motor diagnóstico determinista

## 1. Propósito y estado

Este documento describe la implementación interna de `#006C` sobre la
persistencia `0005` y su adaptador HTTP de `#006D`. El motor selecciona, evalúa
y agrega tareas diagnósticas de texto de forma local, reproducible y acotada.
El núcleo no depende del frontend, Ollama o cualquier otro proveedor de modelos;
los endpoints se limitan a validar, traducir y proyectar sus contratos.

La implementación vive en
`apps/api/src/deutschos_api/diagnostic_engine/` y usa la versión
`diagnostic-engine.v1`. Sus contratos son esquemas Pydantic estrictos: los
campos desconocidos se rechazan y los scores y confianzas están limitados al
intervalo cerrado 0–1.

El motor permanece aislado del progreso normal:

- no importa el Learning Engine;
- no escribe `StudentSkill`;
- no escribe `SkillEvidence`;
- no declara habilidades dominadas;
- no calcula un nivel CEFR global;
- todo `DiagnosticResult` conserva `projection_status = not_projected`.

La especificación pedagógica general sigue en
[`diagnostic-system.md`](diagnostic-system.md). La decisión arquitectónica de
esta implementación se registra en
[`ADR 0007`](adr/0007-deterministic-diagnostic-engine.md); la semántica de ejes
y mappings curriculares se fija en
[`ADR 0008`](adr/0008-diagnostic-axes-and-skill-mappings.md).

## 2. Arquitectura

El paquete separa reglas puras de persistencia:

| Módulo | Responsabilidad |
| --- | --- |
| `schemas.py` | Comandos, recibos, candidatos y objetos de valor estrictos |
| `state_machine.py` | Transiciones explícitas de sesión y tarea |
| `selector.py` | Elegibilidad, dificultad, prioridad, desempate y parada |
| `scoring.py` | Evaluación determinista de respuestas textuales cerradas |
| `aggregation.py` | Estimaciones conservadoras por eje y dimensión curricular |
| `service.py` | Transacciones SQLite, idempotencia y reconstrucción del estado |
| `exceptions.py` | Errores esperados de transición, concurrencia y conflictos |

Las decisiones pedagógicas están en funciones puras. `DiagnosticEngineService`
coordina las filas de `0005`, pero no elige scores, dificultad o bandas por su
cuenta. Esta separación permite probar las mismas entradas sin base de datos y
reproducir después una sesión desde sus tareas y respuestas persistidas.

La API interna disponible es:

- `create_session`;
- `start_session`;
- `pause_session`;
- `resume_session`;
- `abandon_session`;
- `fail_session`;
- `select_next_task`;
- `submit_response`;
- `record_evaluation`;
- `aggregate_results`;
- `complete_session`;
- `get_session_state`.

No es una API HTTP. El adaptador de `api/diagnostic.py` traduce errores del
motor a códigos HTTP sin duplicar estas reglas.

Cada operación recibe un comando Pydantic. Las lecturas y la agregación sin una
acción de estado usan `SessionQuery`; no aceptan diccionarios abiertos ni un
identificador escalar sin validar.

## 3. Candidatos inyectables

El motor consume un `CandidateProvider`. Un candidato incluye identidad y
versión estables, clave de equivalencia, eje, tipo, dificultad 1–5,
prerrequisitos, modalidad, contenido, rúbrica, riesgo de ambigüedad y duración
estimada.

La selección distingue:

- `candidate_id@version`, para no volver a presentar la misma versión;
- `equivalence_key`, para no presentar una variante que mida lo mismo;
- `prerequisite_candidate_ids`, que requieren evidencia positiva previa.

No existe todavía un banco de preguntas de producción. Los proveedores finitos
usados en pruebas son fixtures, no currículo ni contenido pedagógico completo.
El proveedor de filesystem solo puede exponer bancos `production`; el banco real
actual permanece `draft` y no se presenta al alumno.

Solo son elegibles candidatos de texto y autoevaluables. Una tarea marcada
`manual_only`, audio u oral puede validarse como objeto futuro, pero el selector
de esta versión no la presenta.

Cada candidato tiene un único `axis`, persistido como `primary_axis`, que es el
constructo usado por el selector. `skill_id` es una correspondencia curricular
opcional: no modifica elegibilidad, dificultad, prioridad, cobertura ni parada.
Debe ser nulo cuando la tarea no aporta evidencia directa sobre una habilidad
curricular concreta.

`secondary_axes` se conserva para describir capacidades auxiliares, pero no crea
evidencia, no incrementa cobertura y no produce agregados. Convertirlo en una
dimensión operativa exigiría scoring y persistencia separados para evitar doble
conteo.

## 4. Estados de sesión

La API interna ofrece seis estados resumidos:

| Estado del motor | Estado persistido equivalente |
| --- | --- |
| `created` | `not_started` |
| `active` | `onboarding`, `calibrating`, `assessing`, `reviewing`, `time_limited` o `completing` |
| `paused` | `paused` |
| `completed` | `completed` |
| `abandoned` | `cancelled` o `abandoned` |
| `failed` | `error` |

Los estados persistidos conservan las fases pedagógicas del diseño:

```text
not_started → onboarding → calibrating → assessing ⇄ reviewing
                                                ↓
                                          time_limited
                                                ↓
                                           completing → completed
```

Las fases activas reanudables pueden pasar a `paused` y volver exactamente a la
fase guardada. Un fallo técnico pasa a `error`; si conserva una fase activa
válida en `paused_from_status`, puede reanudarse. Abandonar una sesión todavía
no iniciada produce `cancelled`; abandonar una iniciada produce `abandoned`.

Las siguientes repeticiones razonables son idempotentes:

- iniciar una sesión ya activa;
- pausar una sesión ya pausada;
- reanudar una sesión ya activa;
- repetir el avance a una fase ya alcanzada;
- completar, abandonar o marcar error cuando ya está en ese mismo estado.

Una transición distinta que salga de un estado terminal se rechaza. No se puede
reanudar una sesión completada, cancelada o abandonada.

### Tiempo activo

`resumed_at` marca el comienzo del tramo activo actual. Al pausar, abandonar,
fallar o completar, el servicio suma ese tramo a `active_seconds` y pone
`resumed_at` a nulo. El tiempo transcurrido mientras la sesión está pausada no se
cuenta. Las lecturas calculan el tramo activo todavía abierto sin necesitar una
escritura, con límite de 1.500 segundos.

Cada mutación crea un checkpoint y registra una identidad efímera de la instancia
del proceso. Tras reiniciar la API, la nueva instancia adopta el reloj desde el
instante de la primera operación y no imputa como activo el tiempo en que el
proceso estuvo apagado. Mientras la misma API permanece viva, el motor no puede
distinguir todavía entre tiempo de trabajo y una pestaña abandonada: ese intervalo
sí cuenta. La futura interfaz debe pausar explícitamente o aplicar una pausa por
inactividad; `#006C` no inventa eventos de usuario que aún no existen.

Como `started_at` también existe en una sesión recién creada, no se usa para
contar el tiempo anterior a `start_session`.

## 5. Estados de tarea

La máquina permite estas transiciones:

| Desde | Acción | Hacia |
| --- | --- | --- |
| `selected` | presentar | `presented` |
| `selected` | invalidar | `invalidated` |
| `presented` | responder | `answered` |
| `presented` | omitir | `skipped` |
| `presented` | instrucción no entendida | `not_understood` |
| `presented` | abandonar | `abandoned` |
| `presented` | invalidar | `invalidated` |
| `not_understood` | reintentar | `presented` |
| `not_understood` | omitir o abandonar | `skipped` o `abandoned` |
| `answered` | evaluar | `evaluated` |
| `evaluated` | único reintento explícito | `presented` |

Repetir la acción correspondiente sobre `presented`, `skipped`,
`not_understood`, `abandoned`, `evaluated` o `invalidated` es idempotente. Las
demás combinaciones se rechazan.

Selección y presentación ocurren dentro de la misma transacción. Si el proceso
se interrumpe después de insertar `selected`, el rollback elimina la inserción.
Como defensa adicional, una sesión reanudada puede recuperar una tarea
`selected` existente y presentarla sin crear otra.

## 6. Selección adaptativa

### Ejes

Los seis ejes prioritarios son:

1. comprensión escrita;
2. producción escrita;
3. gramática activa;
4. vocabulario receptivo;
5. vocabulario productivo;
6. `communication_repair.typed`.

Fluidez escrita y familiaridad cotidiana o profesional son ejes textuales
adicionales. Escucha, producción oral, pronunciación y fluidez oral se excluyen
defensivamente: una respuesta escrita nunca se convierte en evidencia de esas
modalidades.

### Elegibilidad

Antes de ordenar candidatos, el selector exige:

- modalidad `text` y evaluación automática disponible;
- eje incluido en los ejes textuales;
- identidad y equivalencia todavía no presentadas;
- menos de cinco tareas presentadas para el eje;
- prerrequisitos respaldados por evidencia positiva evaluable;
- dificultad 1 o 2 cuando el eje todavía no tiene evidencia evaluable.

Si las dos últimas tareas comparten tipo, la siguiente debe usar otro tipo. Si
no existe esa alternativa, no se fuerza una tercera tarea redundante y el motor
puede terminar por falta de candidatos.

### Dificultad

Cada eje comienza con objetivo de dificultad 1. Si no existe una tarea 1, una
tarea 2 es la única alternativa inicial admitida.

- `incorrect` baja un nivel, sin bajar de 1;
- `partial` mantiene el nivel;
- `correct_with_help` mantiene el nivel y nunca permite subir;
- `not_evaluable` se ignora para cambiar dificultad;
- dos resultados consecutivos `correct_without_help`, en candidatos
  independientes y dificultades iguales o contiguas, suben un nivel;
- resultados positivos antiguos no anulan un error reciente.

Después se elige la dificultad más cercana al objetivo. En un empate se prefiere
la dificultad menor.

### Prioridad y desempate estable

El orden de prioridad es:

1. eje prioritario todavía no evaluado;
2. contradicción que todavía no recibió desempate;
3. segunda muestra independiente de un eje prioritario;
4. reducción de incertidumbre de un eje prioritario;
5. cobertura y profundización de ejes textuales adicionales;
6. profundidad redundante de ejes ya suficientes.

Así, un desempate nunca desplaza la primera muestra de otro eje esencial; se
activa después de obtener esa cobertura inicial y antes de profundizar.

Una contradicción requiere evidencia positiva y negativa con confianza del
evaluador de al menos 0,6. El selector marca la tarea escogida con
`contradiction_tiebreak`. Una vez presentada una tarea de desempate para ese eje,
no abre otra; si la contradicción persiste, se conserva menor confianza.

Los empates se resuelven sin azar por:

1. prioridad y orden estable del eje;
2. penalización por repetir tipo;
3. distancia a la dificultad objetivo;
4. dificultad menor;
5. riesgo de ambigüedad bajo antes que medio o alto;
6. tipo, identificador y versión en orden lexicográfico.

La misma entrada produce siempre la misma decisión.

### Prevención de bucles

El motor combina varias barreras:

- no repite identidad ni equivalencia;
- no presenta más de cinco tareas por eje;
- permite como máximo un desempate por eje;
- no presenta tres tipos iguales consecutivos;
- no supera veinte tareas en total;
- no selecciona una tarea nueva después del límite temporal;
- devuelve una tarea abierta ya persistida antes de seleccionar otra.

Con estos límites, el selector no puede oscilar indefinidamente aunque las
evidencias sigan siendo contradictorias o insuficientes.

## 7. Evaluación determinista

El vocabulario público del motor usa `incorrect`; al persistir se transforma al
valor histórico `failure` de `DiagnosticResponse`. Los scores versionados son:

| Resultado | Score | Polaridad habitual |
| --- | ---: | --- |
| `incorrect` | 0,0 | negativa |
| `partial` | 0,4 | positiva parcial |
| `correct_with_help` | 0,7 | positiva |
| `correct_without_help` | 1,0 | positiva |
| `not_evaluable` | nulo | insuficiente |

Las estrategias disponibles son coincidencia exacta, lista de respuestas
aceptadas y tokens ordenados. La normalización usa Unicode NFC, espacios
normalizados y comparación sin distinguir mayúsculas salvo que la rúbrica lo
solicite. Una cobertura ordenada de al menos la mitad de los tokens puede
clasificarse como parcial; las listas de respuestas parciales siguen siendo
explícitas.

El riesgo de ambigüedad del candidato fija la confianza del evaluador:

| Riesgo | Confianza |
| --- | ---: |
| bajo | 1,00 |
| medio | 0,85 |
| alto | 0,65 |

Una ayuda distinta de `none` convierte una coincidencia correcta en
`correct_with_help`. La respuesta queda `not_evaluable`, con score nulo y
confianza 0, cuando:

- se abandona la tarea;
- no se entiende la instrucción;
- la respuesta está vacía;
- la respuesta está en español y necesita aclaración;
- el candidato requiere evaluación manual o no tiene clave determinista.

Responder en español no se convierte automáticamente en error lingüístico. Una
respuesta marcada fuera de tema sí es `incorrect`; una respuesta parcialmente
comunicativa es `partial`.

Existe un máximo de dos intentos. La segunda respuesta vacía agota el reintento
y la tarea se omite sin score. Un segundo intento después de una evaluación debe
registrar la asistencia `retry`.

## 8. Agregación por eje y dimensión curricular

La agregación usa únicamente la evaluación efectiva más reciente de cada tarea.
Las correcciones anteriores permanecen en la base, pero no cuentan dos veces.
Una evidencia solo participa en score y confianza cuando es evaluable y su
confianza de evaluador es al menos 0,6. Las demás aumentan el conteo de evidencia
insuficiente.

La selección y los criterios de parada observan el eje completo. Para persistir,
el servicio separa cada eje por `skill_id` opcional y mantiene una cadena
append-only independiente por `(axis, skill_id)`. Así, añadir o corregir evidencia
de una habilidad no deja vigente un resultado obsoleto de otra habilidad del
mismo eje. Las observaciones sin habilidad forman la dimensión `skill_id = NULL`;
no se mezclan con una habilidad curricular concreta.

El peso de una evidencia evaluable es:

```text
confianza_evaluador × (0,8 + 0,05 × dificultad) × asistencia
```

`asistencia` vale 0,75 si hubo ayuda y 1,0 sin ella. El score estimado es la
media ponderada; no es un porcentaje visible ni una estimación CEFR.

### Confianza de estimación

La confianza parte de anclas por cantidad:

| Evidencias independientes | Base |
| ---: | ---: |
| 1 | 0,25 |
| 2 | 0,52 |
| 3 | 0,66 |
| 4 | 0,78 |
| más de 4 | +0,04 por muestra, con límite de base 0,90 |

Se añade 0,08 por dos o más tipos de tarea y 0,04 por dificultades variadas.
La media de confianza de los evaluadores modula el total. Una contradicción
resta 0,24 y la evidencia insuficiente resta 0,03 por observación, con máximo
0,12. El valor final está limitado a 0,95.

Las etiquetas visibles son:

- baja: menos de 0,6;
- moderada: desde 0,6 y menos de 0,8;
- alta: desde 0,8.

Más observaciones pueden aumentar confianza, pero no elevan por sí solas el
score. La contradicción puede reducir confianza aunque el promedio no cambie.

### Cobertura y bandas

- sin observaciones: `not_assessed`, sin score ni banda;
- menos de dos evidencias efectivas: `insufficient`;
- tres evidencias, dos tipos, confianza mínima 0,6 y sin contradicción:
  `sufficient`;
- cualquier situación intermedia con al menos dos evidencias: `observed`.

No se calcula score si hay menos de dos evidencias o la confianza es menor que
0,4. En ese caso la banda es `insufficient_evidence`. Con score disponible:

- menos de 0,4: `initial_basis`;
- desde 0,4: `developing`;
- desde 0,65: `functional_guided`;
- `consistent_sample` exige score mínimo 0,8, confianza 0,75, cuatro evidencias,
  tres positivas, dos tipos, ninguna negativa y dificultad demostrada mínima 2.

Las modalidades futuras siempre se devuelven como `not_assessed`, incluso si se
inyecta por error una observación escrita con ese eje.

El agregado por `(axis, skill_id)` es historial diagnóstico. Incluso con un
`skill_id` válido, sus revisiones mantienen `projection_status = not_projected`
y el servicio no escribe `StudentSkill` ni `SkillEvidence`. La dimensión
`(axis, NULL)` conserva evidencia diagnóstica legítima sin inventar una
correspondencia curricular.

### Referencias CEFR

Una referencia solo puede aparecer por eje y habilidad cuando todas las
evidencias efectivas pertenecen al mismo `skill_id` y la membresía de esa
habilidad en el `CurriculumSkill` versionado declara `cefr_reference`. Exige al
menos dos evidencias positivas, confianza 0,6, score y dificultad demostrada.
Dentro de ese conjunto:

- `A1` exige al menos tres positivas, dos correctas sin ayuda, confianza 0,7,
  score mínimo 0,65 y una referencia curricular explícita `A1`;
- una referencia curricular `A0` solo puede producir `pre-A1`;
- una muestra que aún no cumple el umbral fuerte de `A1` queda, como máximo,
  en `pre-A1`.

La dificultad interna 1–5 nunca se convierte por sí sola en una banda CEFR.

No existe agregación CEFR global y ninguna banda tiene valor de certificación.
Los ejes `not_assessed` se muestran en lecturas, pero no se persisten como
resultados artificiales.

## 9. Criterios de parada

Los criterios se evalúan en este orden:

1. veinte tareas presentadas;
2. 1.500 segundos activos;
3. cobertura y confianza suficientes;
4. objetivo configurado de tareas alcanzado;
5. ausencia de candidatos elegibles;
6. continuar.

La cobertura mínima de cierre exige para cada eje prioritario al menos dos
evidencias, estado `observed` o `sufficient`, confianza mínima 0,5 y ninguna
contradicción sin desempatar. La parada anticipada por cobertura exige además
al menos diez evidencias evaluables y confianza 0,6 en cada eje. Como existen
seis ejes prioritarios, su cobertura mínima normalmente requiere al menos doce
tareas independientes.

El objetivo de sesión se configura entre 12 y 16 tareas; el valor predeterminado
es 14. Alcanzarlo solo termina la sesión cuando también existe cobertura mínima.
Los límites de veinte tareas y veinticinco minutos son absolutos y pueden cerrar
un diagnóstico parcial. Agotar candidatos también produce una conclusión
parcial cuando faltan ejes.

### Contrato editorial de alcanzabilidad

Antes de declarar un banco `reviewed`, sus candidatos deben permitir recorrer
estas reglas desde una sesión vacía. Cada eje prioritario necesita al menos una
entrada de dificultad 1 o 2 y dos evidencias independientes posibles; las tareas
de dificultad 3 o superior necesitan una ruta previa. El límite de cinco por eje
no puede impedir que existan al menos doce tareas potencialmente seleccionables
ni que escenarios ordinarios alcancen diez evidencias evaluables.

También deben descartarse tareas permanentemente inalcanzables y callejones por
prerrequisitos, repetición de tipos o límites. No se exige que una sesión con
respuestas vacías o no evaluables produzca cobertura completa: ese cierre parcial
describe evidencia insuficiente, no necesariamente un banco inválido. Estas
invariantes están documentadas pero todavía no automatizadas; `#006E4C` añadirá
validación y simulación reproducibles sin cambiar el selector.

## 10. Transacciones, concurrencia e idempotencia

Todas las mutaciones usan una única transacción controlada por el servicio. En
SQLite, el servicio ejecuta `BEGIN IMMEDIATE` antes de leer estado mutable. Si
el llamador ya abrió una transacción, un `UPDATE ... WHERE 0` adquiere el bloqueo
de escritura. Esto serializa el ciclo leer–decidir–escribir y evita dos tareas,
intentos o agregados concurrentes incompatibles.

Un bloqueo que supera el `busy_timeout` se transforma en
`DiagnosticConcurrencyError`. Toda excepción hace rollback completo. El
servicio no devuelve después de un commit parcial.

La idempotencia usa varias identidades:

- `create_session` usa `request_id` como semilla persistida y compara el payload;
- acciones de sesión y selección guardan `operation_id`, tipo y payload en
  `selection_state.operation_receipts`;
- una selección repetida devuelve la tarea registrada para esa operación;
- una repetición compatible de otra acción devuelve la entidad lógica en su
  proyección actual, sin repetir el efecto ni prometer una instantánea histórica;
- `evaluation_id` identifica una evaluación y `submission_id` una entrega;
- reutilizar una identidad con otro payload produce conflicto;
- resultados por `(axis, skill_id)` usan UUIDv5 deterministas y solo crean una
  revisión nueva cuando ese agregado cambió;
- las correcciones de respuesta y resultado siguen las cadenas append-only de
  `0005`.

La creación no necesita una restricción nueva: `BEGIN IMMEDIATE` hace que las
creaciones realizadas mediante este servicio sean seriales. Escritores que
eviten deliberadamente el servicio no reciben esa garantía de dominio.

## 11. Pausa, reinicio y reconstrucción

La tarea presentada conserva contenido, rúbrica, identidad y versión. Al
reiniciar la API no se regenera. El servicio reconstruye:

- tareas presentadas y equivalencias utilizadas;
- última evaluación efectiva de cada tarea y sus correcciones;
- fase persistida y fase anterior a la pausa;
- tramo activo y segundos acumulados;
- recibos de operaciones idempotentes;
- resultados efectivos de cada cadena de revisiones.

Por ello una pausa puede durar horas sin aumentar tiempo activo y la sesión
continúa con la misma tarea o la siguiente decisión determinista. El proveedor
de candidatos debe seguir ofreciendo la versión declarada por la sesión; no se
mezclan silenciosamente versiones de diagnóstico.

Un reinicio con la sesión todavía activa tampoco cuenta el periodo sin proceso.
La inactividad con la misma instancia viva sí permanece como limitación hasta
que una interfaz aporte pausa o actividad explícitas.

## 12. Auditoría y límites de `selection_state`

Las transiciones de sesión y tarea se añaden a listas en `selection_state`, con
estado anterior, estado nuevo, acción, razón, UTC y versión del motor. Los
recibos de operación también viven en ese JSON.

Esto ofrece trazabilidad de servicio, pero no es un ledger inmutable de base de
datos: `selection_state` es una proyección mutable y `0005` no tiene una tabla de
eventos con triggers append-only. Las respuestas y resultados sí conservan su
inmutabilidad mediante los triggers existentes.

Si en el futuro se exige auditoría resistente a escrituras directas, será
necesaria una entidad `DiagnosticTransitionEvent` append-only. No se añadió en
`#006C` para mantener el alcance y porque requeriría otra migración.

## 13. Ejemplo de flujo

1. `create_session` crea `not_started` con una clave idempotente.
2. `start_session` pasa a `onboarding` y abre el reloj activo.
3. La primera selección entra en `calibrating` y presenta una tarea de
   dificultad 1 de un eje prioritario no cubierto. La fase pasa a `assessing`
   durante el flujo normal solo después de dos anclas evaluables, no por
   presentar dos tareas. Agotar candidatos o alcanzar un límite puede atravesar
   esa fase únicamente para cerrar un diagnóstico parcial.
4. La entrega y su evaluación determinista se guardan juntas. Una coincidencia
   sin ayuda produce `correct_without_help`.
5. El selector alterna ejes para cubrirlos antes de profundizar.
6. Dos aciertos independientes y consecutivos en un eje permiten subir una
   dificultad; un error posterior baja el objetivo.
7. Una evidencia positiva y otra negativa priorizan una única tarea de
   desempate.
8. `pause_session` cierra el tramo activo. Después de reiniciar la API,
   `resume_session` vuelve a la fase anterior sin contar la pausa.
9. Al alcanzar cobertura, objetivo, límite o agotamiento, la sesión pasa por
   `reviewing` y `completing`.
10. `complete_session` persiste revisiones por eje y dimensión curricular
    observados y termina en `completed`; los ejes sin muestra permanecen
    `not_assessed` en la lectura.

## 14. Sin nueva migración

`#006C` reutiliza íntegramente las cuatro entidades y restricciones de `0005`.
No añade columnas, tablas, índices ni triggers y no migra la base real del
usuario. La persistencia actual basta porque selección y evaluación
determinista se realizan de forma síncrona y atómica.

## 15. Limitaciones y fuera de alcance

Limitaciones actuales:

- `DiagnosticResponse` representa a la vez entrega y evaluación; no puede
  guardar una entrega pendiente y evaluarla de forma asíncrona en otro commit;
- el historial de transiciones en `selection_state` no es un ledger DB
  inmutable;
- no hay banco real de preguntas; solo candidatos finitos inyectados para
  pruebas;
- solo se evalúan rúbricas cerradas autoevaluables;
- una respuesta libre sin clave queda `not_evaluable`;
- la garantía de creación única presupone que las escrituras pasan por el
  servicio;
- la inactividad no puede detectarse mientras la misma instancia de API sigue
  viva; la futura interfaz debe emitir pausa o actividad explícita;
- la alcanzabilidad global de un banco aún no se valida automáticamente;
- el motor está diseñado para SQLite local y un único alumno.

Queda fuera de alcance:

- frontend diagnóstico;
- LLM, Ollama y evaluación libre;
- audio, voz, escucha y pronunciación;
- banco pedagógico de producción;
- integración o proyección a `StudentSkill` y `SkillEvidence`;
- modificaciones curriculares;
- métricas o certificación CEFR global;
- borrado de historial y políticas futuras de audio.

El frontend diagnóstico para el alumno se pospone hasta que un banco supere el
contrato de alcanzabilidad. Mientras tanto, la verificación usa la herramienta
editorial, pruebas HTTP y el futuro simulador de `#006E4C`.

## 16. Adaptador HTTP de `#006D`

El router `api/diagnostic.py` expone el servicio bajo `/api/diagnostic` y usa
los contratos estrictos de `schemas/diagnostic_api.py`. No contiene reglas de
selección, scoring, agregación ni persistencia. Cada mutación conserva el
`BEGIN IMMEDIATE` y el commit único del servicio interno.

| Método y ruta | Resultado público |
| --- | --- |
| `POST /sessions` | Crea o reproduce una sesión (`201`) |
| `GET /sessions/{session_id}` | Estado real, tarea vigente y agregados |
| `POST /sessions/{session_id}/start` | Inicia la sesión |
| `POST /sessions/{session_id}/pause` | Pausa la sesión |
| `POST /sessions/{session_id}/resume` | Reanuda la sesión |
| `POST /sessions/{session_id}/abandon` | Abandona la sesión |
| `POST /sessions/{session_id}/fail` | Registra un fallo técnico |
| `POST /sessions/{session_id}/next-task` | Selecciona o reproduce la siguiente tarea |
| `POST /sessions/{session_id}/responses` | Guarda y evalúa una respuesta atómicamente |
| `POST /responses/{response_id}/corrections` | Añade una revisión append-only |
| `GET /sessions/{session_id}/results` | Lee agregados sin crear revisiones |
| `POST /sessions/{session_id}/complete` | Completa solo cuando el motor lo permite |

Los cuerpos de creación y de cada mutación contienen UUID de operación. El
servicio conserva el fingerprint del contenido: repetir exactamente una
petición devuelve el recurso existente, mientras reutilizar el UUID con otro
contenido devuelve `409`. Esto incluye entregas concurrentes idénticas; SQLite
las serializa y solo una crea filas.

Los DTO públicos se construyen campo por campo. Una tarea incluye instrucciones,
enunciado, opciones, modalidad, eje y dificultad, pero nunca `expected_answer`,
rúbrica, claves de equivalencia, prerrequisitos internos ni razón de selección.
Una respuesta pública incluye el resultado estructurado, no el texto del alumno,
justificación ni códigos internos. Además, el adaptador rechaza recursivamente
claves reservadas dentro del contenido proporcionado por un banco antes de
persistir la tarea.

La traducción de errores es deliberadamente estable y sanitizada:

- `404` para sesión o respuesta inexistente;
- `409` para transición, revisión o idempotencia conflictiva;
- `422` para un cuerpo o una combinación de evaluación inválidos;
- `503` para proveedor ausente, contenido inseguro, contención local o fallo de
  persistencia conocido.

No se incorporan mensajes internos ni trazas a la respuesta HTTP y las rutas no
registran textos del alumno o contenido de tareas. No se importa ni consulta
Ollama.

### Proveedor de producción

`CandidateProvider` se resuelve por dependencia. Los tests inyectan un conjunto
mínimo y determinista; no forma parte del contenido para Jhon. La dependencia de
producción devuelve `503` con un mensaje claro hasta que exista un banco
pedagógico versionado con estado editorial `production`. Los bancos `draft`,
`reviewed` y `deprecated` no son seleccionables. Por ello, en el estado actual todas las rutas
diagnósticas permanecen cerradas de forma segura en una instalación normal. El
frontend futuro no debe sustituir esta ausencia con tareas inventadas.
