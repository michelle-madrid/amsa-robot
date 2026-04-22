const MONTH_LABELS = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']

function fmt(v) {
  if (v === null || v === undefined) return '—'
  return Number(v).toLocaleString('es-CL', { maximumFractionDigits: 2 })
}

export default function PreviewTable({ preview }) {
  if (!preview) return null

  const { total_kpis, matched_kpis, unmatched_kpis, kpis } = preview

  return (
    <div className="card">
      <div className="flex flex-wrap gap-4 mb-4 items-center justify-between">
        <h3 className="font-bold text-corp-navy">Vista previa de datos</h3>
        <div className="flex gap-3">
          <span className="badge-ok">✓ {matched_kpis} mapeados</span>
          {unmatched_kpis > 0 && (
            <span className="badge-warn">⚠ {unmatched_kpis} sin mapear</span>
          )}
          <span className="text-xs text-gray-400">{total_kpis} total</span>
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-gray-100">
        <table className="min-w-full text-xs">
          <thead>
            <tr className="bg-corp-navy text-white">
              <th className="px-3 py-2 text-left font-medium sticky left-0 bg-corp-navy">Cía</th>
              <th className="px-3 py-2 text-left font-medium whitespace-nowrap">KPI</th>
              <th className="px-3 py-2 text-left font-medium">Categoría</th>
              <th className="px-3 py-2 text-center font-medium">Fila Excel</th>
              {MONTH_LABELS.map(m => (
                <th key={m} className="px-2 py-2 text-right font-medium">{m}</th>
              ))}
              <th className="px-2 py-2 text-right font-medium bg-corp-teal">Mes</th>
              <th className="px-2 py-2 text-right font-medium bg-corp-teal">YTD</th>
            </tr>
          </thead>
          <tbody>
            {kpis.map((k, i) => (
              <tr
                key={k.kpi_id + i}
                className={`border-t border-gray-100 ${
                  k.matched ? 'hover:bg-gray-50' : 'bg-amber-50'
                }`}
              >
                <td className="px-3 py-1.5 font-bold text-corp-teal sticky left-0 bg-inherit">
                  {k.compania}
                </td>
                <td className="px-3 py-1.5 max-w-xs truncate" title={k.kpi}>
                  {k.kpi}
                </td>
                <td className="px-3 py-1.5 text-gray-500 max-w-xs truncate">
                  {k.category}
                </td>
                <td className="px-3 py-1.5 text-center">
                  {k.matched
                    ? <span className="badge-ok">{k.excel_row}</span>
                    : <span className="badge-warn">sin fila</span>
                  }
                </td>
                {['enero','febrero','marzo','abril','mayo','junio',
                  'julio','agosto','septiembre','octubre','noviembre','diciembre'].map(m => (
                  <td key={m} className="px-2 py-1.5 text-right tabular-nums text-gray-700">
                    {fmt(k.valores[m])}
                  </td>
                ))}
                <td className="px-2 py-1.5 text-right tabular-nums font-semibold text-corp-teal">
                  {fmt(k.valores.mes)}
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums font-semibold text-corp-teal">
                  {fmt(k.valores.ytd)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
