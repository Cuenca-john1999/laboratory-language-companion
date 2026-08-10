# Biblioteca educativa local

La biblioteca convierte una carpeta de materiales de solo lectura en un catálogo
reconstruible con extracción, fragmentos, búsqueda y consultas educativas fundamentadas.
No realiza fine-tuning, no sube documentos y no escribe en el Learning Engine.

## Límites de datos

- Originales: `LLC_EDUCATIONAL_MATERIALS_DIR`, por defecto
  `./material educativo`.
- Runtime: `LLC_EDUCATIONAL_LIBRARY_RUNTIME_DIR`, por defecto
  `./var/educational-library`.
- Catálogo: `var/educational-library/library.sqlite3`.
- Informes: `var/educational-library/reports/`.

Las variables `DEUTSCHOS_EDUCATIONAL_*` equivalentes se aceptan como fallback
legacy; una variable `LLC_*` siempre tiene precedencia. No se mueve ni duplica
ningún runtime existente durante la migración de identidad.

`material educativo/` y `var/` están ignorados por Git. Los extractores solo
abren originales para lectura. PDF usa un temporal limitado dentro del runtime;
DOCX y EPUB se leen sin extraer miembros al disco y con límites contra archivos
comprimidos desproporcionados. No se siguen enlaces simbólicos ni se abren
macros o archivos comprimidos genéricos.

## Catálogo y migración propia

La base documental está separada de `data/deutschos.sqlite3`. Su migración
ordenada `library_schema` está actualmente en la versión 12 e incluye fuentes,
versiones, documentos, secciones, chunks, FTS5, embeddings opcionales, trabajos,
KnowledgeUnits, revisiones, borradores fundamentados, conversaciones y consultas
docentes. La versión 3 añade metadatos editoriales, índices de sección revisables,
calidad y variantes por página, provenance de embeddings y caché invalidable.
La versión 4 añade memoria pedagógica verificable: conceptos, alias, ubicaciones
versionadas, mapas PDF/impreso/región, feedback separado y auditoría. El contrato
completo se documenta en [pedagogical-memory.md](pedagogical-memory.md) y ADR 0010.
La versión 5 añade la ruta canónica Herder derivada del índice editorial:
importaciones versionadas, 51 temas, outline jerárquico, reconciliación no destructiva
de secciones legacy, variantes documentales y auditoría reversible. El diseño se
documenta en el [ADR 0012](adr/0012-canonical-herder-route.md).
La versión 6 separa la identidad estable de la fuente, la última versión
detectada y la única versión activa para recuperación. Registra ruta y nombre
observados, hash, tamaño, páginas, estados de disponibilidad/extracción/chunks/
embeddings/activación, procedencia técnica y relación con la versión anterior.
Un índice parcial impide que una fuente tenga dos versiones activas. La migración
incluye backfill conservador y rollback formal a la versión 5.
La versión 7 añade ejecuciones ligadas a versión y hash exactos, catálogo de
etapas, estados por página, incidencias, eventos y cobertura multidimensional.
La versión 8 incorpora artefactos regenerables, comparaciones 1:1, 1:N, N:1 y
N:M, correspondencias revisables y planes de transferencia que son únicamente
propuestas. La versión 9 añade bloques de página observados, candidatos
estructurales append-only, jerarquía propuesta y trazabilidad explícita entre
candidatas sustituidas. El rollback v9 elimina solo esas tablas y columna nuevas;
no reconstruye ni inventa candidatos históricos.
La versión 10 añade decisiones editoriales append-only, consolidación de temas,
jerarquía y relaciones, clasificación visual y readiness estructural. La versión
11 registra metadata de export/import y snapshots documentales; los paquetes
continúan fuera de SQLite. La versión 12 separa temas sin resolver, temas con
práctica directa y temas cuya identidad está resuelta por el índice pero para los
que el workbook declara explícitamente que no existe práctica directa. En este
último caso `primary_candidate_id` permanece nulo y el candidato de índice se
conserva únicamente como evidencia auditada.
Puede eliminarse y reconstruirse desde los originales; no contiene progreso del
alumno.

## Auditoría externa y cierre documental

