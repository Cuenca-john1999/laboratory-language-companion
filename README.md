# DeutschOS

Sistema privado, local-first y de usuario único para aprender alemán. La API es
FastAPI, la interfaz usa Next.js y el estado del estudiante se conserva en
SQLite mediante migraciones Alembic. Ollama es opcional: si está apagado, el
perfil, el dashboard y el resto de la aplicación siguen disponibles.

## Requisitos (macOS Apple Silicon)

- Node.js 20.9 o posterior.
- npm.
- Python 3.12.
- Ollama, solo para las funciones que necesitan un modelo local.

```bash
brew install node python@3.12 ollama
```

## Instalación inicial

Desde la raíz del repositorio:

```bash
test -f .env || cp .env.example .env
npm ci
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e 'apps/api[dev]'
```

`.env` es configuración privada y está excluido de Git. Los valores
predeterminados usan únicamente loopback y guardan la base en
`data/deutschos.sqlite3`. `DEUTSCHOS_TIMEZONE` controla la fecha civil del plan
diario y usa `Europe/Berlin` por defecto; los instantes se guardan en UTC.

## Inicio diario

```bash
cd /Volumes/Juegos/DeutschOS
./scripts/doctor.sh
./scripts/dev.sh
```

`doctor.sh` revisa el volumen, runtimes, dependencias, SQLite, migraciones,
Ollama y los puertos 3000/8000. Ollama ausente o apagado es un aviso, no un
fallo esencial.

`dev.sh` comprueba los requisitos, aplica migraciones de forma segura y levanta
FastAPI y Next.js en `127.0.0.1`. Los errores permanecen visibles en la
terminal. `Ctrl-C` cierra ambos procesos. Abre `http://127.0.0.1:3000`; la
documentación de la API está en `http://127.0.0.1:8000/docs`.

La telemetría de Next.js queda desactivada durante el arranque mediante el
script. No se instala ni descarga nada automáticamente.

## Launcher de macOS

Para crear una aplicación nativa que arranque DeutschOS sin abrir Terminal:

```bash
cd /Volumes/Juegos/DeutschOS
./scripts/create-macos-launcher.sh
open dist/DeutschOS.app
```

El resultado es `dist/DeutschOS.app`. Puedes arrastrarlo desde Finder al Dock o
copiarlo primero a `~/Applications`; la aplicación conserva la ruta absoluta
del proyecto en el SSD. Al abrirla comprueba modelos locales, inicia Ollama con
`OLLAMA_MODELS=/Volumes/Juegos/DeutschOS/Ollama/models`, prepara migraciones,
levanta API/web en loopback y abre `http://127.0.0.1:3000` cuando todo responde.
Un segundo clic reutiliza los servicios activos y no crea duplicados.

Para consultar o detener los procesos gestionados por el launcher:

```bash
./scripts/status.sh
./scripts/stop.sh
```

Los logs privados están en `logs/` y los PID files en `run/`; ambos directorios,
`dist/` y `Ollama/` están excluidos de Git. `stop.sh` no cierra un Ollama, API o
web iniciados de otra manera. Si el SSD no está conectado, la aplicación muestra
una alerta: vuelve a montarlo en `/Volumes/Juegos` antes de reintentar. No se
instalan LaunchAgents ni se configura inicio automático de sesión.

## Probar el Learning Engine

El planificador y los repasos funcionan aunque Ollama esté apagado. Con API y
web iniciadas:

```bash
curl -s http://127.0.0.1:8000/api/learning/curriculum
curl -s -X POST http://127.0.0.1:8000/api/learning/daily-plan \
  -H 'Content-Type: application/json' \
  -d '{"available_minutes":10,"motivation":2}'
curl -s http://127.0.0.1:8000/api/learning/today
curl -s http://127.0.0.1:8000/api/learning/reviews
curl -s http://127.0.0.1:8000/api/learning/skills
```

El Dashboard refleja ese plan persistido. En `/progress` se puede regenerar con
otro tiempo/motivación y `/skills` muestra únicamente estimaciones internas
basadas en evidencia. Consulta [Learning Engine](docs/learning-engine.md) para
el contrato de intentos idempotentes y correcciones append-only.

## Modelo local opcional

En una terminal independiente:

```bash
ollama serve
ollama pull qwen3:8b
```

El chat permite elegir cualquier modelo instalado. Si Ollama no responde, la
API comunica indisponibilidad real; no genera respuestas simuladas.

## Copias de seguridad

Una copia manual se crea por defecto dentro de `./backups`:

```bash
./scripts/backup.sh
```

Para guardarla en otra unidad:

```bash
./scripts/backup.sh "/Volumes/Otro Disco/DeutschOS-backups"
```

También se puede definir `DEUTSCHOS_BACKUP_DIR` en el entorno o en `.env`. Cada
bundle incluye una copia SQLite consistente, configuración local editable y
datos pedagógicos adicionales compatibles. Incluye un manifiesto con hashes y
excluye almacenes y pesos de modelos. De `.env` solo conserva una lista cerrada
de opciones locales no secretas; cualquier clave desconocida se omite.
`backups/` nunca se incluye en Git.

El destino predeterminado protege frente a cambios de esquema o errores
locales, pero no frente al fallo físico del mismo SSD. Para eso, configura otra
unidad local. Consulta [operaciones](docs/operations.md) para los detalles de
backup, migración y restauración.

## Comprobaciones de calidad

```bash
cd apps/api
../../.venv/bin/python -m pytest
../../.venv/bin/python -m ruff check .
../../.venv/bin/python -m ruff format --check .
cd ../..
npm run lint:web
npm --workspace @deutschos/web run typecheck
npm run build:web
```

## Estructura

- `apps/web`: Next.js, TypeScript y App Router.
- `apps/api`: FastAPI, SQLAlchemy y Alembic.
- `packages/shared`: contratos TypeScript compartidos.
- `data`: estado local privado, excluido de Git.
- `docs`: arquitectura, pedagogía, seguridad, operaciones y roadmap.
- `scripts`: diagnóstico, desarrollo y copias consistentes.

Más contexto: [arquitectura](docs/architecture.md),
[seguridad](docs/security.md), [modelo de datos](docs/data-model.md) y
[pedagogía](docs/pedagogy.md). El alcance implementado y pendiente del núcleo
de Milestone 1 está en [Learning Engine](docs/learning-engine.md).
