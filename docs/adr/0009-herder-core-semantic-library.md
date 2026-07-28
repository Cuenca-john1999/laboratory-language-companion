# ADR 0009: Núcleo Herder y recuperación semántica local

- Estado: aceptado
- Fecha: 2026-07-18

## Contexto

La biblioteca educativa ya catalogaba materiales locales, extraía texto y ofrecía
FTS5. Sin embargo, una consulta docente no podía distinguir las obras troncales de
las fuentes complementarias, las preguntas en español dependían demasiado de la
coincidencia literal con texto alemán y todos los trabajos Qwen compartían un
único modelo. Los dos volúmenes Herder elegidos por el usuario deben ser la base
pedagógica sin convertir el nombre físico del archivo en identidad ni excluir
evidencia complementaria más relevante.

## Decisión

### Identidad y rol editorial

La identidad estable de una fuente sigue siendo su UUID, hash y versión. Un alias
visible no renombra el original. `pedagogical_role`, prioridad, origen de los
metadatos y estado editorial se persisten y auditan. Asignar o cambiar una fuente
core requiere una operación explícita e idempotente; una sugerencia ambigua nunca
se confirma sola. El manual y el workbook mantienen una relación bidireccional,
pero el workbook no reemplaza la teoría.

### Recuperación híbrida

FTS5 continúa siendo el fallback siempre disponible. Los embeddings locales usan
`text-embedding-embeddinggemma-300m` y registran modelo, digest, dimensión, normalización, hash
del texto y versión de fuente. Se indexan incrementalmente y se invalidan por
provenance, no por fecha global.

El modo híbrido fusiona los rankings lexical y vectorial mediante reciprocal rank
fusion. Después aplica factores acotados de rol, prioridad y calidad documental.
La relevancia continúa dominando: ser core no convierte una coincidencia débil en
la mejor respuesta. Soluciones, índices, vacíos y duplicados confirmados no forman
la evidencia ordinaria.

Las consultas gramaticales buscan primero teoría core y amplían a fuentes
complementarias cuando falta soporte o diversidad. Las consultas explícitas de
ubicación Herder permanecen limitadas al core para evitar atribuciones falsas.

### Modelos por función

Los modelos se eligen por rol y capacidad comprobada. `google/gemma-4-12b-qat` planifica y
repara estructuras; `google/gemma-4-12b-qat` responde consultas ordinarias; `google/gemma-4-26b-a4b-qat` se
reserva para Konjunktiv II, declinación adjetival y fallback profundo;
`google/gemma-4-12b-qat` solo inspecciona páginas seleccionadas. Un lock impide generaciones
concurrentes de modelos grandes. Cada rol tiene timeout, fallback acotado y
`keep_alive` finito. La política se basa en el benchmark local documentado, no en
el tamaño nominal del modelo.

### Calidad y variantes documentales

La calidad se evalúa por página. Una reextracción crea una variante con método,
modelo, versión, hash, métricas y provenance; no modifica el original ni
reinterpretará automáticamente la extracción activa. Visión y OCR son operaciones
selectivas y revisables. Si una capacidad local no está instalada, el sistema
devuelve una indisponibilidad explícita.

### Caché, errores y salida pública

Planes y embeddings de consulta usan caché local con TTL y una huella de fuentes,
modelos, prompts y configuración. La salida docente se muestra solo después de
validar sus citas. El SSE actual comunica progreso real, pero no publica tokens
provisionales. Los fallos distinguen evidencia ausente o débil, recuperación,
modelo, generación, validación, reparación, timeout y cancelación, sin exponer
prompts, paths o trazas.

## Consecuencias

### Positivas

- Herder queda priorizado de forma persistente, revisable y resistente a
  renombrados.
- Las preguntas españolas pueden recuperar texto alemán sin servicios externos.
- El índice es reconstruible, incremental y auditable por versión de modelo.
- Los modelos grandes se usan solo donde el benchmark justifica su coste.
- Una página difícil puede mejorarse sin alterar el material ni perder historial.

### Costes y limitaciones

- Los vectores JSON aumentan el tamaño de la SQLite reconstruible.
- La visión local es lenta y no sustituye un OCR masivo.
- Los títulos de sección detectados automáticamente necesitan revisión editorial.
- El progreso SSE no reduce el tiempo de inferencia ni muestra texto antes de
  verificarlo.
- El benchmark mide este equipo, estos prompts y diez preguntas; debe repetirse
  cuando cambien modelos, cuantización o hardware.

## Alternativas consideradas

### Buscar solo en Herder

Se rechazó porque una fuente complementaria puede contener evidencia más directa.
El core recibe prioridad conservadora, no exclusividad salvo en búsquedas que lo
piden explícitamente.

### Reemplazar FTS5 por un servidor vectorial

Se rechazó por añadir un servicio y dependencia nativa innecesarios para el
volumen actual. SQLite y vectores locales mantienen portabilidad y fallback.

### Usar siempre 27B

Se rechazó: el benchmark fue más lento y produjo más rechazos seguros que 14B.

### Reprocesar visualmente todo el workbook

Se rechazó por coste, latencia y riesgo editorial. La política es seleccionar
páginas pobres relevantes y conservar variantes revisables.

## Criterios de revisión futura

Revisar esta decisión cuando cambie el modelo de embeddings, el corpus supere la
capacidad práctica del índice SQLite, exista OCR local convencional, se publiquen
tokens validados incrementalmente, se incorporen nuevos libros core o un nuevo
benchmark demuestre otra política de modelos.
