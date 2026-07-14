# ADR 0008: Ejes diagnósticos y correspondencias curriculares opcionales

- Estado: aceptado
- Fecha: 2026-07-14

## Contexto

El [ADR 0006](0006-adaptive-diagnostic.md) definió un diagnóstico por capacidades
separadas y el [ADR 0007](0007-deterministic-diagnostic-engine.md) convirtió ese
diseño en un selector y agregador deterministas. El formato de contenido permite
que cada tarea declare un `axis`, un `skill_id` opcional y `secondary_axes`.

En la implementación actual, esos campos no tienen la misma función:

- el selector, la dificultad, los límites, la cobertura y la parada operan sobre
  `axis`;
- `skill_id` conserva una correspondencia curricular opcional al persistir e
  informar resultados;
- `secondary_axes` se conserva como metadato, pero no produce evidencia;
- todos los resultados permanecen separados del progreso normal con
  `projection_status = not_projected`.

Sin un contrato explícito, un autor podría asignar una habilidad curricular solo
para rellenar el campo, presentar un eje secundario como medido o publicar un
banco cuyos candidatos no puedan recorrer las reglas reales del selector.

## Problema

Un constructo diagnóstico amplio —por ejemplo, comprensión escrita— puede usar
material gramatical sin medir de forma directa una habilidad gramatical concreta.
También existen funciones comunicativas para las que `a0-a1.v1` todavía no tiene
una habilidad equivalente. Forzar correspondencias aproximadas convertiría
evidencia contextual en una conclusión curricular que el ejercicio no defiende.

Además, la validez individual de cada JSON no garantiza la alcanzabilidad del
banco completo. Un banco puede carecer de tareas de entrada, concentrar demasiadas
tareas en un eje o agotar candidatos antes de reunir la cobertura y evidencia
mínimas.

## Decisión

### `axis` es el constructo diagnóstico principal

Cada tarea tiene exactamente un eje principal. El selector usa exclusivamente
ese eje para:

- abrir y ajustar dificultad según evidencia previa del mismo eje;
- aplicar el máximo de cinco tareas por eje;
- priorizar cobertura y desempates;
- calcular cobertura y criterios de parada;
- construir la agregación diagnóstica principal.

Los seis ejes prioritarios de texto v1 permanecen:

1. `reading_comprehension`;
2. `written_production`;
3. `active_grammar`;
4. `receptive_vocabulary`;
5. `productive_vocabulary`;
6. `communication_repair.typed`.

Un eje diagnóstico describe qué capacidad observa la tarea. No equivale
necesariamente a una habilidad del currículo.

### `skill_id` es una correspondencia curricular opcional

`skill_id` enlaza una tarea con `CurriculumSkill` únicamente cuando una respuesta
correcta o incorrecta aporta evidencia directa y defendible sobre esa habilidad.
Puede usarse para persistencia, informes y referencias orientativas por habilidad,
pero no controla selección, cobertura, dificultad ni parada.

El valor debe ser `null` cuando:

- el constructo diagnóstico no tiene una habilidad curricular equivalente;
- la relación con la habilidad es indirecta;
- la tarea usa conocimientos auxiliares, pero mide principalmente otra capacidad;
- asignar una habilidad produciría una interpretación curricular engañosa.

Una tarea con `skill_id = null` continúa produciendo evidencia diagnóstica válida
por su eje. Una tarea con `skill_id` no se convierte automáticamente en evidencia
del Learning Engine.

Ejemplos conceptuales para revisar el banco actual, sin modificarlo mediante este
ADR:

| Tarea conceptual | Eje principal defendible | Correspondencia curricular |
| --- | --- | --- |
| Comprender una relación expresada mediante acusativo | `reading_comprehension` | `skill_id = null` si el objetivo es comprender el texto; el acusativo queda en notas editoriales |
| Pedir que repitan una expresión | `communication_repair.typed` | `skill_id = null`; contener `können` no prueba por sí solo `grammar.modal_verbs_basic` |
| Recuperar la palabra `Mikroskop` | `productive_vocabulary` | `skill_id = 22` es defendible cuando la tarea mide directamente el léxico de laboratorio |

