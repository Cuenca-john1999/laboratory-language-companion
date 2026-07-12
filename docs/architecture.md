# Arquitectura

DeutschOS es un monorepo local-first. El navegador habla únicamente con FastAPI; FastAPI gestiona SQLite mediante SQLAlchemy y accede a inferencia a través de `ModelProvider`. `OllamaProvider` traduce el HTTP específico de Ollama a conceptos internos (`health_check`, `list_models`, `chat`, `structured_generate`). Ninguna ruta depende del JSON propio de Ollama.

La API es la autoridad para datos de aprendizaje. El historial del LLM no es memoria: cada petición recibe solo el perfil y un contexto estructurado pequeño consultado explícitamente. Los datos, el código y los modelos permanecen separados (`data/`, repositorio y almacenamiento de Ollama respectivamente).

Los errores de proveedor son visibles como disponibilidad falsa o HTTP 503. La generación estructurada valida con Pydantic, permite una sola reparación controlada y falla sin persistir si sigue siendo inválida.

Las rutas SQLite relativas se anclan a la raíz calculada desde el código, por lo
que FastAPI, Alembic y los scripts usan el mismo archivo aunque se invoquen
desde otro directorio del SSD. La API activa claves foráneas en cada conexión.
Alembic desactiva su enforcement solo durante reconstrucciones de tabla de
SQLite y ejecuta `foreign_key_check` antes y después.

Milestone 1 añade `deutschos_api.learning_engine`, separado tanto de FastAPI
como del proveedor de modelos. `curriculum` define el catálogo versionado;
`scoring` y `reviews` son reglas puras; `planner` produce decisiones
reproducibles; `service` es la única capa que proyecta esas decisiones en
SQLAlchemy. Las rutas HTTP validan y traducen errores, pero no contienen reglas
pedagógicas.

La evidencia evaluada es el registro fuente inmutable; `StudentSkill` es una
proyección reconstruible. Los planes diarios también se persisten para que el
Dashboard reproduzca una decisión real y no la reinterprete. Ninguna de estas
rutas depende de Ollama. Consulta [Learning Engine](learning-engine.md) y los
ADR [0004](adr/0004-deterministic-daily-planner.md) y
[0005](adr/0005-spaced-repetition-and-corrections.md).
