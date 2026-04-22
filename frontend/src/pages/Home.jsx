import { useEffect, useState } from 'react'
import PeriodSelector from '../components/PeriodSelector'
import CompanySelector from '../components/CompanySelector'
import PreviewTable from '../components/PreviewTable'
import StatusCard from '../components/StatusCard'
import LogPanel from '../components/LogPanel'
import { getPeriodos, getPreview, ejecutarAutomatizacion, getDownloadUrl } from '../services/api'

const CURRENT_YEAR = new Date().getFullYear()

export default function Home() {
  const [periodos, setPeriodos] = useState({ anios: [CURRENT_YEAR], tipos: ['Real'] })
  const [anio, setAnio] = useState(CURRENT_YEAR)
  const [mes, setMes] = useState(new Date().getMonth() + 1)
  const [tipo, setTipo] = useState('Real')
  const [companies, setCompanies] = useState(['MLP', 'CEN', 'ANT', 'CMZ'])

  const [preview, setPreview] = useState(null)
  const [result, setResult] = useState(null)
  const [logs, setLogs] = useState([])

  const [loadingPeriods, setLoadingPeriods] = useState(true)
  const [loadingPreview, setLoadingPreview] = useState(false)
  const [loadingRun, setLoadingRun] = useState(false)

  function addLog(message, type = 'info') {
    setLogs(prev => [...prev, { message, type }])
  }

  useEffect(() => {
    getPeriodos()
      .then(data => {
        setPeriodos(data)
        if (data.anios?.length) setAnio(Math.max(...data.anios))
        if (data.tipos?.length) setTipo(data.tipos.includes('Real') ? 'Real' : data.tipos[0])
      })
      .catch(() => addLog('No se pudo conectar con el servidor. Verifique que el backend esté corriendo.', 'error'))
      .finally(() => setLoadingPeriods(false))
  }, [])

  function handlePeriodChange(changes) {
    if ('anio' in changes) setAnio(changes.anio)
    if ('mes' in changes) setMes(changes.mes)
    if ('tipo' in changes) setTipo(changes.tipo)
    setPreview(null)
    setResult(null)
  }

  async function handlePreview() {
    if (!companies.length) { addLog('Selecciona al menos una compañía.', 'warn'); return }
    setLoadingPreview(true)
    setPreview(null)
    setResult(null)
    addLog(`Cargando vista previa: año ${anio}, mes ${String(mes).padStart(2,'0')}, tipo ${tipo}…`)
    try {
      const data = await getPreview({ anio, mes, tipo, companies })
      setPreview(data)
      addLog(`Vista previa lista: ${data.matched_kpis} KPIs mapeados, ${data.unmatched_kpis} sin mapear.`,
        data.unmatched_kpis > 0 ? 'warn' : 'ok')
    } catch (e) {
      addLog(e.response?.data?.detail || 'Error al cargar vista previa.', 'error')
    } finally {
      setLoadingPreview(false)
    }
  }

  async function handleRun() {
    if (!companies.length) { addLog('Selecciona al menos una compañía.', 'warn'); return }
    setLoadingRun(true)
    setResult(null)
    addLog(`Ejecutando automatización: año ${anio}, mes ${String(mes).padStart(2,'0')}, tipo ${tipo}…`)
    try {
      const data = await ejecutarAutomatizacion({ anio, mes, tipo, companies })
      setResult(data)
      addLog(`✓ Excel generado: ${data.archivo_salida} — ${data.total_escritos} KPIs escritos.`, 'ok')
      data.companies.forEach(c => {
        if (c.kpis_no_mapeados > 0) {
          addLog(`${c.compania}: ${c.kpis_no_mapeados} KPIs sin mapear: ${c.kpis_no_mapeados_detalle.slice(0,3).join(', ')}${c.kpis_no_mapeados > 3 ? '…' : ''}`, 'warn')
        }
      })
    } catch (e) {
      addLog(e.response?.data?.detail || 'Error al ejecutar automatización.', 'error')
    } finally {
      setLoadingRun(false)
    }
  }

  const isRunning = loadingPreview || loadingRun

  return (
    <main className="max-w-7xl mx-auto px-4 sm:px-6 py-8 space-y-6">

      {/* Configuración */}
      <div className="card">
        <h2 className="text-corp-teal font-bold text-lg mb-5 flex items-center gap-2">
          <span className="w-1.5 h-5 bg-corp-teal rounded-full inline-block" />
          Configuración del período
        </h2>

        {loadingPeriods ? (
          <p className="text-sm text-gray-400 animate-pulse">Cargando períodos disponibles…</p>
        ) : (
          <div className="space-y-5">
            <PeriodSelector
              anio={anio}
              mes={mes}
              tipo={tipo}
              anios={periodos.anios || [CURRENT_YEAR]}
              tipos={periodos.tipos || ['Real']}
              onChange={handlePeriodChange}
              disabled={isRunning}
            />
            <CompanySelector
              selected={companies}
              onChange={setCompanies}
              disabled={isRunning}
            />
          </div>
        )}
      </div>

      {/* Acciones */}
      <div className="flex flex-wrap gap-3 items-center">
        <button
          onClick={handlePreview}
          disabled={isRunning || loadingPeriods}
          className="btn-secondary"
        >
          {loadingPreview ? '⟳ Cargando…' : '🔍 Vista previa'}
        </button>
        <button
          onClick={handleRun}
          disabled={isRunning || loadingPeriods}
          className="btn-primary"
        >
          {loadingRun ? '⟳ Ejecutando…' : '▶ Ejecutar automatización'}
        </button>

        {result?.archivo_salida && (
          <a
            href={getDownloadUrl(result.archivo_salida)}
            download
            className="btn-amber"
          >
            ⬇ Descargar Excel
          </a>
        )}
      </div>

      {/* Resultado */}
      {result && <StatusCard result={result} />}

      {/* Vista previa */}
      {preview && !result && <PreviewTable preview={preview} />}

      {/* Log */}
      <LogPanel logs={logs} />
    </main>
  )
}
