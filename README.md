# LLC — Laboratory Language Companion

**Local-first language learning for laboratory and life-science professionals.**

LLC es una aplicación desktop, local-first y open source para aprender idiomas
con una especialización progresiva en laboratorio, biomedicina y life sciences.
German es la primera implementación y la arquitectura de referencia; no define
la identidad completa del producto ni impide futuras implementaciones de otros
idiomas.

La implementación actual combina FastAPI, Next.js, Swift y SQLite. LM Studio es
opcional: si está apagado, el perfil, Study, Library y el resto de las funciones
deterministas siguen disponibles. Los modelos locales se usan únicamente para
las funciones que realmente requieren inferencia.

## Requisitos (macOS Apple Silicon)

- Node.js 20.9 o posterior.
- npm.
- Python 3.12.
- LM Studio, solo para las funciones que necesitan un modelo local.

```bash
brew install node python@3.12 lm_studio
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
`data/deutschos.sqlite3`. `LLC_TIMEZONE` controla la fecha civil del plan
diario y usa `Europe/Berlin` por defecto; los instantes se guardan en UTC.
`LLC_*` es el prefijo canónico de configuración. Las variables `DEUTSCHOS_*`
equivalentes siguen aceptándose como fallback temporal y siempre pierden ante
su equivalente `LLC_*`. El nombre legacy del archivo SQLite se conserva para no
forzar una migración destructiva o una segunda copia de datos.

## Inicio diario

```bash
./scripts/doctor.sh
./scripts/dev.sh
```

`doctor.sh` revisa el volumen, runtimes, dependencias, SQLite, migraciones,
LM Studio y los puertos 3000/8000. LM Studio ausente o apagado es un aviso, no un
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
./scripts/build-macos-app.sh
open dist/LLC.app
```

El resultado es `dist/LLC.app`. Es una aplicación SwiftUI ligera con una
ventana de estado; no abre Terminal ni incorpora un navegador. Arrástrala desde
Finder al Dock —no hace falta mover el bundle— y ábrela siempre con el SSD
conectado. La ruta del proyecto se descubre desde los scripts y queda registrada
en el bundle durante el build; el nombre físico del directorio del clon no forma
parte del contrato.

- **Iniciar** ejecuta `scripts/start.sh`, espera a LM Studio, FastAPI y Next.js,
  y abre o activa `~/Applications/LLC.app` mediante su bundle exacto.
  Durante la transición también reconoce `~/Applications/DeutschOS.app` como
  fallback legacy, sin crearla, copiarla ni eliminarla. Safari se usa una sola
  vez como respaldo si ninguna aplicación web instalada puede abrirse.
- **Detener** cierra la web app exacta, FastAPI, Next.js, el servidor local y
  `LM Studio.app`, verifica los puertos y mantiene abierta la ventana.
- **Salir** realiza el mismo apagado completo antes de terminar la aplicación.

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
`dist/` y `LM Studio/` están excluidos de Git. FastAPI y Next.js solo reciben
señales con PID, huella de inicio y comando validados; LM Studio se cierra por
su bundle exacto aunque se abriera manualmente. Si el bundle está copiado fuera del SSD puede
mostrar «SSD no disponible»; si vive en el propio SSD, macOS no podrá abrirlo
hasta volver a montar `/Volumes/Juegos`. No se instalan LaunchAgents ni se
configura inicio automático de sesión.

El launcher AppleScript anterior se conserva solo como respaldo. Para
regenerarlo y usarlo temporalmente:

```bash
./scripts/create-macos-launcher.sh
open "dist/legacy/LLC Launcher.app"
```

Ese respaldo mantiene el comportamiento antiguo de arrancar y abrir la web sin
mostrar una ventana de control. Nunca comparte el nombre ni la ruta principal
de la aplicación nativa.

## Probar el Learning Engine

El planificador y los repasos funcionan aunque LM Studio esté apagado. Con API y
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

La búsqueda léxica FTS5 funciona sin LM Studio. Embeddings y transcripción se
declaran no disponibles si no existe un proveedor local real; no se simulan ni
se descargan modelos. Qwen puede crear KnowledgeUnits candidatas y borradores
con fuentes, siempre separados del currículo y del progreso. Consulta
[Biblioteca educativa](docs/educational-library.md) para formatos, API,
seguridad y limitaciones.

## Modelo local opcional

En una terminal independiente:

```bash
lm_studio serve
lm_studio pull qwen3:8b
```

El chat permite elegir cualquier modelo instalado. Si LM Studio no responde, la
API comunica indisponibilidad real; no genera respuestas simuladas.

## Copias de seguridad

Una copia manual se crea por defecto dentro de `./backups`:

```bash
./scripts/backup.sh
```

Para guardarla en otra unidad:

```bash
./scripts/backup.sh "/Volumes/Otro Disco/LLC-backups"
```

También se puede definir `LLC_BACKUP_DIR` en el entorno o en `.env`. Cada
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
npm --workspace @llc/web run typecheck
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

## Origen y repositorio

LLC se desarrolló originalmente como **DeutschOS**, una aplicación personal
para aprender alemán con material estructurado e IA local. Ese nombre se
conserva únicamente en historia, identificadores persistentes y fallbacks de
compatibilidad documentados.

El hogar público del proyecto es
[`Cuenca-john1999/laboratory-language-companion`](https://github.com/Cuenca-john1999/laboratory-language-companion).
