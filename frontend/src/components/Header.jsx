export default function Header() {
  return (
    <header className="bg-corp-navy shadow-md">
      <div className="max-w-7xl mx-auto px-6 py-4 flex items-center gap-4">
        {/* Diamond logo marks */}
        <div className="flex gap-1 items-center">
          <span className="block w-3 h-3 rotate-45 bg-corp-teal-lt" />
          <span className="block w-3 h-3 rotate-45 bg-corp-amber" />
          <span className="block w-3 h-3 rotate-45 bg-corp-red" />
        </div>
        <div>
          <h1 className="text-white font-bold text-xl tracking-wide">
            Robot 2026
          </h1>
          <p className="text-corp-teal-lt text-xs font-medium tracking-widest uppercase">
            Automatización de Reportes
          </p>
        </div>
      </div>
    </header>
  )
}