### `secondary_axes` es metadato descriptivo

`secondary_axes` puede documentar capacidades auxiliares presentes en una tarea,
pero en la versión actual:

- no crea evidencia independiente;
- no participa en selección ni dificultad;
- no incrementa cobertura;
- no produce agregados secundarios;
- no debe mostrarse como una evaluación multidimensional ejecutada por el motor.

Los autores no deben usarlo para prometer cobertura. Hacerlo operativo requeriría
otra decisión arquitectónica, reglas de scoring por dimensión, persistencia,
resolución de doble conteo, compatibilidad histórica y pruebas específicas.

### Resultados separados y no proyectados

La evidencia y los resultados diagnósticos se guardan en sus propias entidades y
permanecen `not_projected`. El diagnóstico no escribe `StudentSkill` ni
`SkillEvidence`. Esta regla se mantiene tanto para tareas con `skill_id` como para
tareas sin correspondencia curricular.

Una futura proyección deberá ser explícita, revisable y versionada. Tendrá que
comprobar cobertura, confianza y correspondencia antes de crear correcciones
append-only en el progreso normal. La separación actual evita que un diagnóstico
incompleto, contradictorio o de baja confianza contamine el aprendizaje.

### Versionado semántico de tareas

Una nueva versión de tarea es obligatoria cuando cambia cualquier aspecto que
pueda reinterpretar su evidencia, selección o evaluación, incluido:

- eje principal o `skill_id`, también entre un ID y `null`;
- dificultad;
- respuesta correcta o variantes aceptadas;
- rúbrica, estrategia o política de scoring;
- contenido visible de forma que altere la competencia evaluada;
- objetivo pedagógico;
- `equivalence_group`;
- modalidad, `task_type` o `response_type`.

Un cambio en la rúbrica o scoring incrementa además `rubric_version` conforme al
contrato existente. La publicación editorial actualiza `bank_version` cuando
corresponda. Nunca se reinterpretan evidencias históricas después de cambiar eje,
habilidad, rúbrica u objetivo: cada sesión conserva la identidad y versión que
presentó.

Cambios exclusivamente editoriales pueden conservar la versión de tarea solo si
no alteran contenido visible, selección, evaluación ni significado. Ejemplos:
corregir `authoring_notes`, etiquetas o notas del manifiesto. Una corrección de
instrucción visible que pueda cambiar la respuesta del alumno no es puramente
editorial.

### Contrato de alcanzabilidad previo a `reviewed`

Antes de pasar a `reviewed`, un banco debe ser recorrible bajo las reglas reales
del selector, no solo validar su esquema. Como mínimo:

- todos los ejes prioritarios tienen candidatos;
- cada eje prioritario tiene al menos una tarea inicial de dificultad 1 o 2;
- cada eje puede aportar al menos dos evidencias independientes;
- toda tarea de dificultad 3 o superior tiene una ruta previa alcanzable;
- el máximo de cinco tareas por eje no impide el objetivo global;
- existen al menos doce tareas potencialmente seleccionables;
- escenarios ordinarios pueden alcanzar al menos diez evidencias evaluables;
- no hay tareas permanentemente inalcanzables;
- continuar no depende de respuestas perfectas;
- los tipos consecutivos, prerrequisitos y límites no crean callejones sin salida.

Una sesión con respuestas vacías, abandonadas o `not_evaluable` puede terminar
parcial sin que eso demuestre un defecto editorial. La validación debe distinguir
esa insuficiencia del alumno de un banco estructuralmente inalcanzable.

Estas condiciones todavía no se validan automáticamente. `#006E4C` añadirá
validación estática y simulación reproducible del selector.

