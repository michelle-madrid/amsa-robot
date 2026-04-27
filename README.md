# Robot 2026 — Automatización de Reportes AMSA

Sistema interno para automatizar la carga de KPIs mineros desde Excel a SAP, con visualización de datos, cálculo de varianzas de gestión y editor de parámetros de fórmulas.

---

## Índice

1. [Visión general](#1-visión-general)
2. [Stack tecnológico y justificación](#2-stack-tecnológico-y-justificación)
3. [Arquitectura de carpetas](#3-arquitectura-de-carpetas)
4. [Capa de datos: Parquet](#4-capa-de-datos-parquet)
5. [Backend: FastAPI](#5-backend-fastapi)
6. [Frontend: React standalone](#6-frontend-react-standalone)
7. [Flujos principales](#7-flujos-principales)
8. [Sistema de configuración en JSON](#8-sistema-de-configuración-en-json)
9. [Motor de cálculo de varianzas](#9-motor-de-cálculo-de-varianzas)
10. [Decisiones de diseño relevantes](#10-decisiones-de-diseño-relevantes)

---

## 1. Visión general

```
Excel (.xlsx)  →  Parquet  →  FastAPI  →  React (index.html)
                    ↑
              Robot 2026.xlsx
              (fuente de verdad)
```

El flujo completo es:
1. El usuario ejecuta la automatización desde la UI → el backend lee el Excel, extrae KPIs y los escribe en SAP/BPC usando la librería de automatización.
2. Los datos ya procesados se almacenan en `Libro_Gestion.parquet` como fuente de consulta analítica.
3. El frontend consulta el parquet (vía API) para visualizar KPIs por período, calcular varianzas y editar parámetros.

---

## 2. Stack tecnológico y justificación

### Python 3.12 + FastAPI

**Por qué Python:** El ecosistema de automatización de Office (openpyxl, xlwings, win32com) y de datos (pandas, pyarrow) es el más maduro en Python. Toda la lógica de lectura de Excel, transformación de KPIs y escritura en SAP ya existía o es trivial de escribir en Python.

**Por qué FastAPI:** Es el framework Python más rápido para construir APIs REST tipadas. Tres ventajas concretas para este proyecto:
- Validación automática de parámetros de entrada vía Pydantic (evita errores silenciosos en los endpoints de fórmulas y parámetros financieros).
- Async-native: permite que la UI no se congele mientras procesa archivos Excel grandes.
- Generación automática de documentación en `/docs` (útil para depurar endpoints sin herramientas externas).

Se eligió sobre Flask porque Flask no tiene validación de tipos integrada y sobre Django porque Django es excesivo para una API sin ORM ni autenticación.

### Pandas + PyArrow (Parquet)

**Por qué Parquet en vez de SQL:** Los datos de gestión son inmutables entre cargas (se reemplazan al correr la automatización, no se actualizan fila a fila). Un archivo Parquet es perfecto para esto: lectura columnar extremadamente rápida, compresión eficiente y sin necesidad de servidor de base de datos. En un entorno corporativo con recursos restringidos, no instalar PostgreSQL/SQL Server es una ventaja operacional real.

**Por qué Pandas:** La transformación de KPIs mineros requiere operaciones de agregación, pivote y filtrado por múltiples dimensiones (`compania`, `tipo`, `periodo`, `subcategory`, `kpi`). La API DataFrame de Pandas expresa estas operaciones en pocas líneas. PyArrow actúa como motor de serialización para el formato Parquet.

**Cache con invalidación por mtime:** El parquet se carga una sola vez en memoria (`_df_cache`) y se invalida automáticamente cuando el archivo cambia en disco (comparando `st_mtime`). Esto elimina el costo de I/O en cada request sin requerir Redis ni ninguna infraestructura adicional.

```python
def get_cached_df(parquet_path: Path) -> pd.DataFrame:
    global _df_cache, _cache_mtime
    mtime = parquet_path.stat().st_mtime
    if _df_cache is None or mtime != _cache_mtime:
        _df_cache = load_parquet(parquet_path)
        _cache_mtime = mtime
    return _df_cache
```

### React 18 (Babel standalone, sin bundler)

**Por qué React sin bundler:** Este es el punto de diseño más inusual. El frontend es un único archivo `frontend/index.html` que carga React, ReactDOM y Babel desde CDN y transpila JSX en el browser en tiempo real.

Esto tiene una justificación muy concreta: el proyecto corre en un entorno corporativo Windows con restricciones en la instalación de herramientas de desarrollo. Un `npm run build` requiere Node.js, un proceso de build, configurar CORS, etc. El archivo `index.html` se sirve directamente desde FastAPI con `StaticFiles`, no necesita build step, y cualquier cambio de código es instantáneo al recargar.

El costo es el tiempo de transpilación inicial de Babel (~1-2 segundos de spinner). Es aceptable para una herramienta interna de uso no masivo.

> Nota: Existe también un proyecto Vite en `frontend/src/` (con `package.json` y dependencias actualizadas a Node 24). Está disponible para una futura migración si se necesita un frontend más complejo, pero no está integrado al servidor actual.

### Tailwind CSS (vía CDN, inline styles)

Los estilos combinan un `<style>` global con clases utilitarias (`background`, `color`, `padding`) definidas como CSS custom properties. Se evitó Tailwind CDN porque añade 100KB sin beneficio real cuando los componentes son pocos y las clases personalizadas son preferibles para mantener consistencia en la paleta de colores corporativa.

### JSON como capa de configuración

En vez de base de datos de configuración, se usan tres archivos JSON editables:

| Archivo | Propósito |
|---|---|
| `data/kpi_structure.json` | Qué secciones y KPIs aparecen en la visualización |
| `data/kpi_mappings.json` | Qué KPI del parquet alimenta cada fila de la UI |
| `data/formula_params.json` | Qué KPIs del parquet usan las fórmulas de varianzas |
| `data/params_financieros.json` | Tipo de cambio real/presupuesto y exposición TC por compañía |

Esta decisión permite que el usuario final modifique la configuración desde la UI (pestaña "Fórmulas", "Mapeos", "Parámetros") sin tocar código. El backend los lee en cada request (o los cachea con mtime), de modo que un cambio guardado desde la UI se refleja inmediatamente.

---

## 3. Arquitectura de carpetas

```
amsa-robot/
├── backend/
│   └── app/
│       ├── config.py              # Paths globales (parquet, templates)
│       ├── main.py                # Inicialización FastAPI, monta rutas y static
│       ├── routes/
│       │   ├── automation.py      # POST /api/run — ejecuta automatización Excel→SAP
│       │   ├── calculos.py        # GET /api/calculos/{company} — varianzas
│       │   ├── params.py          # GET/PUT /api/params — parámetros financieros
│       │   ├── setup.py           # GET /api/periodos, /api/setup/parquet-kpis
│       │   └── visualizar.py      # GET /api/visualizar/{company} — visualización KPIs
│       └── services/
│           ├── mapping_store.py   # Carga/guarda kpi_mappings.json
│           └── parquet_service.py # Cache del DataFrame, helpers de filtrado
├── data/
│   ├── formula_params.json        # Config fórmulas de varianzas (editable desde UI)
│   ├── kpi_mappings.json          # Mapeos KPI display → KPI parquet
│   ├── kpi_structure.json         # Estructura de secciones para visualización
│   ├── params_financieros.json    # TC real/budget, exposición CLP por compañía
│   └── Libro_Gestion.parquet      # Fuente de datos (generada por automatización)
├── frontend/
│   ├── index.html                 # App completa: React + Babel standalone (producción)
│   └── src/                       # Proyecto Vite (disponible para futura migración)
└── templates/
    └── Robot 2026.xlsx            # Archivo Excel fuente de datos
```

---

## 4. Capa de datos: Parquet

### Esquema del DataFrame

Cada fila del parquet representa un KPI de una compañía en un período:

| Columna | Tipo | Descripción |
|---|---|---|
| `compania` | str | MLP, CEN, ANT, CMZ |
| `tipo` | str | real, plan, ppto |
| `periodo` | int | AAAAMM (e.g., 202601) |
| `anio` | int | 2026 |
| `subcategory` | str | Agrupación del KPI (e.g., "Mina", "Mineral Procesado") |
| `kpi` | str | Nombre del KPI |
| `mes` | float | Valor del mes puntual |
| `ytd` | float | Valor acumulado año a fecha |
| `enero`…`diciembre` | float | Valores mensuales del año |

### Snapshot pattern

Para consultas analíticas, se usa el concepto de **snapshot**: en vez de leer fila a fila, se carga una "foto" del parquet filtrada por `(compania, tipo, periodo)` en un dict `{subcategory||kpi → {field → value}}`. Esto permite hacer lookups O(1) durante el cálculo de varianzas.

```python
# Clave compuesta para evitar colisiones entre subcategorías
key = f"{subcategory}||{kpi_name}" if subcategory else kpi_name
```

---

## 5. Backend: FastAPI

### Resolución de KPIs (`_resolve`)

El parquet puede tener el mismo nombre de KPI en distintas subcategorías. La función `_resolve` implementa un mecanismo de desambiguación:

1. Busca la clave exacta `"subcategory||kpi"`.
2. Si no existe, busca todas las claves que terminan en `"||kpi"` (suffix match).
3. Si hay exactamente 1 match → lo usa. Si hay 0 o más de 1 → retorna `None`.

Esto permite usar claves cortas (`"Tratamiento"`) para KPIs únicos y claves calificadas (`"Mineral Procesado||Ley de Cu"`) para KPIs ambiguos.

### Endpoints principales

```
GET  /api/periodos                         → años, meses, tipos, compañías disponibles
GET  /api/setup/parquet-kpis/{company}     → lista de KPIs del parquet (para datalists)
GET  /api/visualizar/{company}             → KPIs por sección y período (2 comparaciones)
GET  /api/calculos/{company}               → varianzas por período
GET  /api/calculos/formula-params          → configuración de fórmulas actual
PUT  /api/calculos/formula-params          → guarda configuración de fórmulas
GET  /api/params/{company}                 → parámetros financieros
PUT  /api/params/{company}                 → guarda parámetros financieros
```

---

## 6. Frontend: React standalone

### Componentes principales

```
App
├── Header          — navegación entre pestañas
├── PeriodBar       — selector de período / tipo (visible en Visualizar y Parámetros)
├── AutoTab         — ejecutar automatización, ver log y resultado
├── MapeosTab       — editor de kpi_mappings.json por compañía
├── VisualizarTab   — tabla de KPIs con dos comparaciones + tabla de cálculos de varianzas
├── ParamsTab       — editor de params_financieros.json (TC real/budget, exposición)
└── FormulasTab     — editor de formula_params.json (KPI keys, costos unitarios, etc.)
```

### Patrón de fetch

Todos los llamados HTTP pasan por tres helpers simples:

```javascript
apiGet(path)          // GET, lanza Error si !r.ok
apiPost(path, body)   // POST application/json
apiPut(path, body)    // PUT application/json
```

No se usa ningún cliente HTTP externo (ni Axios en el `index.html`; Axios está en el proyecto Vite pero no en el archivo de producción).

### Formato de números

Se usa `Number.toLocaleString('es-CL', {minimumFractionDigits:2, maximumFractionDigits:2})` para respetar el formato chileno (punto como separador de miles, coma decimal).

---

## 7. Flujos principales

### 7.1 Visualización de KPIs

```
Usuario selecciona compañía + período 1 + período 2
→ GET /api/visualizar/{company}?tipo1=...&año1_ini=...
→ Backend:
    1. Lee kpi_structure.json  →  lista ordenada de secciones y KPIs
    2. Lee kpi_mappings.json   →  mapa lk → parquet KPI
    3. Para cada KPI, consulta parquet con _get_series() para ambos períodos
    4. Segundo pasaje: computa KPIs fórmula (_f: true) usando valores ya calculados
→ Frontend renderiza tabla con 2 filas por KPI (comp1, comp2) y columnas mensuales
```

Los **subheaders** (`type: "subheader"` en kpi_structure.json) son separadores visuales dentro de una sección, sin datos propios. El backend los pasa al frontend como `{type: "subheader", label: "..."}` y el frontend los renderiza como una fila de divider azul claro.

### 7.2 Cálculo de varianzas

```
Usuario hace clic en "Calcular"
→ GET /api/calculos/{company}?año_ini=...&mes_ini=...&año_fin=...&mes_fin=...
→ Backend:
    1. Lee formula_params.json  →  KPI keys configurados
    2. Lee params_financieros.json  →  TC real/budget + exposición CLP
    3. Para cada mes del rango: _fetch_snapshot(real) + _fetch_snapshot(plan)
    4. Computa varianzas con fórmulas trilineales y de precio/volumen
    5. YTD para secciones rate-based = suma de deltas mensuales (no del campo ytd del parquet)
→ Frontend renderiza tabla con columnas mensuales + Mes + YTD
```

### 7.3 Edición de parámetros

```
Usuario edita un campo en FormulasTab / ParamsTab / MapeosTab
→ Estado React local se actualiza (setParams / setMappings)
→ Usuario hace clic en "Guardar"
→ PUT /api/calculos/formula-params  o  PUT /api/params/{company}
→ Backend escribe el JSON en disco
→ Próximo request a /api/calculos o /api/visualizar usa los nuevos valores
```

---

## 8. Sistema de configuración en JSON

### `kpi_structure.json`

Define qué aparece en la pestaña Visualizar. Soporta tres tipos de ítem:

```json
{ "type": "header",    "label": "Nombre de Sección" }   // → sección nueva (fondo navy)
{ "type": "subheader", "label": "Nombre Sub" }           // → divisor dentro de sección (fondo azul claro)
{ "type": "kpi",       "label": "Nombre KPI", "unit": "kUS$" }  // → fila de datos
```

### `kpi_mappings.json`

Para cada par `"Sección||KPI"`, define la fuente en el parquet:

```json
"Gasto||Combustible": "Precios Insumos Críticos||Gasto Combustible",  // KPI directo
"Rendimientos||Bolas": { "_f": true, "a": "Consumos||Bolas", "op": "/", "b": "Variables Mineras||Procesamiento", "scale": 1000 },  // fórmula
"Gasto||Ácido": "__NA__"  // no disponible (se oculta con el filtro Ocultar N/A)
```

### `formula_params.json`

Configura qué KPIs del parquet alimentan cada variable de las fórmulas de varianzas:

```json
{
  "kpi_keys": {                          // variables para cálculos de precio/rendimiento/actividad
    "tarifa_bolas": "Tarifa Bolas",
    "ley_cu": "Mineral Procesado||Ley de Cu",
    ...
  },
  "desarrollo_mina": [...],              // KPIs para total Desarrollo Mina
  "desarrollo_mina_items": [             // desglose IFRIC20 / Efecto P / Efecto Q
    { "key": "ifric20", "label": "IFRIC20", "kpis": [] },
    ...
  ],
  "variacion_inventario": [...],
  "actividad_contexto": {               // Mov Mina, costos unitarios para Actividad
    "mov_mina_kpis": ["Movimiento Mina"],
    "costo_unitario_mina": null,        // null = no muestra hasta que el usuario lo configure
    "costo_unitario_conc": null,
    ...
  }
}
```

### `params_financieros.json`

```json
{
  "kpis": {
    "2026": {
      "1": { "dolar_real": 881.96, "dolar_budget": 910 },
      ...
    }
  },
  "exp_tc": { "MLP": 50, "CEN": 55, "ANT": 51, "CMZ": 49.8 }
}
```

---

## 9. Motor de cálculo de varianzas

### Fórmula trilineal (Delta Actividad / Efecto Ley)

Para la descomposición de varianza en producción de cobre fino:

```
Cu_fino = Procesamiento × Ley × Recuperación
```

La varianza total se descompone en efectos de primer, segundo y tercer orden:

```
Δ_tratamiento = ΔP·Lb·Rb + ΔP·ΔL·Rb/2 + ΔP·ΔR·Lb/2 + ΔP·ΔL·ΔR/3
Δ_recuperacion = ΔR·Pb·Lb + ΔR·ΔP·Lb/2 + ΔR·ΔL·Pb/2 + ΔR·ΔP·ΔL/3
Efecto_ley      = ΔL·Pb·Rb + ΔL·ΔP·Rb/2 + ΔL·ΔR·Pb/2 + ΔL·ΔP·ΔR/3
```

Donde `Lb, Rb, Pb` son los valores presupuesto y `ΔP, ΔL, ΔR` las diferencias real-presupuesto. La Ley y la Recuperación se dividen entre 100 antes de aplicar la fórmula (están almacenadas como porcentaje en el parquet, e.g., 0.47% y 89.7%).

### Ajuste por Tipo de Cambio (Actividad Contexto)

Para los costos de Actividad Mina y Concentradora se aplica el factor:

```
tc_factor = exp_TC × (TC_ppto / TC_real) + (1 − exp_TC)
costo_adj = costo_unitario_budget × tc_factor
Actividad_Mina = Δ_volumen × costo_adj
```

Donde `exp_TC` es la fracción del costo denominada en CLP (compañía dependiente).

### YTD para secciones rate-based

Para Delta Gasto (Precio) y Delta Rendimiento el YTD **no** puede tomarse del campo `ytd` del parquet porque el parquet almacena acumulado de tarifa × acumulado de consumo, lo cual no equivale a la suma de los deltas mensuales. Se usa:

```python
ytd = sum(delta_mensual for cada mes del rango)
```

---

## 10. Decisiones de diseño relevantes

### Un solo archivo de frontend

`frontend/index.html` contiene toda la lógica de UI (~1600 líneas). La decisión es deliberada: en un entorno corporativo sin pipeline de CI/CD, mantener un único archivo deployable (que el servidor sirve directamente) elimina la fricción operacional. El costo es la falta de tree-shaking y separación de componentes, tolerable para una herramienta interna.

### Sin ORM ni base de datos

No hay SQLAlchemy, no hay migraciones, no hay servidor de DB. Los datos de gestión son leídos desde Excel (fuente de verdad) y cacheados en Parquet. Los parámetros de configuración viven en JSON. Esta decisión elimina una categoría entera de problemas de infraestructura (versiones de driver, conexiones, permisos de red) en un entorno donde el equipo de IT tiene alta latencia de respuesta.

### Configuración externalizada, código estable

Las variables que cambian con frecuencia (KPIs del parquet, costos unitarios, tipo de cambio) están en JSON editables desde la UI. El código Python no necesita modificarse cuando cambia el parquet o cuando se ajustan parámetros financieros. Esto es especialmente importante para un sistema que corre durante todo el año presupuestario y donde los KPIs pueden cambiar entre versiones del Excel Robot.

### Resolución ambigua de KPIs con fallback explícito

`_resolve()` retorna `None` si un KPI tiene múltiples subcategorías (en vez de elegir arbitrariamente). Esto fuerza al configurador a usar la clave calificada `"subcategory||kpi"` cuando hay ambigüedad, haciendo el error visible (la celda muestra `—`) en lugar de silencioso (valor incorrecto).

### Suma de deltas mensuales para YTD rate-based

Una varianza de precio es `(P_real − P_ppto) × Q_real`. El YTD de esta varianza es la **suma** de los 12 valores mensuales, no la aplicación de la fórmula sobre el YTD del parquet. El YTD del parquet para tarifas es el promedio ponderado acumulado, no un acumulable directo. El sistema lo corrige explícitamente acumulando los deltas mensuales calculados.
