const ALL_COMPANIES = ['MLP', 'CEN', 'ANT', 'CMZ']

const COMPANY_LABELS = {
  MLP: 'Los Pelambres',
  CEN: 'Centinela',
  ANT: 'Antucoya',
  CMZ: 'Zaldívar',
}

export default function CompanySelector({ selected, onChange, disabled }) {
  const allSelected = selected.length === ALL_COMPANIES.length

  function toggleAll() {
    onChange(allSelected ? [] : [...ALL_COMPANIES])
  }

  function toggle(company) {
    if (selected.includes(company)) {
      onChange(selected.filter(c => c !== company))
    } else {
      onChange([...selected, company])
    }
  }

  return (
    <div>
      <label className="block text-sm font-semibold text-corp-navy mb-2">
        Compañías
      </label>
      <div className="flex flex-wrap gap-3">
        <button
          type="button"
          onClick={toggleAll}
          disabled={disabled}
          className={`px-4 py-1.5 rounded-full text-sm font-medium border transition-colors
            ${allSelected
              ? 'bg-corp-teal text-white border-corp-teal'
              : 'bg-white text-corp-teal border-corp-teal hover:bg-corp-gray'
            } disabled:opacity-40`}
        >
          Todas
        </button>
        {ALL_COMPANIES.map(c => (
          <button
            key={c}
            type="button"
            onClick={() => toggle(c)}
            disabled={disabled}
            className={`px-4 py-1.5 rounded-full text-sm font-medium border transition-colors
              ${selected.includes(c)
                ? 'bg-corp-teal text-white border-corp-teal'
                : 'bg-white text-corp-navy border-gray-200 hover:border-corp-teal'
              } disabled:opacity-40`}
          >
            <span className="font-bold">{c}</span>
            <span className="ml-1 text-xs opacity-75">– {COMPANY_LABELS[c]}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
