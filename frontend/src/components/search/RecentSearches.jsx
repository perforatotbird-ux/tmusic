import React from 'react'
import { History, X, Trash2 } from 'lucide-react'

export function RecentSearches({ searches = [], onSelect, onRemove, onClear }) {
  if (!searches || searches.length === 0) return null

  return (
    <div style={{ padding: '16px 4px' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '12px',
        }}
      >
        <div
          style={{
            fontSize: '12px',
            fontWeight: 700,
            textTransform: 'uppercase',
            letterSpacing: '0.05em',
            color: 'var(--dim)',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
          }}
        >
          <History size={14} />
          <span>Недавние запросы</span>
        </div>
        <button
          type="button"
          onClick={onClear}
          style={{
            background: 'none',
            border: 'none',
            color: 'var(--dim)',
            fontSize: '12px',
            fontWeight: 600,
            cursor: 'pointer',
            padding: '2px 6px',
            borderRadius: '4px',
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
            transition: 'color 0.15s ease',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--ink)')}
          onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--dim)')}
        >
          <Trash2 size={12} />
          <span>Очистить</span>
        </button>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
        {searches.map((s) => (
          <div
            key={s}
            onClick={() => onSelect(s)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '6px 12px',
              borderRadius: '999px',
              backgroundColor: 'rgba(255, 255, 255, 0.05)',
              border: '1px solid var(--line)',
              color: 'var(--ink)',
              fontSize: '13px',
              cursor: 'pointer',
              transition: 'background-color 0.15s ease, border-color 0.15s ease',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = 'rgba(255, 255, 255, 0.1)'
              e.currentTarget.style.borderColor = 'var(--dim)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = 'rgba(255, 255, 255, 0.05)'
              e.currentTarget.style.borderColor = 'var(--line)'
            }}
          >
            <span>{s}</span>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                onRemove(s)
              }}
              aria-label={`Удалить ${s}`}
              style={{
                background: 'none',
                border: 'none',
                padding: '2px',
                marginRight: '-4px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                color: 'var(--dim)',
                borderRadius: '50%',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--ink)')}
              onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--dim)')}
            >
              <X size={12} />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