El Laboratorio expone una sección **Auditoría** para las versiones documentales
candidatas. La vista mantiene separado el readiness estructural del readiness para
lectura IA, muestra bloqueadores, cola P1/P2, temas sin identidad y pendientes
visuales, y conserva un historial breve de snapshots inmutables.

Los paquetes `audit-package.v1` se generan fuera de Git en
`var/educational-library/exports/`. `full` contiene la representación completa;
`review_only` limita el alcance a incidencias y su contexto; `targeted` selecciona
temas, páginas, candidatos, relaciones o elementos de revisión. Antes de crear un
ZIP la API devuelve páginas, elementos, tamaño estimado y número de imágenes. Las
imágenes solo se derivan del PDF bajo petición explícita y no se interpretan con
Vision.

`manifest.json` liga el paquete a fuente, versión, hash del PDF, esquema de base,
pipelines y revisiones. Cada archivo tiene tamaño y SHA-256, además de un hash
lógico reproducible del contenido. Es una garantía de integridad, no de identidad
criptográfica del revisor. El PDF se comparte siempre por separado.

Un import `audit-decisions.v1` sigue obligatoriamente este flujo:

1. seleccionar JSON (sin escritura);
2. validar identidad, hashes, targets y estados esperados;
3. ejecutar dry-run;
4. revisar aplicables, stale, conflictos, inválidas y sin efecto;
5. confirmar Apply atómico.

Las decisiones aplicadas se añaden al ledger como `external_audit`; no borran
eventos previos. Solo se recalculan proyecciones editoriales y readiness. Los
snapshots fijan el estado documental, pero incluso `ready_for_ai` mantiene la
versión como candidata. Véase
[ADR 0016](adr/0016-document-audit-export-and-closure.md).

El inventario documental explícito calcula SHA-256 sin extraer texto. La misma
ruta con hash nuevo crea una `SourceVersion` candidata dentro de la misma fuente,
sin mover el puntero activo ni invalidar su recuperación. Un hash conocido y una
ubicación anterior ausente permiten reconocer un renombre; coincidencias múltiples
quedan separadas para revisión manual. Una ruta ausente queda `missing` sin borrar
historial, chunks o embeddings. La operación completa es transaccional e
idempotente. El escaneo automático al iniciar está desactivado por defecto.

`/laboratory` muestra métricas separadas, fuentes, historial de versiones y la
acción confirmada «Detectar cambios». La API correspondiente vive bajo
`/api/library/laboratory`: resumen, listado, detalle de fuente, versiones, detalle
de versión, ejecución de inventario y último resultado. Los GET no ejecutan
inventario. Esta fase no expone acciones de OCR, chunking, embeddings ni promoción.

La pestaña **Ejecuciones** añade procesamiento iterativo fijado a una
`source_version` y a su SHA-256. Una ejecución de alto nivel conserva
configuración, pipeline, linaje, etapas, intentos, eventos, resultados por página
y snapshots de cobertura; los `processing_jobs` siguen siendo tareas técnicas
hijas. Solo preflight y reconciliación de cobertura están habilitados. El resto
del catálogo de etapas aparece como «No ejecutado».

La cobertura se presenta por archivo, páginas, texto, calidad, estructura,
revisión, chunks, embeddings y mapeo canónico. Un denominador desconocido se
muestra como «Sin denominador» y una etapa futura como «No ejecutado». No se
almacena ni presenta un porcentaje global de comprensión. La API de ejecuciones
vive bajo `/api/library/laboratory/runs`; sus GET son de solo lectura y las
mutaciones (crear, preflight, reconciliar, pausar, reanudar, cancelar, reintentar
y crear una pasada desde pendientes) son explícitas.

## Fuentes nucleares y mapa Herder

`pedagogical_role` separa `core_theory`, `core_workbook`, `core_answer_key`,
`supplementary`, `reference`, `glossary`, `answer_key` y `unknown`. El nombre
físico no es identidad: IDs, hashes y versiones sobreviven a un renombrado, y
`display_alias` presenta un nombre editorial sin tocar el original. Una asignación
core exige `operation_id`, es idempotente, auditable y nunca se confirma
automáticamente si hay más de un candidato equivalente.

El manual y el workbook confirmados se relacionan en ambos sentidos. El índice
interno deriva sugerencias de capítulos o temas con página, método, confianza y
estado editorial. Son propuestas revisables: título, rango y tema pueden corregirse
y una sección teórica puede enlazarse con práctica. Una nueva `SourceVersion` no
reinterpreta el índice histórico.

