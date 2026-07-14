# Formato de contenido pedagógico

## 1. Objetivo y alcance

DeutschOS mantiene el contenido pedagógico local bajo:

```text
data/
├── curriculum/
├── diagnostic/
├── lessons/
└── review/
```

`data/diagnostic/` es la ubicación autorizada para bancos diagnósticos. Los
otros directorios siguen reservados para contratos posteriores. La
infraestructura no usa red y no habilita LLM, audio, frontend ni proyección al
progreso. `0.1.0` inició una muestra auténtica con seis tareas, `0.2.0` la amplió
a doce y `0.3.0` reequilibra el banco de doce tareas entre los seis ejes
prioritarios. El banco permanece en estado `draft`: no está disponible para el
alumno ni constituye un diagnóstico completo.

Se conserva `data/` porque ya separa de forma clara contenido versionable,
datos locales y código, funciona desde el SSD externo y no presenta un riesgo
técnico que justifique una migración estética a otra carpeta.

La semántica normativa de ejes y correspondencias curriculares se define en el
[`ADR 0008`](adr/0008-diagnostic-axes-and-skill-mappings.md).

## 2. Unidad de publicación

Un banco consta de:

1. un manifiesto situado directamente en `data/diagnostic/` y terminado en
   `.bank.json`;
2. uno o más archivos de tareas terminados en `.tasks.json` y declarados por el
   manifiesto.

El cargador no interpreta cualquier JSON como contenido. Rechaza archivos
huérfanos, archivos no declarados y manifiestos que apunten fuera del directorio
autorizado. Ensambla y valida el conjunto completo antes de devolver candidatos;
nunca ofrece una carga parcial.

## 3. Manifiesto

El esquema actual es `diagnostic-bank-manifest.v1`. Su forma es:

```json
{
  "schema_version": "diagnostic-bank-manifest.v1",
  "bank_id": "<identificador-estable>",
  "bank_version": "<version-del-banco>",
  "diagnostic_version": "diagnostic-text.v1",
  "target_language": "de",
  "levels": ["pre-A1", "A1"],
  "modalities": ["text"],
  "axes": ["<eje-declarado>"],
  "editorial_status": "draft",
  "created_on": "2026-07-14",
  "updated_on": "2026-07-14",
  "files": [
    {
      "path": "<nombre>.tasks.json",
      "sha256": null
    }
  ],
  "minimum_compatibility": {
    "application_version": "0.3.0",
    "diagnostic_engine_version": "diagnostic-engine.v1",
    "curriculum_version": "a0-a1.v1"
  },
  "editorial_notes": "<notas-para-revisión>"
}
```

El fragmento describe estructura; no constituye un banco ni contenido listo
para Jhon.

### Campos del manifiesto

| Campo | Regla |
| --- | --- |
| `bank_id` | Identidad estable en minúsculas; no cambia entre revisiones del mismo banco |
| `bank_version` | Publicación editorial del banco |
| `schema_version` | Contrato de serialización; versiones desconocidas se rechazan |
| `diagnostic_version` | Versión de sesión compatible; nunca se mezclan versiones |
| `target_language` | `de` en texto v1 |
| `levels` | Bandas cubiertas realmente por las tareas: `pre-A1` y/o `A1` |
| `modalities` | Exactamente `text` en v1 |
| `axes` | Inventario de ejes primarios y secundarios presentes; no acredita cobertura |
| `editorial_status` | `draft`, `reviewed`, `production` o `deprecated` |
| `created_on` | Fecha ISO 8601 de creación editorial |
| `updated_on` | Fecha ISO 8601 de revisión; nunca anterior a `created_on` |
| `files` | Lista cerrada de archivos relativos `.tasks.json` |
| `minimum_compatibility` | Aplicación, motor y currículo mínimos admitidos |
| `editorial_notes` | Decisiones, revisión pendiente y contexto de publicación |

Los niveles, modalidades y ejes declarados deben coincidir con las tareas. Esta
coherencia estructural no demuestra cobertura: la alcanzabilidad se calcula solo
con los ejes principales y las reglas del selector.

## 4. Estados editoriales

- `draft`: trabajo editable; puede tener brechas de cobertura o alcanzabilidad,
  las herramientas pueden informar problemas de preparación y nunca está
  disponible para el alumno.