### Expectativas por estado editorial

- `draft`: puede contener problemas de cobertura o alcanzabilidad; las
  herramientas pueden informar errores o advertencias de preparación y nunca se
  sirve en producción.
- `reviewed`: ha superado contratos de esquema y alcanzabilidad, no contiene
  tareas permanentemente inalcanzables y tiene revisión pedagógica. Aún puede no
  estar habilitado en producción.
- `production`: cumple todo lo anterior, tiene checksums e integridad correctos,
  pruebas de integración completas y activación explícita. No filtra contenido
  privado ni modifica el progreso normal sin una decisión posterior separada.

## Alternativas consideradas

### Exigir siempre una habilidad curricular

Se rechazó porque obligaría a inventar o exagerar correspondencias para lectura,
escritura y reparación comunicativa. El currículo no debe crecer como parche del
banco.

### Seleccionar y cubrir por `skill_id`

Se rechazó porque mezclaría progresión curricular con capacidades diagnósticas,
dejaría fuera tareas legítimas con `null` y cambiaría las reglas ya versionadas
del selector.

### Agregar automáticamente `secondary_axes`

Se rechazó porque contaría una sola respuesta varias veces sin rúbricas separadas
ni una política de atribución. El campo conserva por ahora valor editorial.

### Debilitar umbrales o límites del selector

Se rechazó como solución al desequilibrio de un banco. Reducir evidencias, admitir
dificultad alta desde cero o ampliar gramática ocultaría el problema de contenido
sin mejorar la validez diagnóstica.

### Añadir ahora `assessment_construct`

Se pospone. El eje principal y un pool pequeño curado bastan para la primera
versión alcanzable; otro nivel de clasificación añadiría contrato, migración y
selección antes de demostrar su necesidad.

## Consecuencias

### Positivas

- El banco puede medir capacidades sin mappings curriculares artificiales.
- Selección y agregación conservan una semántica única y auditable por eje.
- Las referencias curriculares siguen siendo útiles cuando son directas.
- El progreso normal permanece protegido.
- La promoción editorial podrá detectar incompatibilidades globales antes de
  exponer contenido.

### Costes y limitaciones

- `skill_id = null` limita referencias curriculares y bandas por habilidad para
  esa tarea, aunque no invalida el resultado por eje.
- `secondary_axes` no representa todavía evaluación multidimensional real.
- El contrato de alcanzabilidad requiere una herramienta que aún no existe.
- Un pool grande dentro de un solo eje puede seguir dependiendo del desempate
  lexicográfico hasta introducir subconstructos explícitos.

## Decisiones aplazadas

- añadir habilidades curriculares de lectura o escritura;
- añadir una habilidad curricular de reparación comunicativa;
- crear un campo `assessment_construct`;
- seleccionar tareas por subconstructo;
- convertir `secondary_axes` en evidencia;
- proyectar resultados al Learning Engine;
- cambiar límites, umbrales o ejes prioritarios del selector;
- admitir audio o voz.

Se revisará la necesidad de `assessment_construct` si existen pools grandes en
un eje, el orden lexicográfico afecta la cobertura pedagógica, es necesario
garantizar subhabilidades dentro de gramática, un pool curado demuestra ser
insuficiente o más de un banco real reproduce el mismo problema.

## Criterios de revisión futura

Esta decisión debe revisarse antes de:

- hacer operativos los ejes secundarios;
- proyectar cualquier resultado a `StudentSkill` o `SkillEvidence`;
- introducir nuevos ejes o modalidades;
- cambiar la identidad persistida de una dimensión agregada;
- añadir selección por subconstructo;
- promover el primer banco a `production` si la simulación revela que estas
  invariantes no bastan.

Las reglas operativas están en
[`docs/diagnostic-engine.md`](../diagnostic-engine.md) y las reglas de autoría en
[`docs/content-format.md`](../content-format.md).