La ruta Herder canónica no reutiliza las 53 secciones OCR como estructura visible.
Un índice de referencia validado aporta Tema 1–51, títulos bilingües, páginas impresas
y subapartados. La importación conserva por separado página del PDF de referencia,
página impresa del libro y página del PDF digital del manual. Los mappings legacy
son `exact`, `probable`, `ambiguous`, `unmatched` o `rejected`; solo `exact` puede
presentar datos personales existentes bajo el tema vigente, sin reescribir la base
principal. Una relación probable o ambigua permanece disponible para revisión.

Estados principales de proceso: `pending`, `processing`, `processed`, `partial`,
`needs_ocr`, `awaiting_transcriber`, `unsupported` y `error`. Los trabajos
persisten progreso y cursor; un trabajo que estaba `running` al reabrir el
proceso pasa a `interrupted` y puede reintentarse. Un error de archivo no aborta
los demás.

## Extracción y chunks

Soporte completo de esta primera versión:

- TXT, Markdown y HTML estructurado;
- PDF mediante `pdftotext`, preservando página y detectando baja densidad para
  `needs_ocr`;
- DOCX (párrafos, encabezados y texto de tablas) y EPUB (spine y capítulos);
- SRT y VTT con timestamps.

Audio y vídeo se catalogan y, cuando existe `ffprobe`, conservan sus metadatos.
Sin un transcriptor local configurado quedan honestamente
`awaiting_transcriber`. Las imágenes quedan `needs_ocr`; archivos comprimidos y
formatos heredados no se ejecutan ni extraen.

El chunker usa secciones, páginas, párrafos y oraciones antes de recurrir a una
ventana limitada. Conserva jerarquía, página o timestamp, rol de contenido y un
hash estable. Los roles de solución se excluyen de búsqueda y generación por
defecto.

## Búsqueda y memoria pedagógica

FTS5 con tokenización Unicode y ranking BM25 está siempre disponible. Los modos
`semantic` y `hybrid` usan `text-embedding-embeddinggemma-300m` mediante `EmbeddingProvider`; si
no está instalado o disponible, la respuesta declara `semantic_available=false`
y cae a léxico. Nunca se fabrican vectores de producción. Consulta y texto se
normalizan con NFC y espacios canónicos, sin traducirlos ni borrar signos.
EmbeddingGemma recibe consultas como `task: search result | query: …` y
documentos como `title: none | text: …`. Este formato se aplica únicamente al
texto enviado al modelo; los chunks originales permanecen intactos. La versión
registrada es `embeddinggemma-retrieval.v1`.

Cada vector registra chunk, versión de fuente, modelo, digest, dimensión, hash del
texto normalizado, versión de normalización, calidad, estado y error. Se excluyen
vacíos, índices, soluciones, duplicados confirmados y fuentes sin extracción. La
selección incremental se invalida solo por nueva versión, digest, normalización o
estado `stale`; un lote con dimensiones incompatibles se registra como fallido y
puede reintentarse. Los vectores viven únicamente en `var/`.

El híbrido aplica reciprocal rank fusion a BM25 y coseno. Después usa
multiplicadores conservadores de rol, prioridad y calidad: el manual Herder se
favorece entre resultados relevantes, pero una coincidencia complementaria mucho
mejor puede ganar. Las consultas gramaticales recuperan primero core y amplían a
fuentes complementarias si faltan diversidad o evidencia. Las búsquedas explícitas
de ubicación Herder no mezclan KnowledgeUnits o fuentes ajenas. Soluciones y answer
keys reciben penalización y nunca son base por defecto.

Qwen puede derivar KnowledgeUnits estructuradas con estados `candidate`,
`needs_review`, `approved`, `rejected`, `conflict` y `stale`. Cada cita se
verifica como substring literal del chunk declarado. Una cita no verificable se
repara una vez; si aún quedan citas inválidas se descartan, se limita la
confianza y la unidad requiere revisión. Una unidad sin ninguna cita verificable
no se persiste. Las unidades aprobadas tienen prioridad, pero ninguna se
incorpora automáticamente al currículo.

