# 💼 Job Hunter Bot — ofertas Junior / Trainee / Practicante → ntfy.sh

Bot en **Python 3.11+** que corre 24/7 en un VPS Ubuntu (sin interfaz gráfica), busca cada hora
ofertas de programador **Junior / Trainee / Practicante / Intern / Entry-Level** en más de 20
portales nacionales (Perú) e internacionales, las puntúa (0–100), deduplica y te envía una
**notificación push a tu celular vía ntfy.sh** por cada oferta nueva con buen match, con el
enlace directo y un resumen de la descripción.

Todo (keywords, ubicaciones, exclusiones, fuentes, umbrales) se configura en `config.yaml` y `.env`:
**no hay nada hardcodeado en el código**.

---

## 1. Instalación en un VPS Ubuntu 22.04+ desde cero

```bash
# 1) Dependencias del sistema
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git tzdata

# 2) Copia el proyecto al servidor (ejemplo con scp desde tu PC)
#    scp -r job-hunter-bot root@TU_IP:/opt/
sudo mkdir -p /opt/job-hunter-bot
cd /opt/job-hunter-bot        # aquí deben estar main.py, config.yaml, core/, scrapers/...

# 3) Entorno virtual + dependencias
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 4) Variables de entorno
cp .env.example .env
nano .env                     # pon tu NTFY_TOPIC (y las API keys si las tienes)

# 5) Prueba que la notificación llega a tu celular
python main.py --test-notify

# 6) Prueba un ciclo completo (puede tardar 5–12 min según fuentes activas)
python main.py --once
```

> **Zona horaria**: los timestamps y el scheduler usan `America/Lima`
> (configurable en `scheduler.timezone`). Opcional: `sudo timedatectl set-timezone America/Lima`.

### Playwright (OPCIONAL — no es necesario)

El bot funciona perfectamente **sin** Playwright (`advanced.use_playwright: false`, valor por
defecto), manteniendo el VPS liviano. Solo actívalo si quieres intentar las fuentes que
renderizan con JavaScript (Bumeran, Jobrapido, Expertini):

```bash
source venv/bin/activate
pip install playwright playwright-stealth
playwright install --with-deps chromium     # ~400 MB
# y en config.yaml:  advanced.use_playwright: true
```

---

## 2. Configurar ntfy.sh (notificaciones al celular)

1. Instala la app **ntfy** en tu móvil (Android/Play Store, F-Droid o iOS App Store).
2. Elige un **topic** difícil de adivinar (es como una contraseña pública), por ejemplo
   `mi-empleo-secreto-8x2k`.
3. En la app: **+ → Subscribe to topic →** escribe exactamente ese topic → *Subscribe*.
4. Ponlo en `.env`:

```env
NTFY_TOPIC=mi-empleo-secreto-8x2k
NTFY_SERVER=https://ntfy.sh
# NTFY_TOKEN=  (solo si usas tu propio servidor ntfy con autenticación)
```

5. Verifica: `python main.py --test-notify` → debe sonar tu celular.

Formato de cada notificación:

```
💼 Practicante Desarrollo de Software — ACME S.A.C.
📍 Arequipa, Perú (Remoto 🌎 si aplica)
💰 S/. 1.200,00 (Mensual)
⭐ Match: 85/100 | Fuente: computrabajo
🎯 Skills de mi CV encontradas: javascript, react, html, css

Buscamos practicante de últimos ciclos con conocimientos de React y JavaScript…

🔗 https://pe.computrabajo.com/oferta/...
```

* `Priority: high` si el score ≥ 80 (suena distinto), `default` en el resto.
* Al **tocar la notificación** se abre la oferta (header `Click`), y hay un botón **“Ver oferta”**.
* Máximo **15 notificaciones por ciclo** (las de mayor score primero); si hay más, llega una
  notificación resumen: *“Se encontraron X ofertas más”*.
* Al arrancar el servicio: **“✅ Job Hunter Bot activo en tu VPS”**.
* Si **todas** las fuentes fallan en un ciclo: alerta con prioridad alta.

---

## 3. Tu CV manda: matching personalizado (`cv_profile`)

El bot **solo notifica ofertas donde realmente tienes posibilidades**, comparándolas con tu
perfil real definido en `config.yaml → cv_profile` (nada de esto está en el código):

