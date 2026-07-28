# Arquitectura

DeutschOS es un monorepo local-first. El navegador habla únicamente con FastAPI; FastAPI gestiona SQLite mediante SQLAlchemy y accede a inferencia a través de `ModelProvider`. `LMStudioProvider` traduce el HTTP específico de LM Studio a conceptos internos (`health_check`, `list_models`, `chat`, `structured_generate`). Ninguna ruta depende del JSON propio de LM Studio.

La API es la autoridad para datos de aprendizaje. El historial del LLM no es memoria: cada petición recibe solo el perfil y un contexto estructurado pequeño consultado explícitamente. Los datos, el código y los modelos permanecen separados (`data/`, repositorio y almacenamiento de LM Studio respectivamente).

Los errores de proveedor son visibles como disponibilidad falsa o HTTP 503. La generación estructurada valida con Pydantic, permite una sola reparación controlada y falla sin persistir si sigue siendo inválida.

El chat docente trata cada respuesta como una generación lógica que puede
contener segmentos efímeros. Una salida visible terminada por `length` conserva
su texto y admite una sola continuación automática con el mismo rol, modelo,
temperatura y límite de 2048 tokens por llamada. La continuación recibe el
contexto original y el texto parcial como turno `assistant` temporal; nunca
recibe reasoning previo. El servidor elimina únicamente el solapamiento exacto
sufijo/prefijo más largo, limitado a 512 caracteres y con un mínimo conservador
de cuatro. Los presupuestos de recuperación vacía y continuación son
independientes, pero una acción original nunca supera tres llamadas al
proveedor. Si la continuación vuelve a terminar por `length`, la interfaz
conserva una sola burbuja y ofrece una continuación manual, sin encadenar otra
automáticamente. Los segmentos, instrucciones internas y contenido privado no
se persisten: solo se actualiza una sesión lógica con metadatos seguros.

La biblioteca educativa usa una segunda SQLite reconstruible y versionada,
separada deliberadamente de la base de progreso. `educational_library` contiene
el escáner incremental, extractores, chunker, FTS5, embeddings locales con
provenance, roles editoriales, calidad por página y variantes revisables. FastAPI
compone esos servicios sin hacer que LM Studio sea requisito para catálogo o
búsqueda léxica. Consulta
[Biblioteca educativa](educational-library.md).

La consulta docente usa ese mismo catálogo e índice: reglas deterministas o un
planificador pequeño producen búsquedas, FTS5 y coseno se fusionan con RRF, y el
servicio selecciona evidencia core y complementaria. Un router asigna modelos por
función y un prompt genera una explicación con citas comprobadas. Conversaciones,
caché y provenance viven en la SQLite reconstruible de biblioteca. El perfil
normal se consulta solo para adaptar idioma y profundidad; la operación no
proyecta resultados al Learning Engine. Consulta el
[ADR 0009](adr/0009-herder-core-semantic-library.md).

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
rutas depende de LM Studio. Consulta [Learning Engine](learning-engine.md) y los
ADR [0004](adr/0004-deterministic-daily-planner.md) y
[0005](adr/0005-spaced-repetition-and-corrections.md).

El modo `/study` cruza ambos mundos sin mezclarlos: lee la ruta editorial Herder
desde la SQLite reconstruible de biblioteca, pero guarda sesiones, posición,
notas, dudas y enlaces manuales en la base principal no reconstruible. Solo
persiste IDs externos y snapshots; no existen claves foráneas entre bases. Sus
estados son autorreportados y nunca se proyectan a `StudentSkill` o
`SkillEvidence`. Consulta [Modo de estudio guiado](guided-study.md) y el
[ADR 0011](adr/0011-guided-study-persistence.md).
