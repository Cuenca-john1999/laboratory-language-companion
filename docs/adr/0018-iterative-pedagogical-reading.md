# ADR 0018: Lectura pedagógica iterativa con evidencia

- Estado: aceptada
- Fecha: 2026-08-10

## Contexto

El cierre documental distingue una versión estructuralmente preparada de una
versión apta para lectura con IA. Esa lectura necesita extraer conceptos,
definiciones, reglas, restricciones, excepciones, contrastes, terminología,
patrones, paradigmas, ejemplos, advertencias, prerequisitos y relaciones sin
confundir una inferencia de modelo con memoria confirmada.

La versión de Herder se mantiene candidata e inactiva. Una lectura no debe
ejecutar OCR, Vision, inventario, extracción, chunks, embeddings ni activación.

## Decisión

Se adopta el contrato `pedagogical-reading.v1` y la migración de biblioteca v13.
Cada ejecución queda fijada a `source_id`, `source_version_id`, SHA-256 del
documento, idioma, rol y modelo local resuelto, versión del prompt,
configuración, número de pasada y linaje. La primera pasada planifica los temas
consolidados en orden; las posteriores sólo incluyen temas con fallo, evidencia
insuficiente, asuntos no resueltos o conflictos abiertos.

El texto observado se entrega al modelo por bloques estructurales. Si excede el
presupuesto, se divide exclusivamente en límites de bloque. Cada salida debe
seguir un esquema JSON estricto. Las citas se validan contra bloque, página,
versión y texto normalizado antes de persistirse. Una salida sin cita válida se
rechaza.

Conceptos y relaciones se almacenan como `proposed`, `corroborated`,
`needs_review` o `conflicted`. El motor nunca escribe `confirmed`, nunca activa
la versión y nunca incorpora candidatas a la memoria pedagógica verificada. La
corroboración requiere la misma afirmación observada en una ejecución anterior
con evidencia distinta.

Tema es la unidad atómica. Un fallo durable puede reintentarse sin repetir los
temas completados. Las ejecuciones admiten planificar, iniciar, pausar,
reanudar, cancelar y preparar una pasada focalizada. La continuación automática
es opt-in y está desactivada por defecto. Hay un máximo configurable de pasadas
y la ejecución se detiene cuando no queda un objetivo de mejora material.

La coordinación de IA es cooperativa: una consulta interactiva señala prioridad
y la lectura en background cede entre temas, nunca en mitad de la transacción de
un tema. Se reutilizan el router de modelos, LM Studio, sus timeouts y su única
reparación JSON controlada.

## Consecuencias

- La procedencia de cada candidata incluye documento, versión, página, bloque,
  cita, tema, pasada, modelo, rol, prompt, idioma, confianza e incertidumbre.
- Cobertura y delta se guardan por pasada; no representan una puntuación de
  aprendizaje ni una confirmación editorial.
- GET de readiness, planificación, ejecuciones, etapas, cobertura, candidatas,
  no resueltos, conflictos y scheduler son de solo lectura.
- La interfaz **AI Reading** del Laboratorio sólo expone controles explícitos y
  no ofrece confirmación masiva.
- La migración v13 es reversible a v12 y elimina únicamente el staging de
  lectura pedagógica.