- `reviewed`: revisión pedagógica realizada y validaciones de contrato y
  alcanzabilidad superadas, sin tareas permanentemente inalcanzables; todavía no
  implica publicación.
- `production`: cumple lo exigido para `reviewed`, integridad, pruebas de
  integración y activación explícita; es la única fuente que el proveedor de la
  API puede exponer.
- `deprecated`: histórico no seleccionable.

Pasar a `production` exige al menos un archivo, tareas deterministas y un SHA-256
válido por archivo declarado. Los checksums son opcionales durante `draft` y
`reviewed` para no convertir cada edición en trabajo mecánico. Si se proporciona
uno en cualquier estado, siempre se verifica.

Existe un manifiesto `draft`, pero ningún banco `production`. La API mantiene su
`503` conocido: la presencia de material en revisión no lo activa.

## 5. Archivo de tareas

Cada archivo declarado usa `diagnostic-task-file.v1`:

```json
{
  "schema_version": "diagnostic-task-file.v1",
  "bank_id": "<mismo-bank-id-del-manifiesto>",
  "tasks": []
}
```

`bank_id` impide asociar accidentalmente un fragmento con otro banco. Un mismo
archivo no puede declararse dos veces ni pertenecer a dos manifiestos.

## 6. Contrato de tarea

Una tarea separa explícitamente tres ámbitos:

- `public`: instrucciones, prompt y opciones que puede recibir el alumno;
- `private`: rúbrica, claves y política explícita de scoring usada por el
  evaluador;
- `editorial`: tags y notas de autoría que nunca llegan al motor ni al cliente.

El resto del objeto contiene identidad, selección y compatibilidad:

| Campo | Tipo o límite |
| --- | --- |
| `id` | ID estable, 2–100 caracteres permitidos |
| `version` | Versión de la tarea |
| `level` | `pre-A1` o `A1` |
| `axis` | `DiagnosticAxis` existente |
| `secondary_axes` | Ejes descriptivos adicionales, textuales y sin duplicados; no generan evidencia |
| `skill_id` | Entero positivo o `null`; el campo es obligatorio |
| `task_type` | `DiagnosticTaskType` existente |
| `difficulty` | Entero 1–5 |
| `modality` | Literal `text` |
| `response_type` | `single_choice`, `short_text`, `ordered_tokens` o `free_text` |
| `estimated_seconds` | 5–600 |
| `prerequisites` | IDs presentes en el mismo banco |
| `equivalence_group` | Grupo estable que mide la misma dimensión |
| `ambiguity_risk` | `low`, `medium` o `high` |
| `scoring_mode` | `deterministic` o `manual` |
| `rubric_version` | Versión independiente de la semántica evaluadora |
| `public` | Contenido visible estricto |
| `private` | Rúbrica y política de scoring estrictas |
| `metadata` | Tema y contexto no sensibles |
| `editorial` | Tags y `authoring_notes` |

El formato admite representar `manual` únicamente para preparación editorial
futura, pero un banco `production` de la versión actual solo puede contener
`deterministic`. Esto no incorpora un evaluador manual ni LLM.

### Eje principal y correspondencia curricular

`axis` es el constructo diagnóstico principal. Cada tarea declara exactamente
uno y el motor lo usa para selección, dificultad, límites, cobertura, parada y
agregación principal. Un eje no equivale necesariamente a una habilidad
curricular.

`skill_id` es una correspondencia opcional aunque su clave sea obligatoria en el
JSON. Solo debe contener un ID cuando el resultado de la tarea aporte evidencia
directa y defendible sobre esa habilidad. Debe ser `null` si la relación es
indirecta, el objetivo pertenece a otro constructo o el mapping induciría una
interpretación engañosa. `null` no invalida la evidencia por eje, y un ID no
proyecta por sí solo nada al Learning Engine.

`secondary_axes` es únicamente metadato descriptivo en la versión actual. No
selecciona, no abre dificultad, no incrementa cobertura y no genera evidencias ni
agregados secundarios. Los autores no deben declararlo o presentarlo como una
evaluación multidimensional ejecutada por el motor.

## 7. Rúbricas y scoring

Las estrategias reutilizan `RubricStrategy` del motor:

- `exact_match`;
- `accepted_answers`;
- `ordered_tokens`;
- `manual_only`.

