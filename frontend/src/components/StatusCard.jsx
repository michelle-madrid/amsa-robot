function Icon({ ok }) {
  if (ok === null) return <span className="text-gray-400 text-xl">○</span>
  return ok
    ? <span className="text-emerald-500 text-xl">✓</span>
    : <span className="text-corp-red text-xl">✗</span>
}

export default function StatusCard({ result }) {
  if (!result) return null
  const { companies, total_escritos, archivo_salida, anio, mes } = result

  return (
    <div className="card border-l-4 border-corp-teal">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="font-bold text-corp-teal text-lg">Resultado</h3>
          <p className="text-sm text-gray-500">
            Período {anio} — Mes {String(mes).padStart(2, '0')}
          </p>
        </div>
        <div className="text-right">
          <p className="text-3xl font-bold text-corp-teal">{total_escritos}</p>
          <p className="text-xs text-gray-500">KPIs escritos</p>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        {companies.map(c => (
          <div
            key={c.compania}
            className={`rounded-lg p-3 text-center border ${
              c.ok ? 'border-emerald-200 bg-emerald-50' : 'border-red-200 bg-red-50'
            }`}
          >
            <Icon ok={c.ok} />
            <p className="font-bold text-sm mt-1">{c.compania}</p>
            <p className="text-xs text-gray-600">{c.kpis_escritos} escritos</p>
            {c.kpis_no_mapeados > 0 && (
              <p className="text-xs text-corp-amber font-medium">
                {c.kpis_no_mapeados} sin mapear
              </p>
            )}
          </div>
        ))}
      </div>

      <div className="bg-corp-gray rounded-lg px-4 py-2 flex items-center gap-2">
        <span className="text-corp-teal text-sm">📄</span>
        <span className="text-sm font-medium text-corp-navy truncate">{archivo_salida}</span>
      </div>
    </div>
  )
}
