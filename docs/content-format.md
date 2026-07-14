# Formato de contenido pedagógico

## 1. Objetivo y alcance

DeutschOS reserva una estructura local y versionable para contenido pedagógico:

```text
data/
├── curriculum/
├── diagnostic/
├── lessons/
└── review/
```

`#006E0` implementa únicamente el contrato JSON y el cargador de
`data/diagnostic/`. Los otros directorios son puntos de extensión; todavía no
tienen esquemas ni contenido. No hay preguntas de producción en el repositorio.

Se eligió JSON porque el proyecto ya lo usa en persistencia y contratos, Python
lo puede leer sin dependencias nuevas y Pydantic puede generar JSON Schema a
partir del mismo modelo que valida la ejecución.

## 2. Archivo de banco diagnóstico

Cada archivo `*.json` situado directamente en `data/diagnostic/` representa una
parte del banco. Los subdirectorios no se recorren. El documento raíz usa este
contrato:

```json
{
  "schema_version": "diagnostic-task-bank.v1",
  "diagnostic_version": "diagnostic-text.v1",
  "bank_version": "<release-del-archivo>",
  "tasks": []
}
```

Un banco vacío es válido como documento, pero no habilita el diagnóstico. Si el
conjunto completo no contiene tareas, la API continúa devolviendo `503` con el
mismo mensaje que antes de existir el cargador.

## 3. Contrato de tarea

Cada elemento futuro de `tasks` deberá contener al menos:

| Campo | Tipo | Regla |
| --- | --- | --- |
| `id` | string | Identidad estable, minúsculas, números, punto, guion o `_` |
| `version` | string | Versión inmutable de esa tarea |
| `axis` | enum | Eje diagnóstico existente |
| `skill_id` | integer o `null` | Dimensión curricular opcional, pero el campo es obligatorio |
| `task_type` | enum | Tipo de tarea admitido por el motor |
| `difficulty` | integer | Entero entre 1 y 5 |
| `prompt` | string | Texto visible, no vacío |
| `expected_response_type` | enum | `single_choice`, `short_text`, `ordered_tokens` o `free_text` |
| `rubric_version` | string | Versión del contrato evaluador |
| `metadata` | objeto | Metadatos estrictos de selección y trazabilidad |
| `estimated_seconds` | integer | Entre 5 y 600 segundos |

El formato v1 añade campos necesarios para materializar un `TaskCandidate`:

- `secondary_axes`: ejes secundarios, vacío por defecto;
- `options`: opciones visibles; solo se permiten para `single_choice`;
- `rubric`: definición privada de evaluación determinista.

El siguiente fragmento muestra solo la forma, no una pregunta ni una respuesta
pedagógica real:

```json
{
  "id": "<identificador-estable>",
  "version": "<version-de-tarea>",
  "axis": "<eje-diagnostico>",
  "secondary_axes": [],
  "skill_id": null,
  "task_type": "<tipo-de-tarea>",
  "difficulty": 1,
  "prompt": "<contenido-pedagogico-pendiente>",
  "expected_response_type": "<tipo-de-respuesta>",
  "rubric_version": "<version-de-rubrica>",
  "metadata": {
    "equivalence_key": null,
    "prerequisite_task_ids": [],
    "ambiguity_risk": "low",
    "tags": []
  },
  "estimated_seconds": 30,
  "options": [],
  "rubric": {
    "strategy": "manual_only",
    "accepted_responses": [],
    "partial_responses": [],
    "expected_tokens": [],
    "case_sensitive": false
  }
}
```

Este ejemplo estructural no debe copiarse como contenido de producción. Además,
la combinación mostrada solo sería válida con
`expected_response_type = free_text`; el validador rechaza combinaciones
incoherentes.

## 4. Rúbrica y separación público/privado

Las estrategias v1 coinciden con el motor determinista:

- `exact_match`;
- `accepted_answers`;
- `ordered_tokens`;
- `manual_only`, reservado para compatibilidad futura con texto libre.

Las claves correctas viven únicamente en `rubric`. El adaptador las transforma
en `expected_answer` y `DeterministicRubric` internos. La respuesta HTTP se
construye campo por campo y solo copia `prompt` y `options`; nunca expone la
rúbrica, respuestas aceptadas, equivalencias, prerrequisitos o razón de
selección.

Una tarea `manual_only` puede representarse para compatibilidad futura, pero el
selector determinista actual no la presenta como autoevaluable. No implica que
exista ya un evaluador LLM.

## 5. Versionado

Existen cuatro referencias con responsabilidades distintas:

1. `schema_version` cambia solo cuando cambia la forma del JSON. Un cargador v1
   rechaza otra versión en vez de adivinarla.
2. `diagnostic_version` agrupa las tareas compatibles con una sesión. El
   proveedor nunca mezcla versiones.
3. `bank_version` identifica la publicación del archivo para revisión y Git.
4. `task.version` cambia cuando se altera prompt, opciones, respuesta o
   significado. Un `id` mantiene su identidad y no puede aparecer dos veces en
   la misma versión diagnóstica.

`rubric_version` versiona de forma independiente la semántica de evaluación. El
cargador la conserva dentro del snapshot privado de respuesta esperada.

Los cambios incompatibles requieren un nuevo `schema_version` y una ruta de
migración explícita. No se relaja el modelo v1 con campos desconocidos.

## 6. Validación y carga

Los modelos viven en
`deutschos_api.content.diagnostic.DiagnosticTaskBank` y
`DiagnosticTaskDefinition`. Todos heredan el comportamiento estricto de
`APIModel`:

- rechazan campos extra y campos obligatorios ausentes;
- validan enums, identificadores y límites;
- comprueban la compatibilidad entre tipo de respuesta y rúbrica;
- impiden opciones duplicadas, auto-prerrequisitos y metadatos duplicados;
- rechazan IDs repetidos entre archivos de una misma versión;
- exigen que los prerrequisitos existan dentro de esa versión diagnóstica;
- limitan cada archivo a 2 MiB;
- leen UTF-8 y ordenan archivos y candidatos de forma determinista.

`FilesystemCandidateProvider.from_directory()` valida el conjunto completo antes
de ofrecer ningún candidato. Un archivo malformado hace que la dependencia HTTP
devuelva un `503` sanitizado; no se cargan parcialmente los demás archivos ni se
incluye la ruta interna en la respuesta.

El modelo Pydantic es la fuente de verdad y permite obtener JSON Schema mediante
`DiagnosticTaskBank.model_json_schema()`. Si en el futuro se publica un archivo
de schema, deberá generarse o comprobarse contra ese modelo para evitar dos
contratos divergentes.

## 7. Integración y compatibilidad futura

FastAPI resuelve el proveedor desde la raíz del repositorio, no desde el
directorio de ejecución. Esto mantiene la ruta válida cuando DeutschOS vive en
el SSD externo. El proveedor implementa el protocolo `CandidateProvider` y no
modifica el selector, scoring, agregación ni persistencia.

Compatibilidad prevista:

- nuevos bancos JSON pueden repartirse en varios archivos sin cambiar el
  proveedor;
- una versión futura puede añadir localización del prompt mediante un schema
  nuevo;
- audio y voz necesitarán campos y validadores propios, no metadatos libres en
  v1;
- una referencia estable por código curricular podría complementar
  `skill_id` en otro schema, pero no se infiere ahora;
- currículo, lecciones y repaso necesitarán contratos independientes antes de
  aceptar archivos en sus directorios.

El formato no habilita frontend, LLM, audio, Learning Engine ni proyección a
`StudentSkill` o `SkillEvidence`.