El validador cruza `response_type`, `scoring_mode`, opciones y rúbrica. Entre
otras condiciones:

- selección única necesita al menos dos opciones públicas únicas;
- respuestas correctas o parciales deben existir entre esas opciones;
- tokens ordenados necesitan una clave de tokens;
- scoring determinista no acepta `manual_only`;
- scoring manual solo acepta `manual_only`;
- producción no admite tareas no deterministas.

Cada tarea determinista declara además `private.scoring_policy`. Este bloque
documenta el contrato que ya ejecuta el scorer, sin modificarlo:

- normalización Unicode NFC;
- eliminación de espacios exteriores y colapso de espacios interiores;
- sensibilidad a mayúsculas coherente con `rubric.case_sensitive`;
- puntuación significativa;
- score máximo `1.0`;
- condiciones explícitas para respuesta correcta e incorrecta.

El validador rechaza una discrepancia entre la sensibilidad a mayúsculas de la
política y la rúbrica. Las tareas del banco inicial ignoran diferencias de
capitalización. La puntuación continúa siendo significativa: la revisión
editorial `0.1.1` enumera de forma cerrada el punto final tolerado en los dos
huecos y la oración declarativa, y el signo de interrogación final tolerado en
la pregunta. No se elimina ni transforma globalmente ningún signo.

La conversión a `TaskCandidate` copia únicamente `public.instructions`,
`public.prompt` y opciones al contenido presentable. Rúbrica, respuestas,
prerrequisitos, equivalencia, tags y notas editoriales permanecen privados. El
adaptador HTTP mantiene además su lista defensiva de claves reservadas.

## 8. Validación relacional

Pydantic rechaza campos extra, campos ausentes, enums desconocidos y límites
inválidos. El cargador añade validaciones del conjunto:

- `bank_id` y task IDs duplicados;
- versiones de esquema, aplicación, motor o currículo incompatibles;
- archivo declarado ausente, repetido o con `bank_id` diferente;
- JSON malformado con archivo, línea y columna;
- errores Pydantic con archivo y ruta del campo;
- paths absolutos, `..`, barras no portables y symlinks;
- JSON no declarado;
- SHA-256 ausente en producción o incorrecto;
- máximo de 2 MiB por archivo y 16 MiB para la carga completa;
- modalidades o ejes no textuales;
- niveles, modalidades y ejes del manifiesto incoherentes;
- prerrequisitos ausentes;
- `skill_id` fuera de `a0-a1.v1`;
- un grupo de equivalencia que mezcle eje o habilidad;
- rúbrica no evaluable declarada como determinista;
- IDs y orden de candidatos reproducibles.

La API contrasta `skill_id` mediante una lectura de `CurriculumSkill`. La
herramienta editorial abre SQLite en modo de solo lectura. Ninguna validación
crea o modifica filas.

### Alcanzabilidad antes de revisión

La validez local de los archivos no basta para promover un banco a `reviewed`.
El conjunto debe ser recorrible bajo el selector real:

- cada uno de los seis ejes prioritarios tiene candidatos y una entrada de
  dificultad 1 o 2;
- cada eje puede aportar dos evidencias independientes;
- las tareas de dificultad 3 o superior tienen una ruta previa;
- el máximo de cinco tareas por eje permite alcanzar el objetivo global;
- hay al menos doce tareas potencialmente seleccionables y los escenarios
  ordinarios pueden producir diez evidencias evaluables;
- no existen tareas permanentemente inalcanzables ni callejones causados por
  tipos consecutivos, prerrequisitos o límites;
- continuar no depende de respuestas perfectas.

Una sesión dominada por respuestas `not_evaluable` puede finalizar parcial sin
que el banco sea inválido. `#006E4C` implementa estas comprobaciones mediante
validación estática, diez escenarios deterministas y exploración acotada del
selector real.

## 9. Seguridad y comportamiento de producción

El cargador usa exclusivamente `pathlib`, `json`, `hashlib` y Pydantic. No hace
peticiones de red, no ejecuta contenido, no importa módulos declarados por los
archivos y no emite telemetría.

Toda ruta se resuelve dentro del directorio autorizado. Los archivos simbólicos
se rechazan incluso si parecen apuntar dentro, evitando que su destino cambie
entre revisiones. Cualquier error aborta la construcción completa del proveedor.

