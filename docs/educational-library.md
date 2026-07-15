# Biblioteca educativa local

La biblioteca convierte una carpeta de materiales de solo lectura en un catálogo
reconstruible con extracción, fragmentos, búsqueda y consultas educativas fundamentadas.
No realiza fine-tuning, no sube documentos y no escribe en el Learning Engine.

## Límites de datos

- Originales: `DEUTSCHOS_EDUCATIONAL_MATERIALS_DIR`, por defecto
  `./material educativo`.
- Runtime: `DEUTSCHOS_EDUCATIONAL_LIBRARY_RUNTIME_DIR`, por defecto
  `./var/educational-library`.
- Catálogo: `var/educational-library/library.sqlite3`.
- Informes: `var/educational-library/reports/`.

`material educativo/` y `var/` están ignorados por Git. Los extractores solo
abren originales para lectura. PDF usa un temporal limitado dentro del runtime;
DOCX y EPUB se leen sin extraer miembros al disco y con límites contra archivos
comprimidos desproporcionados. No se siguen enlaces simbólicos ni se abren
macros o archivos comprimidos genéricos.

## Catálogo y migración propia

La base documental está separada de `data/deutschos.sqlite3`. Su migración
ordenada `library_schema` está actualmente en la versión 2 e incluye fuentes,
versiones, documentos, secciones, chunks, FTS5, embeddings opcionales, trabajos,
KnowledgeUnits, revisiones, borradores fundamentados, conversaciones y consultas
docentes. Puede eliminarse y
reconstruirse desde los originales; no contiene progreso del alumno.

Un escaneo compara ruta, tamaño y `mtime`; solo calcula SHA-256 para entradas
nuevas o potencialmente modificadas. Un contenido nuevo crea `SourceVersion`,
un hash conocido permite reconocer renombres y una ruta ausente queda `missing`.
Los duplicados se confirman por hash. Al cambiar una versión, las unidades que
dependen de la anterior quedan `stale`. Un lock de proceso evita escaneos
simultáneos y no queda bloqueado tras una caída.

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
`semantic` y `hybrid` usan `EmbeddingProvider`; si no hay un modelo de embeddings
local configurado, la respuesta declara `semantic_available=false` y cae a
léxico. Nunca se fabrican vectores de producción. El modo híbrido usa reciprocal
rank fusion.

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

`EducationalTeacherService` es el único pipeline de consulta educativa. Qwen crea
primero un plan estricto `library-query-plan.v1`: intención, ambigüedad y hasta
seis consultas locales. El servicio valida el plan, ejecuta la recuperación
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

## Operación

```bash
./scripts/educational-library.sh status
./scripts/educational-library.sh scan --metadata-only
./scripts/educational-library.sh scan
./scripts/educational-library.sh search "Akkusativ" --mode lexical
./scripts/educational-library.sh derive "Nominativ Akkusativ" --model qwen3:14b
./scripts/educational-library.sh generate "Nominativ Akkusativ" \
  --objective micro_lesson --level A1 --model qwen3:14b
./scripts/educational-library.sh integrity
```

FastAPI inicia un polling incremental no bloqueante si
`DEUTSCHOS_EDUCATIONAL_LIBRARY_SCAN_ON_STARTUP=true`; el intervalo mínimo es 60
segundos y se configura con
`DEUTSCHOS_EDUCATIONAL_LIBRARY_SCAN_INTERVAL_SECONDS`. No instala un daemon.

La sección web `/library` empieza por **Pregunta a tu biblioteca**: pregunta
natural, progreso comprensible, explicación, ejemplos, confianza, continuaciones
y un historial local. Las fuentes y detalles de evidencia están plegados. La
búsqueda FTS5, inventario, catálogo y controles editoriales permanecen en
**Herramientas avanzadas** y no dominan la experiencia. No hay selector de
modelo, nivel o estrategia de recuperación en la consulta principal. El
controlador macOS muestra disponibilidad, ruta y
cantidad catalogada mediante campos aditivos de `deutschos-status-v1`.

## API

- `GET /api/library/status`, `GET /api/library/inventory`
- `POST /api/library/scan`, `POST /api/library/process`
- `GET /api/library/jobs`, acciones `cancel`, `pause` y `retry`
- `GET/PUT /api/library/sources`, versiones, chunks, excluir y reprocesar
- `GET /api/library/search?query=...&mode=lexical|semantic|hybrid`
- `POST /api/library/ask`, `GET /api/library/queries/{query_id}`
- `GET/DELETE /api/library/conversations`, lectura de sus consultas
- `GET/POST /api/library/knowledge`, revisión de unidades
- `POST /api/library/grounded/generate`, `GET /api/library/grounded/{id}`

Las rutas no aceptan paths del filesystem, paginan resultados, sanitizan errores
y no exponen documentos completos. El trabajo pesado se ejecuta fuera del event
loop mediante tareas controladas.

## Limitaciones de la primera versión

No hay OCR masivo, transcripción automática, análisis visual ni extracción de
subtítulos internos de vídeo. No se descargan modelos de embeddings o Whisper.
La detección de idioma, CEFR y temas es heurística y editable. Los conflictos
semánticos se señalan de forma conservadora, pero todavía requieren revisión
humana. El polling de proceso es suficiente para la aplicación abierta; no es un
servicio del sistema operativo. Las consultas requieren el Qwen local
configurado; catálogo, búsqueda y lectura del historial siguen disponibles si
el proveedor no responde. Consulta educativa y ejercicios rápidos son productos
separados: esta experiencia no genera, puntúa ni registra ejercicios.
