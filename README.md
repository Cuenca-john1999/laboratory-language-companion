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

## Aplicación de control para macOS

Para compilar la aplicación nativa de control sin abrir Xcode:

```bash
cd /Volumes/Juegos/DeutschOS
./scripts/build-macos-app.sh
open dist/DeutschOS.app
```

El resultado es `dist/DeutschOS.app`. Es una aplicación SwiftUI ligera con una
ventana de estado; no abre Terminal ni incorpora un navegador. Arrástrala desde
Finder al Dock —no hace falta mover el bundle— y ábrela siempre con el SSD
conectado. La ruta del proyecto queda registrada durante el build, por lo que
también puede copiarse a `~/Applications` sin perder el proyecto del SSD.

- **Iniciar** ejecuta `scripts/start.sh`, muestra el progreso real y abre la web
  cuando Ollama, FastAPI y Next.js responden.
- **Abrir DeutschOS** abre `http://127.0.0.1:3000` cuando la web está disponible.
- **Detener** ejecuta `scripts/stop.sh` y mantiene abierta la ventana.
- **Salir** detiene los procesos gestionados antes de terminar la aplicación.

La X roja y `⌘Q` piden confirmación si hay servicios activos o un arranque en
curso. Cancelar conserva la ventana; **Detener y salir** espera el cierre. La app
consulta `scripts/status.sh --machine` cada cuatro segundos, en lugar de inferir
el estado a partir del último botón. Un segundo clic o arranque concurrente no
duplica servicios gracias al bloqueo y los PID files de los scripts existentes.

Los errores se resumen sin trazas técnicas y **Abrir logs** muestra `logs/`.
Para consultar o detener desde una shell siguen disponibles:

```bash
./scripts/status.sh
./scripts/stop.sh
```

Los logs privados están en `logs/` y los PID files en `run/`; ambos directorios,
`dist/` y `Ollama/` están excluidos de Git. `stop.sh` no cierra un Ollama, API o
web iniciados de otra manera. Si el bundle está copiado fuera del SSD puede
mostrar «SSD no disponible»; si vive en el propio SSD, macOS no podrá abrirlo
hasta volver a montar `/Volumes/Juegos`. No se instalan LaunchAgents ni se
configura inicio automático de sesión.

El launcher AppleScript anterior se conserva solo como respaldo. Para
regenerarlo y usarlo temporalmente:

```bash
./scripts/create-macos-launcher.sh
open "dist/legacy/DeutschOS Launcher.app"
```

Ese respaldo mantiene el comportamiento antiguo de arrancar y abrir la web sin
mostrar una ventana de control. Nunca comparte el nombre ni la ruta principal
de la aplicación nativa.

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

## Biblioteca educativa

Coloca materiales privados en `material educativo/` y abre `/library`. Los
originales no se modifican; el catálogo reconstruible vive en
`var/educational-library/` y ambos directorios están fuera de Git.

```bash
./scripts/educational-library.sh scan --metadata-only
./scripts/educational-library.sh scan
./scripts/educational-library.sh search "Akkusativ"
./scripts/educational-library.sh integrity
```

La búsqueda léxica FTS5 funciona sin Ollama. Embeddings y transcripción se
declaran no disponibles si no existe un proveedor local real; no se simulan ni
se descargan modelos. Qwen puede crear KnowledgeUnits candidatas y borradores
con fuentes, siempre separados del currículo y del progreso. Consulta
[Biblioteca educativa](docs/educational-library.md) para formatos, API,
seguridad y limitaciones.

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
./scripts/test-macos-app.sh
./scripts/test-macos-app.sh --integration
./scripts/build-macos-app.sh
npm run lint:web
npm --workspace @deutschos/web run typecheck
npm run build:web
```

La primera prueba de macOS es unitaria y no inicia servicios. `--integration`
ejecuta el mismo `ControllerModel` de la ventana y realiza un ciclo real de
Iniciar, segundo clic idempotente, Detener, reinicio y Salir.

Limitaciones actuales: el build produce un binario Apple Silicon con firma local
ad hoc; no hay actualización automática, app de barra de menús ni arranque al
iniciar sesión. Al cambiar la ruta del SSD hay que recompilar el bundle.

## Estructura

- `apps/web`: Next.js, TypeScript y App Router.
- `apps/api`: FastAPI, SQLAlchemy y Alembic.
- `apps/macos-controller`: ventana nativa SwiftUI y parser del estado operativo.
- `packages/shared`: contratos TypeScript compartidos.
- `data`: estado local privado, excluido de Git.
- `material educativo`: originales privados de solo lectura, excluidos de Git.
- `var/educational-library`: catálogo e índices reconstruibles, excluidos de Git.
- `docs`: arquitectura, pedagogía, seguridad, operaciones y roadmap.
- `scripts`: diagnóstico, desarrollo y copias consistentes.

Más contexto: [arquitectura](docs/architecture.md),
[seguridad](docs/security.md), [modelo de datos](docs/data-model.md) y
[pedagogía](docs/pedagogy.md). El alcance implementado y pendiente del núcleo
de Milestone 1 está en [Learning Engine](docs/learning-engine.md).
