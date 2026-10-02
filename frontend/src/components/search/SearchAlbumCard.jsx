import React, { useState } from 'react'
import { Disc } from 'lucide-react'
import { ClickableRow } from '../ui/ClickableRow'

function hashHue(str) {
  let h = 0
  const s = str || '?'
  for (let i = 0; i < s.length; i++) {
    h = (h * 31 + s.charCodeAt(i)) % 360
  }
  return h
}

export function SearchAlbumCard({ album, onClick }) {
  const [imgError, setImgError] = useState(false)
  const coverSrc = album.custom_image_url || album.cover_url
  const letter = (album.title || '?').trim()[0] || '?'
  const hue = hashHue(album.title)

  return (
    <ClickableRow
      onClick={() => onClick?.(album)}
      style={{
        display: 'flex',
        flexDirection: 'column',
        padding: '10px',
        borderRadius: '12px',
        cursor: 'pointer',
        transition: 'background-color 0.15s ease, transform 0.15s ease',
        backgroundColor: 'rgba(255, 255, 255, 0.02)',
        border: '1px solid rgba(255, 255, 255, 0.05)',
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
      {/* Square Cover */}
      <div
        style={{
          width: '100%',
          aspectRatio: '1 / 1',
          borderRadius: '10px',
          overflow: 'hidden',
          marginBottom: '8px',
          backgroundColor: 'rgba(255, 255, 255, 0.05)',
          boxShadow: '0 4px 12px rgba(0, 0, 0, 0.35)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: `linear-gradient(135deg, hsl(${hue}, 40%, 25%), hsl(${(hue + 45) % 360}, 45%, 15%))`,
        }}
      >
        {coverSrc && !imgError ? (
          <img
            src={coverSrc}
            alt={album.title}
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
          lineHeight: '1.2',
        }}
        title={album.title}
      >
        {album.title}
      </div>

      <div
        style={{
          fontSize: '11.5px',
          color: 'var(--dim)',
          marginTop: '3px',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          display: 'flex',
          gap: '4px',
        }}
      >
        {album.artist_name && <span>{album.artist_name}</span>}
        {album.artist_name && album.year && <span>•</span>}
        {album.year && <span>{album.year}</span>}
      </div>
    </ClickableRow>
  )
}