La generación fundamentada recupera chunks sin soluciones y conocimiento
revisable, exige IDs de fuente en cada afirmación central y persiste un borrador
con versión de modelo/prompt. Si las fuentes no están aprobadas añade una
advertencia y limita la confianza. Los ejemplos y ejercicios son síntesis
originales; la API devuelve fragmentos breves y no capítulos.

## Preguntar a la biblioteca

`EducationalTeacherService` es el único pipeline de consulta educativa. Reglas
deterministas crean planes seguros para consultas frecuentes (`die`, acusativo,
`kein/nicht`, pronombres, Konjunktiv II y otras); los demás planes usan
`google/gemma-4-12b-qat`. El plan estricto `library-query-plan.v1` contiene intención,
ambigüedad y hasta seis consultas locales. El servicio valida el plan, ejecuta la recuperación
híbrida con fallback léxico, combina KnowledgeUnits con chunks originales, baja
la prioridad de solucionarios y deduplica por hash, grupo de duplicados y
similitud. Se seleccionan como máximo cinco fuentes, diez chunks, dos chunks por
fuente y 18 000 caracteres de contexto; todos los límites son configurables.

El prompt `library-teacher-answer.v1` explica en español, genera ejemplos breves
en alemán y exige que cada afirmación central cite IDs del paquete recuperado.
La API valida que las citas existan, que los puntos esenciales tengan soporte,
que no se copien pasajes largos y que no aparezcan rutas, prompts, jerga del
ranking ni evasiones de grounding. Existe una sola reparación controlada con
`library-teacher-answer-repair.v1`; un segundo fallo produce una respuesta segura
de evidencia insuficiente. Qwen no completa silenciosamente la respuesta con
conocimiento externo.

### Contrato docente y enrutamiento

Todos los modos generativos reutilizan `gemma-teacher.v1`. El contrato fija español
de España, adaptación solo al nivel respaldado, corrección que acepta variantes
válidas, ejercicios sin solución prematura y ejemplos de alemán general y de
laboratorio. El modo biblioteca añade exclusivamente su contrato estructurado:
Herder `core_theory` manda en teoría, `core_workbook` en práctica y las fuentes
complementarias solo apoyan o contrastan. Una página, ubicación o cita se presenta
únicamente si fue recuperada y verificada; PDF e impresa se distinguen.

El modelo habitual atiende explicaciones, vocabulario, ejercicios y correcciones
breves con evidencia clara. El profundo se selecciona por una razón registrada
internamente: gramática compleja, conflicto o memoria rechazada, comparación extensa
o contexto largo que además requiera varios pasos. La longitud aislada no basta.
Cada rol usa exactamente su modelo configurado: un fallo se comunica y no activa
otro modelo.

Si la evidencia documental no basta, Teacher Query lo indica, no completa la
respuesta con conocimiento general atribuido a Herder y propone reformular o elegir
una fuente. La conversación general puede dar una explicación general, pero debe
identificarla como tal. Visión queda reservada a una imagen o página concreta cuando
texto y memoria verificada no basten; no inicia OCR masivo.

### Latencia, caché y fallos

Los nombres de modelo viven en configuración, no dispersos en el pipeline:

- planner y reparación estructural: `google/gemma-4-12b-qat`;
- embeddings: `text-embedding-embeddinggemma-300m`;
- docente ordinario: `google/gemma-4-12b-qat`;
- explicaciones profundas justificadas: `google/gemma-4-26b-a4b-qat`;
- visión selectiva: `google/gemma-4-12b-qat`.

Se comprueba capacidad antes de usar cada rol, existe un timeout por rol,
`keep_alive` es limitado y un lock impide generar en paralelo con varios modelos
grandes. No se descargan modelos ni existe una cadena de fallback.

Para futuras calibraciones se actualiza primero el contrato central o el apéndice
mínimo del modo, se añaden pruebas de propiedades (no snapshots de texto completo)
y se realizan pocas consultas reales representativas. No se cambian identificadores
de modelo, embeddings ni datos de progreso durante una calibración pedagógica.

Los fallos conocidos se persisten y proyectan como `no_evidence`, `weak_evidence`,
`retrieval_failure`, `model_unavailable`, `generation_failure`,
`citation_validation_failure`, `repair_failure`, `cancelled` o `timeout`. La API
usa mensajes sanitizados y nunca devuelve prompts, rutas, trazas o scoring privado.
Los timings separan planificación, FTS, embedding de consulta, vectorial, ranking,
generación, validación, reparación y total.