```yaml
cv_profile:
  name: "Andres Raul Ore Soto"
  summary: >
    Estudiante de Ingeniería de Sistemas (3er año, UNSA Arequipa), Alumni Oracle Next
    Education (ONE) especialización Backend... Inglés básico A2, portugués intermedio.
  core_skills: ["javascript", "react", "node.js", "sql", "java", "python", "html", "css"]
  secondary_skills: ["typescript", "c#", "c++", "flutter", "dart", "bootstrap", "electron", ...]
  max_years_experience: 2          # descarta ofertas que pidan más años
  english_level: "A2"
  strict_english_filter: true      # descarta ofertas que exijan inglés avanzado/fluido
  incompatible_stacks: ["php", "laravel", "ruby on rails", "golang", "rust", "cobol", "sap abap", ...]
  friendly_signals: ["estudiante", "part-time", "mentoría", "training", "bootcamp", "primer empleo", ...]
```

### ETAPA 1 — Filtros duros (si falla cualquiera → score 0, nunca se notifica)

| Filtro | Ejemplo que se descarta |
|---|---|
| **Seniority excluyente** (`search.exclude_keywords`) | “**Senior** PHP Developer”, “Tech **Lead**”, “**Arquitecto** de software” |
| **Años de experiencia** (regex es/en, umbral `max_years_experience: 2`) | “**5 años de experiencia**”, “mínimo 3 años”, “4+ years of experience” |
| **Inglés avanzado** (`strict_english_filter`) | “**inglés avanzado**”, “**fluent English**”, “English **C1**”, “bilingüe”. ✅ *Sí* pasa: “inglés básico / técnico / lectura / intermedio” |
| **Stack incompatible** (`incompatible_stacks` y ninguna de tus skills) | oferta 100% PHP/Laravel, Ruby on Rails, Golang, Rust, Cobol, SAP ABAP |
| **Rubro no relacionado a software** (`non_dev_keywords` sin rol técnico ni skills) | “Practicante área **Marketing**”, “Asesor de **ventas**”, “**Call center**” |

El matching es **case-insensitive, sin tildes** (`unicodedata`) y por **palabra completa**:
`ia` ya no hace match dentro de “exper**ia**encia” ni `java` dentro de “**java**script`.

### ETAPA 2 — Scoring positivo (0–100, umbral `min_score: 55`)

| Regla | Puntos |
|---|---|
| El **título** contiene nivel de entrada (junior, jr, trainee, practicante, intern, entry level, graduate, sin experiencia, recién egresado, estudiante) | **+25** |
| **Skills de tu CV** en título + descripción: core 6 pts c/u (máx 24) + secundarias 2 pts c/u (máx 6) | **+30** |
| **Arequipa** (presencial/híbrido) o **remoto desde Perú** | **+15** |
| **Remoto internacional** LATAM / worldwide (y que pasó el filtro de inglés) | **+10** |
| Publicada hace menos de **48 h** | **+10** |
| **Señales amigables**: estudiante, part-time, mentoría, formación, training, bootcamp, primer empleo, Oracle/ONE | **+10** |

Las **skills encontradas se guardan** y viajan en la notificación (`🎯 Skills de mi CV encontradas: react, javascript…`).

Cuando el listado solo trae una descripción corta, el bot **descarga la página de detalle**
antes de puntuar (`matching.enrich_details: true`, tope `max_detail_fetches_per_cycle`), para que
los filtros de experiencia/inglés se apliquen sobre el texto completo.

### ETAPA 3 (opcional, apagada por defecto) — Matching semántico con IA

```yaml
matching:
  use_ai_matching: true      # requiere OPENAI_API_KEY en .env
  ai_provider: "openai"      # o "emergent" si usas EMERGENT_LLM_KEY
  ai_model: "gpt-4o-mini"    # alternativas: gpt-4.1-mini, gpt-5-mini
  ai_weight: 0.4             # score final = 60% reglas + 40% IA
  ai_min_rule_score: 40      # solo consulta la IA si las reglas dan >= 40
  ai_max_calls_per_cycle: 25
```

Envía tu `cv_profile.summary` + la descripción de la oferta al LLM y espera
`{"match_score": 0-100, "reason": "..."}`. El motivo se incluye en la notificación (`🤖 IA: …`).
Los resultados se **cachean en SQLite** (tabla `ai_cache`) para no re-evaluar la misma oferta.
Si la API falla o falta la key, **se usan solo las reglas** (nunca crashea).
Costo aproximado con gpt-4o-mini: ~US$ 0.02–0.05 por cada 100 ofertas evaluadas.

### Editar tus palabras clave de búsqueda

```yaml
search:
  keywords:                       # ← EDITA ESTO
    - "junior developer"
    - "trainee programador"
    - "practicante desarrollo de software"
    - "junior full stack"
    - "junior backend"
    - "junior frontend react"
