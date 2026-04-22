const MESES = [
  'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
  'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre',
]

export default function PeriodSelector({ anio, mes, tipo, anios, tipos, onChange, disabled }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
      {/* Año */}
      <div>
        <label className="block text-sm font-semibold text-corp-navy mb-1">
          Año
        </label>
        <select
          value={anio}
          onChange={e => onChange({ anio: Number(e.target.value) })}
          disabled={disabled}
          className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm
                     focus:outline-none focus:ring-2 focus:ring-corp-teal disabled:bg-gray-50"
        >
          {anios.map(a => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>
      </div>

      {/* Mes */}
      <div>
        <label className="block text-sm font-semibold text-corp-navy mb-1">
          Mes de cierre
        </label>
        <select
          value={mes}
          onChange={e => onChange({ mes: Number(e.target.value) })}
          disabled={disabled}
          className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm
                     focus:outline-none focus:ring-2 focus:ring-corp-teal disabled:bg-gray-50"
        >
          {MESES.map((name, idx) => (
            <option key={idx + 1} value={idx + 1}>{name}</option>
          ))}
        </select>
      </div>

      {/* Tipo */}
      <div>
        <label className="block text-sm font-semibold text-corp-navy mb-1">
          Tipo de dato
        </label>
        <select
          value={tipo}
          onChange={e => onChange({ tipo: e.target.value })}
          disabled={disabled}
          className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm
                     focus:outline-none focus:ring-2 focus:ring-corp-teal disabled:bg-gray-50"
        >
          {tipos.map(t => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
      </div>
    </div>
  )
}
