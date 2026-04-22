export default function LogPanel({ logs }) {
  if (!logs || logs.length === 0) return null

  return (
    <div className="card">
      <h3 className="font-semibold text-corp-navy mb-3 text-sm uppercase tracking-wider">
        Registro de actividad
      </h3>
      <ul className="space-y-1.5 max-h-48 overflow-y-auto">
        {logs.map((log, i) => (
          <li
            key={i}
            className={`text-xs flex gap-2 items-start font-mono ${
              log.type === 'error' ? 'text-corp-red' :
              log.type === 'warn'  ? 'text-corp-amber' :
              log.type === 'ok'    ? 'text-emerald-600' :
                                     'text-gray-600'
            }`}
          >
            <span className="shrink-0 opacity-50">
              {log.type === 'error' ? '✗' :
               log.type === 'warn'  ? '⚠' :
               log.type === 'ok'    ? '✓' : '›'}
            </span>
            <span>{log.message}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