```

Tras editar el YAML: `sudo systemctl restart job_hunter`.

## 4. Comandos (CLI)

```bash
source venv/bin/activate

python main.py                      # modo 24/7: ciclo inmediato + cada 60 min
python main.py --once               # un solo ciclo completo y termina
python main.py --test-notify        # prueba de notificación ntfy
python main.py --source computrabajo   # prueba UNA sola fuente (útil para depurar)
python main.py --stats              # estadísticas de la BD (ofertas, fuentes, ciclos)
python main.py --list-sources       # lista fuentes, nivel y credenciales requeridas
python main.py --once --no-notify   # ciclo sin enviar notificaciones (modo prueba)
python -m tests.test_smoke          # smoke test: 3 APIs + scoring + dedup + ntfy
python -m tests.test_matcher        # tests del matcher basado en tu CV (11 casos)
```

---

## 5. Instalar como servicio systemd (24/7 con reinicio automático)

```bash
sudo cp /opt/job-hunter-bot/job_hunter.service /etc/systemd/system/job_hunter.service
# revisa que WorkingDirectory / ExecStart apunten a tu ruta real
sudo systemctl daemon-reload
sudo systemctl enable --now job_hunter

# Operación diaria
systemctl status job_hunter
journalctl -u job_hunter -f            # logs en vivo
sudo systemctl restart job_hunter      # tras editar config.yaml o .env
```

El servicio incluye `Restart=always` y `RestartSec=30`: si el proceso muere, systemd lo levanta
de nuevo en 30 s.

Logs propios del bot: `logs/bot.log` (rotativo, 5 MB × 3 archivos) con un resumen por ciclo:

```
Ciclo completado: 11 fuentes OK, 6 fallidas, 23 ofertas nuevas, 15 notificadas
```

---

## 6. Fuentes de empleo (arquitectura por niveles)

### NIVEL A — APIs oficiales / JSON público (máxima confiabilidad)

| Fuente | Key necesaria | Estado verificado |
|---|---|---|
| **Remotive** (`remotive.com/api/remote-jobs`) | — | ✅ funciona |
| **RemoteOK** (`remoteok.com/api`) | — | ✅ funciona |
| **Arbeitnow** (Europa: Alemania/Finlandia…) | — | ✅ funciona |
| **The Muse** (`themuse.com/api/public/jobs`) | — | ✅ funciona |
| **Get on Board** (LATAM tech) | — | ✅ funciona |
| **Google Careers** (HTML público server-rendered) | — | ✅ funciona |
| **Teamtailor — Enaex Perú** (JSON-LD JobPosting) | — | ✅ funciona |
| **Jooble** | `JOOBLE_API_KEY` | ⏳ pendiente de tu key |
| **Adzuna** (ES, US, MX, NZ, CA, DE) | `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | ⏳ pendiente de tu key |
| **JSearch / Google for Jobs** (LinkedIn, Indeed, Glassdoor, Bebee, Jobrapido…) | `RAPIDAPI_KEY` | ⏳ opcional, **muy recomendada** |
| **Microsoft Careers** (`gcsservices…/search/api/v1/search`) | — | ⚠️ suele bloquear IPs de datacenter (TLS/404) |
| **Meta Careers** (GraphQL público) | — | ⚠️ responde 400 a muchas IPs de datacenter |

### NIVEL B — Scraping HTML “amigable” (requests + BeautifulSoup, headers reales)

| Fuente | Estado verificado |
|---|---|
| **Computrabajo Perú** (+ Chile / México / Argentina) | ✅ funciona (título, empresa, salario, modalidad, antigüedad) |
| **NTT Data — Pandapé** | ✅ funciona (listado + JSON-LD del detalle) |
| **Kitempleo Perú** (`kitempleo.pe/search/`) | ✅ funciona |
| **beBee Perú** (ItemList + JobPosting JSON-LD) | ✅ funciona |
| **Bumeran Perú** | ⚠️ Cloudflare 403 (API interna y HTML) → intenta Playwright |
| **Buscojobs** | ⚠️ WAF responde 405 a IPs de VPS |
| **Expertini Perú** | ⚠️ 403 |
| **Jobrapido Perú** | ⚠️ resultados por JS + rate-limit 429 → requiere Playwright |

### NIVEL C — Sitios con anti-bot fuerte

* **LinkedIn**: se usa el endpoint público de *guest search*
  (`/jobs-guest/jobs/api/seeMoreJobPostings/search`) con headers de navegador real,
  **máximo 1 request por minuto**, pocas keywords por ciclo y backoff exponencial.
  Si responde **429 o 999**, la fuente se **desactiva automáticamente 6 horas**. ✅ verificado.
