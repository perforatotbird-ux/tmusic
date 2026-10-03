import React, { useEffect, useRef } from 'react'
import {
  Search,
  X,
  Loader2,
  Play,
  ArrowRight,
  Disc,
  Users,
  Music,
  FolderHeart,
  Frown,
} from 'lucide-react'
import { useSearchStore } from '../../store/searchStore'
import { usePlayerStore } from '../../store/playerStore'
import { SearchCategoryTabs } from './SearchCategoryTabs'
import { SearchTrackItem } from './SearchTrackItem'
import { SearchArtistCard } from './SearchArtistCard'
import { SearchAlbumCard } from './SearchAlbumCard'
import { RecentSearches } from './RecentSearches'
import Modal from '../ui/Modal'

export function UnifiedSearchModal({ onOpenArtist, onOpenAlbum }) {
  const {
    isOpen,
    closeSearch,
    query,
    setQuery,
    activeCategory,
    setActiveCategory,
    unifiedResults,
    categoryResults,
    isLoading,
    isLoadingMore,
    loadMoreCategory,
    recentSearches,
    addRecentSearch,
    removeRecentSearch,
    clearRecentSearches,
  } = useSearchStore()

  const { playQueue, playTrack } = usePlayerStore()
  const inputRef = useRef(null)

  // Global shortcut: / or Ctrl+K / Cmd+K to open, Esc to close
  useEffect(() => {
    const handleKeyDown = (e) => {
      const target = e.target
      const isInput =
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.isContentEditable)

      if (e.key === 'Escape' && isOpen) {
        closeSearch()
        return
      }

      if (
        (e.key === '/' && !isInput) ||
        ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K'))
      ) {
        e.preventDefault()
        if (isOpen) {
          closeSearch()
        } else {
          useSearchStore.getState().openSearch()
        }
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, closeSearch])

  const handleTrackPlay = (track, trackList = null) => {
    if (query.trim()) addRecentSearch(query.trim())
    // Search rows use backend key names — normalize to the queue/cached shape
    // so the offline cache stores real Artist → Album instead of blanks.
    const list = (trackList || [track]).map((t) => ({
      ...t,
      artist: t.artist || t.artist_name || '',
      album: t.album || t.album_title || '',
      coverUrl: t.coverUrl || t.custom_image_url || t.cover_url || null,
    }))
    const idx = list.findIndex((t) => t.id === track.id)
    playQueue(list, Math.max(0, idx), {
      title: track.album_title || 'Результаты поиска',
      artistName: track.artist_name || 'Различные исполнители',
      coverUrl: track.custom_image_url || track.cover_url,
    })
  }

  const handleArtistClick = (artistId) => {
    if (query.trim()) addRecentSearch(query.trim())
    closeSearch()
    onOpenArtist?.(artistId)
  }

  const handleAlbumClick = (album) => {
    if (query.trim()) addRecentSearch(query.trim())
    closeSearch()
    onOpenAlbum?.(album)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && query.trim()) {
      addRecentSearch(query.trim())
    }
  }

  const totalResultsCount =
    (unifiedResults.total_artists || 0) +
    (unifiedResults.total_albums || 0) +
    (unifiedResults.total_tracks || 0)

  const counts = {
    tracks: unifiedResults.total_tracks,
    artists: unifiedResults.total_artists,
    albums: unifiedResults.total_albums,
  }

  return (
    <Modal
      open={isOpen}
      onClose={closeSearch}
      title="Поиск по медиатеке"
      maxWidth={820}
      maxHeight="92dvh"
      initialFocusRef={inputRef}
      backdrop="rgba(0, 0, 0, 0.75)"
      backdropFilter="blur(10px)"
      style={{ background: 'transparent', border: 'none', boxShadow: 'none' }}
    >
      {/* Search Modal Container */}
      <div
        style={{
          width: '100%',
          maxWidth: '820px',
          maxHeight: '100%',
          backgroundColor: 'var(--bg)',
          border: '1px solid var(--line-bright, rgba(255, 255, 255, 0.12))',
          borderRadius: '20px',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          boxShadow: '0 24px 64px rgba(0, 0, 0, 0.8)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search Header Bar */}
        <div
          style={{
            padding: '16px 20px 12px 20px',
            borderBottom: '1px solid var(--line, rgba(255, 255, 255, 0.08))',
            display: 'flex',
            flexDirection: 'column',
            gap: '12px',
          }}
        >
          <div
            style={{
              position: 'relative',
              display: 'flex',
              alignItems: 'center',
              width: '100%',
            }}
          >
            <Search
              size={20}
              style={{
                position: 'absolute',
                left: '16px',
                color: 'var(--accent)',
                pointerEvents: 'none',
              }}
            />
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Поиск треков, исполнителей и альбомов..."
              style={{
                width: '100%',
                padding: '12px 50px 12px 48px',
                fontSize: '16px',
                fontWeight: 500,
                color: 'var(--ink, #fff)',
                backgroundColor: 'rgba(255, 255, 255, 0.05)',
                border: '1px solid rgba(255, 255, 255, 0.1)',
                borderRadius: '12px',
                boxSizing: 'border-box',
              }}
              autoFocus
            />

            <div
              style={{
                position: 'absolute',
                right: '12px',
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
              }}
            >
              {isLoading && (
                <Loader2 size={18} className="animate-spin" style={{ color: 'var(--accent)' }} />
              )}
              {query && (
                <button
                  type="button"
                  onClick={() => setQuery('')}
                  aria-label="Очистить"
                  style={{
                    background: 'none',
                    border: 'none',
                    color: 'var(--dim)',
                    cursor: 'pointer',
                    padding: '4px',
                    display: 'flex',
                    alignItems: 'center',
                  }}
                >
                  <X size={18} />
                </button>
              )}
              <button
                type="button"
                onClick={closeSearch}
                aria-label="Закрыть"
                style={{
                  background: 'rgba(255, 255, 255, 0.08)',
                  border: 'none',
                  color: 'var(--dim)',
                  borderRadius: '6px',
                  padding: '4px 8px',
                  fontSize: '11px',
                  fontWeight: 700,
                  cursor: 'pointer',
                  marginLeft: '4px',
                }}
              >
                ESC
              </button>
            </div>
          </div>

          {/* Category Tabs */}
          {query.trim() && (
            <SearchCategoryTabs
              activeCategory={activeCategory}
              onSelectCategory={setActiveCategory}
              counts={counts}
            />
          )}
        </div>

        {/* Search Results Scroll Area */}
        <div
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: '16px 20px',
            display: 'flex',
            flexDirection: 'column',
            gap: '24px',
          }}
        >
          {/* Initial state: No Query */}
          {!query.trim() ? (
            <RecentSearches
              searches={recentSearches}
              onSelect={(s) => setQuery(s)}
              onRemove={removeRecentSearch}
              onClear={clearRecentSearches}
            />
          ) : isLoading &&
            !unifiedResults.tracks.length &&
            !unifiedResults.artists.length &&
            !unifiedResults.albums.length &&
            !categoryResults.items.length ? (
            /* Loading Spinner */
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                padding: '60px 0',
                gap: '12px',
                color: 'var(--dim)',
              }}
            >
              <Loader2 size={32} className="animate-spin" style={{ color: 'var(--accent)' }} />
              <div style={{ fontSize: '13px', fontWeight: 600 }}>Ищем в каталоге...</div>
            </div>
          ) : activeCategory === 'all' ? (
            /* ─── UNIFIED VIEW (ALL CATEGORIES) ─── */
            totalResultsCount === 0 && !isLoading ? (
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  padding: '60px 0',
                  gap: '12px',
                  color: 'var(--dim)',
                }}
              >
                <Frown size={40} style={{ opacity: 0.6 }} />
                <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--ink)' }}>
                  Ничего не найдено
                </div>
                <div style={{ fontSize: '13px', textAlign: 'center', maxWidth: '360px' }}>
                  По запросу «{query}» не нашлось треков, исполнителей или альбомов. Попробуйте изменить формулировку.
                </div>
              </div>
            ) : (
              <>
                {/* Artists Section */}
                {unifiedResults.artists.length > 0 && (
                  <section>
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
                          fontSize: '15px',
                          fontWeight: 800,
                          color: 'var(--ink)',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '8px',
                        }}
                      >
                        <Users size={16} style={{ color: 'var(--accent)' }} />
                        <span>Исполнители</span>
                      </div>
                      {unifiedResults.total_artists > unifiedResults.artists.length && (
                        <button
                          type="button"
                          onClick={() => setActiveCategory('artists')}
                          style={{
                            background: 'none',
                            border: 'none',
                            color: 'var(--accent)',
                            fontSize: '12px',
                            fontWeight: 700,
                            cursor: 'pointer',
                            display: 'flex',
                            alignItems: 'center',
                            gap: '4px',
                          }}
                        >
                          Все {unifiedResults.total_artists} <ArrowRight size={13} />
                        </button>
                      )}
                    </div>
                    <div
                      style={{
                        display: 'grid',
                        gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))',
                        gap: '10px',
                      }}
                    >
                      {unifiedResults.artists.map((artist) => (
                        <SearchArtistCard
                          key={artist.id}
                          artist={artist}
                          onClick={handleArtistClick}
                        />
                      ))}
                    </div>
                  </section>
                )}

                {/* Albums Section */}
                {unifiedResults.albums.length > 0 && (
                  <section>
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
                          fontSize: '15px',
                          fontWeight: 800,
                          color: 'var(--ink)',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '8px',
                        }}
                      >
                        <Disc size={16} style={{ color: 'var(--accent)' }} />
                        <span>Альбомы</span>
                      </div>
                      {unifiedResults.total_albums > unifiedResults.albums.length && (
                        <button
                          type="button"
                          onClick={() => setActiveCategory('albums')}
                          style={{
                            background: 'none',
                            border: 'none',
                            color: 'var(--accent)',
                            fontSize: '12px',
                            fontWeight: 700,
                            cursor: 'pointer',
                            display: 'flex',
                            alignItems: 'center',
                            gap: '4px',
                          }}
                        >
                          Все {unifiedResults.total_albums} <ArrowRight size={13} />
                        </button>
                      )}
                    </div>
                    <div
                      style={{
                        display: 'grid',
                        gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
                        gap: '12px',
                      }}
                    >
                      {unifiedResults.albums.map((album) => (
                        <SearchAlbumCard
                          key={album.id}
                          album={album}
                          onClick={handleAlbumClick}
                        />
                      ))}
                    </div>
                  </section>
                )}

                {/* Tracks Section */}
                {unifiedResults.tracks.length > 0 && (
                  <section>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        marginBottom: '10px',
                      }}
                    >
                      <div
                        style={{
                          fontSize: '15px',
                          fontWeight: 800,
                          color: 'var(--ink)',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '8px',
                        }}
                      >
                        <Music size={16} style={{ color: 'var(--accent)' }} />
                        <span>Треки</span>
                      </div>

                      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                        <button
                          type="button"
                          onClick={() => handleTrackPlay(unifiedResults.tracks[0], unifiedResults.tracks)}
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: '5px',
                            background: 'var(--accent)',
                            color: '#000',
                            border: 'none',
                            borderRadius: '999px',
                            padding: '4px 12px',
                            fontSize: '12px',
                            fontWeight: 700,
                            cursor: 'pointer',
                          }}
                        >
                          <Play size={12} fill="#000" />
                          <span>Слушать</span>
                        </button>

                        {unifiedResults.total_tracks > unifiedResults.tracks.length && (
                          <button
                            type="button"
                            onClick={() => setActiveCategory('tracks')}
                            style={{
                              background: 'none',
                              border: 'none',
                              color: 'var(--accent)',
                              fontSize: '12px',
                              fontWeight: 700,
                              cursor: 'pointer',
                              display: 'flex',
                              alignItems: 'center',
                              gap: '4px',
                            }}
                          >
                            Все {unifiedResults.total_tracks} <ArrowRight size={13} />
                          </button>
                        )}
                      </div>
                    </div>

                    <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                      {unifiedResults.tracks.map((track) => (
                        <SearchTrackItem
                          key={track.id}
                          track={track}
                          onPlay={(t) => handleTrackPlay(t, unifiedResults.tracks)}
                          onOpenArtist={handleArtistClick}
                          onOpenAlbum={handleAlbumClick}
                        />
                      ))}
                    </div>
                  </section>
                )}
              </>
            )
          ) : (
            /* ─── CATEGORY SPECIFIC PAGINATED VIEW ─── */
            <>
              {categoryResults.items.length === 0 && !isLoading ? (
                <div
                  style={{
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                    padding: '60px 0',
                    gap: '12px',
                    color: 'var(--dim)',
                  }}
                >
                  <Frown size={40} style={{ opacity: 0.6 }} />
                  <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--ink)' }}>
                    Ничего не найдено
                  </div>
                </div>
              ) : activeCategory === 'tracks' ? (
                /* Tracks Paginated List */
                <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                  {categoryResults.items.map((track) => (
                    <SearchTrackItem
                      key={track.id}
                      track={track}
                      onPlay={(t) => handleTrackPlay(t, categoryResults.items)}
                      onOpenArtist={handleArtistClick}
                      onOpenAlbum={handleAlbumClick}
                    />
                  ))}
                </div>
              ) : activeCategory === 'artists' ? (
                /* Artists Paginated Grid */
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))',
                    gap: '12px',
                  }}
                >
                  {categoryResults.items.map((artist) => (
                    <SearchArtistCard
                      key={artist.id}
                      artist={artist}
                      onClick={handleArtistClick}
                    />
                  ))}
                </div>
              ) : activeCategory === 'albums' ? (
                /* Albums Paginated Grid */
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))',
                    gap: '14px',
                  }}
                >
                  {categoryResults.items.map((album) => (
                    <SearchAlbumCard
                      key={album.id}
                      album={album}
                      onClick={handleAlbumClick}
                    />
                  ))}
                </div>
              ) : null}

              {/* Load More Button */}
              {categoryResults.hasMore && (
                <div style={{ display: 'flex', justifyContent: 'center', padding: '16px 0' }}>
                  <button
                    type="button"
                    onClick={loadMoreCategory}
                    disabled={isLoadingMore}
                    style={{
                      background: 'rgba(255, 255, 255, 0.08)',
                      border: '1px solid var(--line)',
                      color: 'var(--accent)',
                      borderRadius: '8px',
                      padding: '8px 24px',
                      fontSize: '13px',
                      fontWeight: 700,
                      cursor: isLoadingMore ? 'default' : 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px',
                    }}
                  >
                    {isLoadingMore && <Loader2 size={14} className="animate-spin" />}
                    <span>{isLoadingMore ? 'Загрузка...' : 'Показать ещё'}</span>
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </Modal>
  )
}