Los errores editoriales conservan archivo y ubicación para uso local. La API no
reenvía esos detalles: responde con un `503` sanitizado. El cargador no procesa
respuestas del alumno y no añade logs con prompts, rúbricas o datos personales.

En producción, `FilesystemCandidateProvider` selecciona únicamente bancos
`production`. `draft`, `reviewed` y `deprecated` no pueden aparecer al usuario.
Los proveedores sintéticos continúan siendo dependencias de prueba y nunca se
guardan en `data/diagnostic/`.

## 10. Herramienta editorial

El comando independiente es:

```bash
./scripts/validate-diagnostic-bank.sh
```

Por defecto valida `data/diagnostic/`. También acepta un directorio:

```bash
./scripts/validate-diagnostic-bank.sh /ruta/al/banco
```

No modifica archivos. Devuelve código `0` cuando todo el conjunto valida y un
código distinto de cero ante el primer error estructural. En éxito imprime:

- número de bancos y tareas;
- estado editorial;
- ejes;
- `skill_id` usados;
- distribución de dificultad;
- tipos de tarea;
- readiness, tareas y ejes alcanzables, límites observados y problemas concretos.

Para un banco `draft`, una brecha de alcanzabilidad deja
`ready_for_review = false`, pero conserva código `0` si la estructura es válida.
La preparación puede exigirse explícitamente:

```bash
./scripts/validate-diagnostic-bank.sh --require-ready
./scripts/validate-diagnostic-bank.sh --require-ready /ruta/al/banco
```

Un banco `reviewed` o `production` no alcanzable devuelve código distinto de cero
incluso sin esa opción. El análisis ejecuta escenarios correctos, incorrectos,
parciales, con ayuda, mixtos y no evaluables. También explora de forma
determinista y memoizada las categorías del motor con un límite de estados. Si
alcanza ese límite, conserva los resultados parciales, informa
`exploration_inconclusive` y nunca declara readiness.

Una tarea permanentemente inalcanzable no fue seleccionada en ninguna trayectoria
de una exploración conclusiva. Si la exploración es inconclusa, el informe no
presenta como probada esa ausencia.

Los errores incluyen archivo y ubicación editorial, pero no se muestran por la
API HTTP.

## 11. Versionado y evolución

Las versiones tienen responsabilidades independientes:

1. `schema_version` cambia con la forma del manifiesto o archivo de tareas.
2. `bank_version` identifica una publicación editorial.
3. `diagnostic_version` agrupa candidatos compatibles con una sesión.
4. `task.version` cambia al alterar contenido o significado.
5. `rubric_version` cambia al alterar la evaluación.
6. `minimum_compatibility` impide cargar contenido para otro runtime.

Los cambios incompatibles requieren un schema nuevo. No se usará
`extra="ignore"` para simular compatibilidad. Audio y voz necesitarán otro
contrato; no se introducirán como metadata libre. Si una futura versión usa
referencias curriculares estables por código, deberá añadirlas explícitamente y
definir la transición desde `skill_id`.

Cambiar `axis`, `skill_id` —incluido pasar a o desde `null`—, dificultad,
respuesta correcta, rúbrica, scoring, objetivo pedagógico, `equivalence_group`,
modalidad, `task_type`, `response_type` o contenido visible que altere la
competencia evaluada requiere una nueva `task.version`. Los cambios de rúbrica o
scoring requieren además una nueva `rubric_version`; la publicación actualiza
`bank_version` según la convención existente.

Solo pueden conservar la versión los cambios editoriales que no alteren contenido
visible, selección, evaluación ni significado, por ejemplo correcciones en
`authoring_notes`, tags o notas del manifiesto. Una corrección de la instrucción
visible capaz de cambiar la respuesta no es editorial. Las sesiones históricas
conservan su versión: nunca se reinterpretan después de cambiar eje, habilidad,
rúbrica u objetivo.

Se mantiene el nombre `docs/content-format.md` porque ya es el punto de entrada
versionado y no existe una razón técnica para romper enlaces o duplicar la
documentación.

Los campos de fecha y la política privada de scoring se cerraron antes del
primer banco persistido. Por ello continúan bajo los contratos v1: no había
manifiestos previos que migrar y no cambia la proyección al motor ni al DTO
público.