Planes y embeddings de consulta se cachean en SQLite con TTL. La clave incorpora
consulta o conversación, huella de fuentes activas, modelos, digest,
normalización, prompt y configuración. Cambiar una fuente core cambia la huella y
evita reutilizar resultados obsoletos.

`POST /api/library/ask/stream` emite SSE de progreso (`accepted`, `planning` y
`verified` o `error`) y persiste cancelaciones. La respuesta pública solo aparece
como verificada después de validar citas. Esta versión mejora la espera visible,
pero aún no emite tokens provisionales: entrega el texto final de una vez para no
mostrar contenido no validado.

La confianza describe la calidad de la evidencia, no una probabilidad
calibrada. El score interno, acotado a `[0,1]`, es:

```text
0.18
+ diversidad de fuentes (0.12 una, 0.28 dos, 0.35 tres o más)
+ 0.22 × calidad media de extracción
+ 0.12 × confianza editorial media
+ 0.08 × proporción de teoría o glosario
+ 0.12 si existe KnowledgeUnit aprobada
- 0.25 si toda la evidencia son soluciones
- 0.12 × proporción de extracciones con calidad < 0.35
- 0.20 si existe conflicto de conocimiento
```

Se etiqueta `sólida` desde 0.75 con dos fuentes independientes, `moderada`
desde 0.50, `limitada` desde 0.28 e `insuficiente` por debajo. La ausencia de
revisión limita `sólida` a `moderada`; conflicto o baja calidad limitan `sólida`
y `moderada` a `limitada`, sin convertir evidencia insuficiente en evidencia
positiva.

La consulta, el plan validado, la respuesta interna, provenance, versiones de
prompt, modelo, timings, warnings y relación conversacional se guardan en la
SQLite reconstruible. Las continuaciones solo envían la pregunta y respuesta
directa anteriores, vuelven a planificar y recuperan evidencia nueva. El DTO
público omite planes, claims internos, chunk IDs, rutas y reglas de ranking. El
contexto pedagógico lee perfil, preferencias y categorías recientes de error de
la base principal, pero nunca escribe `StudentSkill`, `SkillEvidence` ni otro
progreso.

### Consultar dónde aparece un tema

El mismo `POST /api/library/ask` acepta consultas de localización y persiste el
turno en la conversación ordinaria. La respuesta declara `answer_kind` como
`source_lookup` o `teacher_answer_with_source_lookup` y añade un
`source_lookup` estructurado con concepto, estado, hasta tres ubicaciones
iniciales, página PDF, página impresa, layout, región, estado de revisión, cita,
provenance y fragmento breve. Una continuación `expand` puede mostrar hasta diez
ubicaciones y ejecutar recuperación híbrida adicional.

La detección local separa localización pura, explicación y petición mixta. Una
ubicación confirmada se resuelve desde SQLite sin planner ni docente. El DTO
indica por separado `used_generation`, `memory_hit`, `lookup_cache_hit` y
`hybrid_fallback`; `answer_cache_hit` permanece falso mientras no exista una
caché de respuestas. Los timings añaden detección, lookup de memoria y fallback.
No se exponen paths, hashes, prompts, chunks completos ni razones privadas de
ranking.

En peticiones mixtas, los claims pedagógicos siguen pasando por generación y
validación normal, pero la cita de ubicación se reconstruye de forma
determinista. Si el docente local no está disponible o falla la reparación, el
turno conserva la tarjeta de ubicación y un `failure_reason` específico; no se
degrada falsamente a `no_evidence`.

## Operación