* **Indeed y Glassdoor**: **NO se scrapean**. Están protegidos por Cloudflare/DataDome y son
  inviables (y arriesgados) desde la IP de un VPS: bloqueo casi inmediato, CAPTCHAs y posible
  ban de IP. Se cubren **vía JSearch (Google for Jobs)** y **Adzuna**, que ya indexan sus ofertas
  de forma legítima. Activa `jsearch: true` y pon tu `RAPIDAPI_KEY` para cubrirlos.

### Reglas generales aplicadas a todas las fuentes

* Cada scraper es un módulo independiente que hereda de `BaseScraper`
  (`fetch_jobs(keywords, locations) -> list[JobOffer]`).
* Si una fuente falla: se registra en logs, se reintenta con **backoff 1 s → 4 s → 10 s** (3 intentos)
  y el ciclo continúa. **El bot nunca crashea por una fuente caída.**
* **Circuit breaker**: si una fuente falla 3 ciclos seguidos se desactiva **6 horas**
  (`advanced.circuit_breaker_*`). Se ve con `python main.py --stats`.
* Delay aleatorio **2–5 s** entre requests al mismo dominio, **11 User-Agents reales rotativos**,
  header `Accept-Language: es-PE,es;q=0.9,en;q=0.8`, timeout **20 s** en todos los requests.
* Antes de parsear HTML crudo se intenta **siempre** el JSON-LD `schema.org/JobPosting`
  embebido (mucho más estable).
* Si falta una API key, la fuente se **omite con un warning** (sin crash).

Activa/desactiva cada fuente individualmente en `config.yaml → sources`.

---

## 7. Cómo obtener las API keys gratuitas

### Jooble (`JOOBLE_API_KEY`)
1. Entra a <https://jooble.org/api/about>.
2. Completa el formulario (nombre, email, país, uso: búsqueda personal de empleo).
3. Recibirás la key por correo. Pégala en `.env` → `JOOBLE_API_KEY=...`.

### Adzuna (`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`)
1. Regístrate en <https://developer.adzuna.com/signup>.
2. Confirma el correo y entra a <https://developer.adzuna.com/admin/access_details>.
3. Copia **App ID** y **App Key** a `.env`.
4. Ajusta los países en `config.yaml → source_options.adzuna.countries`
   (`es`, `us`, `mx`, `nz`, `ca`, `de`, `gb`, …).

### JSearch / RapidAPI (`RAPIDAPI_KEY`) — recomendada para LinkedIn/Indeed/Glassdoor
1. Crea cuenta en <https://rapidapi.com/>.
2. Suscríbete al plan **Basic (gratuito)** de <https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch>.
3. Copia tu **X-RapidAPI-Key** a `.env` → `RAPIDAPI_KEY=...`.
4. En `config.yaml` pon `jsearch: true`.

### OpenAI (`OPENAI_API_KEY`) — solo si activas `use_ai_matching: true`
1. Crea una API key en <https://platform.openai.com/api-keys>.
2. Pégala en `.env` → `OPENAI_API_KEY=sk-...` y pon `matching.use_ai_matching: true`.
3. Si usas otro endpoint compatible con OpenAI, defínelo en `OPENAI_BASE_URL`.

Si dejas cualquiera vacía, el bot simplemente **omite** esa fuente / etapa (log de warning).

---

## 8. Deduplicación y base de datos

* SQLite en `jobs.db` (ruta configurable en `advanced.database_path`).
* Tabla `jobs`: `id` (**SHA-256** de *url normalizada + título + empresa*), `title`, `company`,
  `location`, `salary`, `url`, `description`, `source`, `score`, `is_remote`, `country`,
  `posted_at`, `matched_skills`, `ai_score`, `ai_reason`, `created_at`, `notified_at`.
* Antes de notificar se verifica que el hash **no exista**: nunca se notifica dos veces la misma oferta.
* **Dedup cruzada entre fuentes**: si el mismo *título + empresa* ya se notificó desde otra
  plataforma en los últimos **7 días** (`matching.cross_source_dedup_days`), se omite.
* **Limpieza automática**: se borran los registros con más de **60 días**
  (`matching.retention_days`).
* Tablas auxiliares: `source_state` (circuit breaker, último error), `cycles` (histórico de ciclos)
  y `ai_cache` (resultados del matching semántico opcional).

Verificación rápida del dedup:

```bash
python main.py --once      # notifica N ofertas
python main.py --once      # debe notificar 0 (todas ya estaban en la BD)
python main.py --stats
```

