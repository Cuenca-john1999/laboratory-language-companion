# ADR 0020: Graphify como proyección opcional del conocimiento canónico

- Estado: aceptada
- Fecha: 2026-08-18

## Decisión

`CanonicalContent` de LLC permanece como fuente de verdad para conocimiento documental. Una capa
en `educational_library.graph` lo proyecta a diccionarios `{nodes, edges}` compatibles con
Graphify. Los IDs canónicos se conservan literalmente como IDs de nodo y relación; el origen,
estado de revisión, provenance y evidence originales viajan además como metadata `llc_*`.

El origen `extracted` se proyecta a `EXTRACTED` y `inferred` a `INFERRED`. El estado de revisión no
se colapsa dentro de esa confidence: `unverified`, `verified` y `rejected` permanecen separados.
LLC no posee hoy un origen equivalente a `AMBIGUOUS`, por lo que la proyección no lo inventa.

El adaptador importa `graphifyy` de forma diferida y solo cuando `LLC_GRAPH_ENABLED=true`. La
dependencia está en el extra `apps/api[graph]`; FastAPI, profesor, embeddings e ingestión no la
importan. Antes y después de construir el `DiGraph`, el adaptador rechaza pérdidas de identidad y
relaciones paralelas que Graphify no pueda representar sin sobrescritura.

Los artefactos son regenerables y se escriben por defecto bajo
`var/educational-library/graphify-out/`:

```bash
.venv/bin/python scripts/project-graphify.py path/to/canonical.json
LLC_GRAPH_ENABLED=true .venv/bin/python scripts/project-graphify.py \
  path/to/canonical.json --validate-with-graphify
```

Se pueden repetir `--item-id` para un POC conectado pequeño. El comando acepta únicamente
`llc.cloud-content.v1`; no reextrae PDFs, no llama modelos y no modifica el canonical.

## Consecuencias

LLC canonical knowledge remains the source of truth. Graphify artifacts are derived and
disposable. Eliminar Graphify no afecta al runtime ordinario. A cambio, Graphify usa actualmente
un grafo dirigido simple: más de una relación con el mismo par ordenado de nodos se conserva en
la proyección LLC, pero el adaptador se niega a construirla hasta que exista una representación
multigrafo segura. Sus consultas nativas tampoco interpretan `llc_review_status`: sin un filtro
LLC posterior recorrerían también assertions `unverified` o `rejected`, aunque esa metadata no se
haya perdido. Por eso esta fase no conecta el grafo al profesor ni acepta resultados de Graphify
como canonical.