```bash
./scripts/educational-library.sh status
./scripts/educational-library.sh scan --metadata-only
./scripts/educational-library.sh scan
./scripts/educational-library.sh search "Akkusativ" --mode lexical
./scripts/educational-library.sh core
./scripts/educational-library.sh assign-core --apply-unambiguous
./scripts/educational-library.sh sections SOURCE_ID --build
./scripts/educational-library.sh index-semantic --batch-size 32
./scripts/educational-library.sh page-quality SOURCE_ID
./scripts/educational-library.sh reprocess-page SOURCE_ID 10 --method vision
./scripts/educational-library.sh ask "¿Qué significa die?"
./scripts/educational-library.sh derive "Nominativ Akkusativ" --model google/gemma-4-12b-qat
./scripts/educational-library.sh generate "Nominativ Akkusativ" \
  --objective micro_lesson --level A1 --model google/gemma-4-12b-qat
./scripts/educational-library.sh integrity
./scripts/educational-library.sh import-herder-index /ruta/Herder_Index.pdf \
  --canonical-json /ruta/Herder_Index_canonical.json
./scripts/educational-library.sh canonical-route
./scripts/educational-library.sh canonical-route-mappings --status probable
./scripts/educational-library.sh canonical-route-reconcile \
  --operation-id canonical-route-reconcile-v1
```

FastAPI inicia un polling incremental no bloqueante si
`LLC_EDUCATIONAL_LIBRARY_SCAN_ON_STARTUP=true`; el intervalo mínimo es 60
segundos y se configura con
`LLC_EDUCATIONAL_LIBRARY_SCAN_INTERVAL_SECONDS`. No instala un daemon.

La sección web `/library` empieza por **Pregunta a tu biblioteca**: pregunta
natural, progreso comprensible, explicación, ejemplos, confianza, continuaciones
y un historial local. Las fuentes y detalles de evidencia están plegados. La
búsqueda FTS5, inventario, catálogo y controles editoriales permanecen en
**Herramientas avanzadas** y no dominan la experiencia. No hay selector de
modelo, nivel o estrategia de recuperación en la consulta principal. El
controlador macOS muestra disponibilidad, ruta y
cantidad catalogada mediante campos aditivos de `deutschos-status-v1`.

Las localizaciones usan una tarjeta **Dónde aparece** separada de la explicación,
con estado de confianza documental, PDF/libro, layout y región. Desde ella se
puede abrir la fuente, confirmar/rechazar, indicar “No lo sé”, calibrar doble
página o mitad, editar la página impresa, ampliar ubicaciones y pedir una
explicación. La cola de revisión comienza en diez elementos y ofrece filtros por
Herder, uso reciente, tipo y estado.

El panel **Fuentes principales** muestra manual y workbook, confirmación, alias,
relación, calidad de extracción y estado semántico. Los detalles de respuesta
distinguen base core y apoyo complementario, modelo, caché y tiempos. Las
herramientas avanzadas permiten construir o revisar secciones, evaluar páginas,
reprocesar selectivamente y elegir una variante sin sobrescribir extracción ni
originales.

## Extracción estructural observada

La sección **Extracción** de `/laboratory` ejecuta exclusivamente estas etapas:
`pdf-preflight.v1`, `page-materialization.v1`,
`embedded-text-extraction.v1`, `layout-analysis.v1`,
`structure-candidate-extraction.v1`, `coverage-reconciliation.v1` y
`version-comparison.v1`. El motor es Poppler. Lee la capa de texto ya incrustada,
conserva palabras, líneas, bloques, coordenadas, geometría, orden de lectura y
regiones visuales observadas, y escribe miniaturas regenerables bajo el runtime
con una clave de hash y página.

Los candidatos distinguen texto observado de normalización conservadora de
layout. Reglas deterministas pueden proponer títulos, temas, niveles, apartados,
ejemplos, tablas, ejercicios y soluciones, pero nunca corrigen el OCR como verdad,
resumen, traducen ni publican conceptos pedagógicos. Los estados son `proposed`,
`auto_supported`, `needs_review`, `rejected_by_rule` y `superseded`. Repetir
páginas crea una pasada hija y conserva la evidencia anterior.

Las mutaciones de cada etapa son `POST` explícitos bajo
`/api/library/laboratory/runs/{run_id}`. Bloques, candidatos paginados, jerarquía,
cobertura, incidencias, eventos y miniaturas son lecturas separadas. No existen
acciones de OCR, LLM, chunking, embeddings, confirmación editorial, publicación o
activación dentro de este pipeline.

## Revisión y consolidación estructural

La sección **Revisión** de `/laboratory` consume exclusivamente los candidatos
observados por la extracción estructural. `document-review.v1` separa el estado
editorial efectivo del candidato original, registra cada decisión de manera
append-only y materializa una vista consolidada reversible: una identidad
principal por Tema 1–51, jerarquía enlazada a candidatos, relaciones prudentes
entre ejercicios y soluciones, clasificación técnica de páginas sin texto y un
snapshot de readiness. No modifica OCR, contenido observado, chunks, embeddings
ni activación.