---

## 9. Estructura del proyecto

```
job-hunter-bot/
├── main.py                  # entrypoint: APScheduler cada 60 min + primera corrida inmediata + CLI
├── config.yaml              # TODA la configuración (keywords, fuentes, umbrales)
├── .env.example             # plantilla de credenciales
├── requirements.txt
├── README.md
├── job_hunter.service       # unidad systemd lista para usar
├── core/
│   ├── models.py            # dataclass JobOffer + normalización/hashing
│   ├── database.py          # SQLite + deduplicación + circuit breaker
│   ├── matcher.py           # filtros duros + scoring 0-100 basado en cv_profile
│   ├── matcher_ai.py        # opcional: matching semántico CV vs oferta con LLM
│   ├── notifier.py          # ntfy.sh (prioridad, tags, click, actions)
│   ├── http_client.py       # sesión requests: retries, UA rotativo, delays, JSON-LD, Playwright
│   └── logger.py            # logging rotativo (logs/bot.log) + consola
├── scrapers/
│   ├── base.py              # BaseScraper (clase abstracta) + utilidades comunes
│   ├── computrabajo.py      ├── jooble_api.py         ├── adzuna_api.py
│   ├── remotive_api.py      ├── remoteok_api.py       ├── arbeitnow_api.py
│   ├── themuse_api.py       ├── getonboard_api.py     ├── google_careers.py
│   ├── microsoft_careers.py ├── meta_careers.py       ├── teamtailor.py
│   ├── pandape.py           ├── kitempleo.py          ├── buscojobs.py
│   ├── bumeran.py           ├── expertini.py          ├── jobrapido.py
│   ├── bebee.py             ├── linkedin_guest.py     └── jsearch_api.py
└── tests/
    ├── test_smoke.py        # 3 fuentes API + scoring + dedup + notificación de prueba
    └── test_matcher.py      # 11 tests del matcher basado en tu CV (filtros duros + scoring)
```

---

## 10. Solución de problemas

| Síntoma | Qué revisar |
|---|---|
| No llegan notificaciones | `python main.py --test-notify`; ¿el topic de la app móvil es idéntico al de `.env`? |
| “faltan credenciales: …” | Es normal: esa fuente se omite hasta que pongas la key en `.env` |
| Una fuente falla siempre | `python main.py --source <nombre>` para ver el error; puede estar bloqueando tu IP |
| “desactivada por circuit breaker hasta …” | Se reactiva sola en 6 h; o borra su fila: `sqlite3 jobs.db "DELETE FROM source_state WHERE source='X';"` |
| Muy pocas ofertas | Añade keywords, baja `matching.min_score`, activa `jsearch` con `RAPIDAPI_KEY` |
| Se descarta una oferta que te interesaba | Revisa `logs/bot.log`: el motivo se registra (seniority, años, inglés, stack, rubro). Ajusta `cv_profile` |
| Quieres ver ofertas que piden inglés | Pon `strict_english_filter: false` o quita frases de `english_exclude_patterns` |
| Aparecen stacks que no manejas | Añádelos a `cv_profile.incompatible_stacks` |
| El ciclo tarda mucho | Normal: delays anti-bloqueo + LinkedIn 1 req/min. Desactiva fuentes lentas en `sources` |
| El servicio no arranca | `journalctl -u job_hunter -n 50`; revisa rutas en `job_hunter.service` |

---

## 11. ⚖️ Advertencia legal

* El **web scraping debe respetar los Términos de Servicio** de cada plataforma y su
  `robots.txt`. Varias fuentes prohíben explícitamente el scraping automatizado.
* Las **fuentes API oficiales** (Jooble, Adzuna, Remotive, RemoteOK, Arbeitnow, The Muse,
  Get on Board, JSearch) son las **recomendadas** y las que este bot prioriza (NIVEL A).
* **Indeed, Glassdoor y LinkedIn** restringen el scraping: por eso Indeed/Glassdoor no se
  scrapean y LinkedIn se consulta solo por su endpoint público de invitado, a baja frecuencia
  (1 request/minuto) y con desactivación automática si responde 429/999.
* Este proyecto está pensado para **uso personal, no comercial**, con **baja frecuencia**
  (1 ciclo por hora) y para la **búsqueda de empleo individual**. No revendas ni republiques
  los datos obtenidos.
* **El uso es bajo tu propia responsabilidad.** Si una plataforma te pide detener el acceso
  automatizado, desactiva esa fuente en `config.yaml` (`sources: <fuente>: false`).
