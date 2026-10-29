import React, { useState } from 'react'
import { Users } from 'lucide-react'
import { ClickableRow } from '../ui/ClickableRow'

function hashHue(str) {
  let h = 0
  const s = str || '?'
  for (let i = 0; i < s.length; i++) {
    h = (h * 31 + s.charCodeAt(i)) % 360
  }
  return h
}

export function SearchArtistCard({ artist, onClick }) {
  const [imgError, setImgError] = useState(false)
  const coverSrc = artist.custom_image_url || artist.image_url
  const letter = (artist.display_name || '?').trim()[0] || '?'
  const hue = hashHue(artist.display_name)

  return (
    <ClickableRow
      onClick={() => onClick?.(artist.id)}
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        padding: '12px 8px',
        borderRadius: '12px',
        cursor: 'pointer',
        transition: 'background-color 0.15s ease, transform 0.15s ease',
        backgroundColor: 'rgba(255, 255, 255, 0.02)',
        border: '1px solid rgba(255, 255, 255, 0.05)',
        textAlign: 'center',
        userSelect: 'none',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.backgroundColor = 'rgba(255, 255, 255, 0.06)'
        e.currentTarget.style.transform = 'translateY(-2px)'
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.backgroundColor = 'rgba(255, 255, 255, 0.02)'
        e.currentTarget.style.transform = 'translateY(0)'
      }}
    >
      {/* Квадратная обложка — та же «коробка CD», что и в каталоге */}
      <div
        style={{
          width: '76px',
          height: '76px',
          borderRadius: '6px',
          overflow: 'hidden',
          marginBottom: '10px',
          flexShrink: 0,
          border: '1px solid rgba(255, 255, 255, 0.09)',
          boxShadow: '0 4px 12px rgba(0, 0, 0, 0.3)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: `linear-gradient(135deg, hsl(${hue}, 40%, 25%), hsl(${(hue + 45) % 360}, 45%, 15%))`,
        }}
      >
        {coverSrc && !imgError ? (
          <img
            src={coverSrc}
            alt={artist.display_name}
            onError={() => setImgError(true)}
            style={{ width: '100%', height: '100%', objectFit: 'cover' }}
          />
        ) : (
          <span
            style={{
              fontSize: '28px',
              fontWeight: 900,
              color: 'var(--accent)',
              textTransform: 'uppercase',
            }}
          >
            {letter}
          </span>
        )}
      </div>

      <div
        style={{
          fontSize: '13px',
          fontWeight: 700,
          color: 'var(--ink)',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          maxWidth: '100%',
          width: '100%',
        }}
        title={artist.display_name}
      >
        {artist.display_name}
      </div>

      <div
        style={{
          fontSize: '11px',
          color: 'var(--dim)',
          marginTop: '3px',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          maxWidth: '100%',
        }}
      >
        {artist.album_count > 0 ? `${artist.album_count} рел.` : 'Исполнитель'}
      </div>
    </ClickableRow>
  )
}