Las reglas excluyen índices y cabeceras repetidas como identidades principales,
superseden duplicados exactos sin borrarlos y elevan ambigüedades a P0/P1/P2.
Los lotes solo se proponen para patrones homogéneos, muestran muestra, páginas y
efecto estimado, requieren aplicación explícita y admiten reversión lógica. No
existe “confirmar todo”. Las páginas sin texto se clasifican únicamente como
`image_only` o `unknown`; su contenido no se describe sin revisión visual.

## API

- `GET /api/library/status`, `GET /api/library/inventory`
- `GET /api/library/models/roles`, `GET /api/library/core`
- asignación core, secciones y enlaces editoriales bajo `/sources` y `/sections`
- calidad, variantes, reproceso y revisión bajo `/sources/{id}/pages`
- `POST /api/library/semantic/index`
- `POST /api/library/scan`, `POST /api/library/process`
- `GET /api/library/jobs`, acciones `cancel`, `pause` y `retry`
- `GET/PUT /api/library/sources`, versiones, chunks, excluir y reprocesar
- `GET /api/library/search?query=...&mode=lexical|semantic|hybrid`
- `POST /api/library/ask`, `POST /api/library/ask/stream`,
  `GET /api/library/queries/{query_id}`
- `GET/DELETE /api/library/conversations`, lectura de sus consultas
- `GET/POST /api/library/knowledge`, revisión de unidades
- estado, temas, búsqueda, mappings y auditoría bajo `/canonical-route`
- revisión de tema en `/canonical-route/topics/{theme_number}/review` y reversión
  en `/canonical-route/revert`
- `POST /api/library/grounded/generate`, `GET /api/library/grounded/{id}`
- creación de extracción bajo `/laboratory/extraction/runs`; ejecución explícita
  de preflight, páginas, texto, layout, candidatos, cobertura y comparación bajo
  `/laboratory/runs/{run_id}`
- bloques paginables por ejecución/página y candidatos filtrables bajo
  `/laboratory/extraction/candidates`
- jerarquía y miniaturas bajo `/laboratory/extraction/versions/{version_id}`
- resumen, cola paginada, decisiones, lotes y eventos bajo `/laboratory/review`;
  temas, jerarquía consolidada, relaciones y readiness bajo
  `/laboratory/review/versions/{version_id}`

Las rutas no aceptan paths del filesystem, paginan resultados, sanitizan errores
y no exponen documentos completos. El trabajo pesado se ejecuta fuera del event
loop mediante tareas controladas.

## Inteligencia documental y limitaciones

`page_quality` conserva longitud y densidad, idioma, reemplazos, caracteres
extraños, líneas repetidas, orden, columnas, tablas, alemán posiblemente dañado,
calidad `good|acceptable|poor|unusable`, warnings y revisión. El pipeline prefiere
texto nativo o pdftotext, después OCR local disponible, visión selectiva y revisión
humana. Cada variante registra método, versión, hash, calidad y provenance;
seleccionarla no borra ni reescribe la anterior.

En el entorno validado existen `pdftotext`, `pdftoppm` y `google/gemma-4-12b-qat`; no existen
Tesseract ni OCRmyPDF, por lo que OCR devuelve 503 en lugar de fingir capacidad.
Una única página difícil del workbook se transcribió con visión a 120 dpi,
razonamiento desactivado, salida limitada e imagen temporal eliminada. No hay OCR
visual masivo, transcripción automática ni extracción de subtítulos internos de
vídeo. No se descargan modelos de embeddings o Whisper.
La detección de idioma, CEFR y temas es heurística y editable. Los conflictos
semánticos se señalan de forma conservadora, pero todavía requieren revisión
humana. El polling de proceso es suficiente para la aplicación abierta; no es un
servicio del sistema operativo. Las consultas requieren el Qwen local
configurado; catálogo, búsqueda y lectura del historial siguen disponibles si
el proveedor no responde. Consulta educativa y ejercicios rápidos son productos
separados: esta experiencia no genera, puntúa ni registra ejercicios.
