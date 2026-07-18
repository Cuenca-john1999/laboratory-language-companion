# Memoria pedagógica verificable

## Qué recuerda DeutschOS

La memoria pedagógica conserva hechos documentales pequeños y corregibles:
conceptos, alias, relaciones propuestas y ubicaciones en versiones concretas de
los materiales. No guarda una respuesta generada por Qwen como conocimiento
verdadero. La evidencia original continúa siendo obligatoria.

La persistencia forma parte de la SQLite de biblioteca, esquema 4. No toca la
base principal ni el progreso del alumno.

El upgrade 3→4 crea primero una copia consistente mediante la API de backup de
SQLite, ejecuta `quick_check` y la publica de forma atómica bajo
`var/educational-library/backups/library.sqlite3.schema3-<UTC>.bak`. La copia es
runtime local excluido de Git. La migración del esquema continúa siendo
transaccional y una biblioteca creada desde cero llega directamente a esquema 4.

## Confianza y estados

El orden de confianza es:

1. `user_confirmed`: el usuario confirmó la ubicación o el concepto.
2. `system_verified`: una comprobación determinista validó fuente vigente,
   chunk, cita y calidad mínima.
3. `candidate`: propuesta de sección, KnowledgeUnit candidata o consulta
   completada que todavía necesita revisión.

`rejected` es memoria negativa exacta. `conflict` conserva propuestas
incompatibles y `stale` identifica evidencia de una versión documental anterior.
Ninguno de esos tres estados se utiliza como verdad vigente.

Una confirmación de la explicación solo valora la respuesta. No confirma sus
fuentes. Una confirmación de ubicación sí puede promover ese enlace exacto a
`user_confirmed`. “No lo sé” crea una revisión neutral y mantiene el estado.

## Conceptos, alias y relaciones

Un concepto tiene ID estable, nombre canónico, idioma, nombres de presentación en
español/alemán, categoría flexible, descripción y estado. Los alias se normalizan
con Unicode NFKC, minúsculas semánticas y espacios; se conserva el texto original.
Una colisión entre alias de conceptos distintos produce conflicto, no fusión.

Las relaciones admitidas son `broader_than`, `narrower_than`, `related_to`,
`contrasts_with`, `prerequisite_of`, `example_of`, `used_in` y
`commonly_confused_with`. Las sugerencias automáticas empiezan como candidatas.

## Paginación y regiones

El mapa de página separa:

- `pdf_page_index`: índice interno, cero-based;
- `pdf_page_number`: página pública del PDF, uno-based;
- páginas impresas izquierda, derecha o completa como etiquetas de texto;
- layout `single_page`, `double_page`, `mixed` o `unknown`;
- rotación y versión del mapa;
- estado y procedencia de la revisión.

Las regiones son `full`, `left`, `right`, `both`, `custom` o `unknown`. Una región
personalizada usa coordenadas normalizadas dentro de la página; las citas
públicas nunca muestran esas coordenadas.

Ejemplos de proyección pública:

```text
Herder · PDF p. 35 · libro p. 62
Herder · PDF p. 89 · libro p. 175 · mitad derecha
Herder · PDF p. 89 · página impresa sin identificar
```

Nunca se calcula ni inventa una página impresa a partir del número del PDF.

## Teacher y recuperación

Antes de la recuperación híbrida, Teacher detecta alias presentes de forma
explícita en la consulta. Las ubicaciones vigentes y relevantes aportan una
prioridad acotada: confirmadas primero, verificadas después y candidatas como
apoyo. El fragmento original vuelve a cargarse y participa en el ranking normal.
Una ubicación confirmada irrelevante no entra en la consulta.

Después de una consulta `completed`, solo el concepto del plan y las ubicaciones
realmente citadas pueden convertirse en candidatos. La respuesta generada no se
importa. Consultas `insufficient`, fallidas, canceladas o expiradas no crean
conocimiento positivo. Una respuesta marcada incorrecta no se usa como contexto
en una continuación.

Caché y memoria no son lo mismo: la caché evita repetir planes o embeddings bajo
el mismo fingerprint; la memoria conserva decisiones editoriales y evidencia
versionada. Un cache hit no implica memoria confirmada y viceversa.

## Importación inicial

La importación explícita y auditable puede leer:

- secciones editoriales Herder como conceptos/ubicaciones candidatas;
- KnowledgeUnits aprobadas como `system_verified` solo si la cita aparece en un
  chunk actual y la página no es inutilizable;
- KnowledgeUnits candidatas como candidatas;
- objetivos de consultas completadas como candidatos.

No inventa páginas impresas ni regiones. El caso confirmado por el usuario de
`Akkusativ` en Herder PDF p. 89 se importa únicamente con el interruptor explícito
correspondiente. Registra layout de doble página, pero deja página impresa y
región como desconocidas hasta su revisión.

```bash
./scripts/educational-library.sh memory-status
./scripts/educational-library.sh memory-import \
  --operation-id verified-memory-import-v1 \
  --confirm-herder-akkusativ-pdf-89
```

La operación es idempotente. El historial conserva actor, acción, estado anterior
y nuevo, comentario, consulta relacionada y reversión.

## API local

La API se encuentra bajo `/api/library`:

- `GET /memory/status`
- `GET|POST /memory/concepts`
- `GET /memory/concepts/{concept_id}`
- `POST /memory/concepts/{concept_id}/aliases`
- `POST /memory/concepts/{concept_id}/relations`
- `GET|POST /memory/locations`
- `GET /memory/locations/{location_id}`
- `GET|PUT /memory/source-versions/{version_id}/pages/{pdf_page}`
- `POST /queries/{query_id}/feedback`
- `POST /queries/{query_id}/locations/{location_id}/feedback`
- `GET /queries/{query_id}/memory`
- `GET /memory/review-queue`
- `POST /memory/{target_type}/{target_id}/review`
- `POST /memory/reviews/{review_id}/revert`
- `GET /memory/audit`
- `POST /memory/import`

Los DTO no exponen hashes de contenido, rutas locales, prompts, el índice PDF
cero-based en citas ni claves privadas de evaluación.

## Invalidación y límites

Una versión nueva de fuente marca sus ubicaciones y mapas anteriores como
`stale`. Un renombrado con hash idéntico conserva la versión. Cambiar solo un
alias del concepto no invalida evidencia. Editar el mapa aumenta
`mapping_version`; la cita pública se recalcula desde el mapa vigente.

No hay OCR masivo, inferencia automática de regiones, proyección al Learning
Engine ni ontología generada en masa. La interfaz permite corregir izquierda,
derecha, ambas, layout y etiquetas impresas, pero no incluye todavía un editor
visual complejo de bounding boxes.
