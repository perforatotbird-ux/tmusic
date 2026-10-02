import React from 'react'
import { Disc, Users, Music, Layers } from 'lucide-react'

export function SearchCategoryTabs({ activeCategory, onSelectCategory, counts = {} }) {
  const tabs = [
    { id: 'all', label: 'Все', icon: Layers, count: null },
    { id: 'tracks', label: 'Треки', icon: Music, count: counts.tracks },
    { id: 'artists', label: 'Исполнители', icon: Users, count: counts.artists },
    { id: 'albums', label: 'Альбомы', icon: Disc, count: counts.albums },
  ]

  return (
    <div
      role="tablist"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        overflowX: 'auto',
        padding: '6px 0',
        scrollbarWidth: 'none',
      }}
    >
      {tabs.map((tab) => {
        const Icon = tab.icon
        const isActive = activeCategory === tab.id
        const hasCount = typeof tab.count === 'number' && tab.count > 0

        return (
          <button
            key={tab.id}
            role="tab"
            aria-selected={isActive}
            onClick={() => onSelectCategory(tab.id)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '7px 14px',
              borderRadius: '999px',
              fontSize: '13px',
              fontWeight: isActive ? 700 : 500,
              cursor: 'pointer',
              whiteSpace: 'nowrap',
              transition: 'all 0.15s ease',
              border: isActive ? '1px solid var(--accent)' : '1px solid var(--line)',
              backgroundColor: isActive ? 'var(--accent)' : 'rgba(255, 255, 255, 0.04)',
              color: isActive ? '#000' : 'var(--ink)',
            }}
          >
            <Icon size={14} style={{ opacity: isActive ? 1 : 0.7 }} />
            <span>{tab.label}</span>
            {hasCount && (
              <span
                style={{
                  fontSize: '11px',
                  fontWeight: 800,
                  padding: '1px 6px',
                  borderRadius: '999px',
                  backgroundColor: isActive ? 'rgba(0, 0, 0, 0.15)' : 'rgba(255, 255, 255, 0.1)',
                  color: isActive ? '#000' : 'var(--dim)',
                }}
              >
                {tab.count > 999 ? '999+' : tab.count}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
