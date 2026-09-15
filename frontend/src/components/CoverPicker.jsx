import React, { useState, useEffect, useRef } from 'react'
import { libraryApi } from '../api/library'
import { Upload, Link as LinkIcon, Trash2, X, Check } from 'lucide-react'

export function CoverPicker({ target, onClose, onUpdated }) {
  const [activeTab, setActiveTab] = useState('url') // 'url' | 'file'
  const [url, setUrl] = useState('')
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState(null)
  // Every createObjectURL must be paired with revokeObjectURL, otherwise each
  // picked file leaks a blob URL until the tab is closed.
  const previewRef = useRef(null)

  useEffect(() => () => {
    if (previewRef.current) {
      URL.revokeObjectURL(previewRef.current)
      previewRef.current = null
    }
  }, [])

  if (!target) return null

  const setPreviewUrl = (nextFile) => {
    if (previewRef.current) {
      URL.revokeObjectURL(previewRef.current)
      previewRef.current = null
    }
    const nextUrl = nextFile ? URL.createObjectURL(nextFile) : null
    previewRef.current = nextUrl
    setPreview(nextUrl)
  }

  const handleFileChange = (e) => {
    const selected = e.target.files?.[0]
    if (selected) {
      setFile(selected)
      setPreviewUrl(selected)
      setError(null)
    }
  }

  const handleDrop = (e) => {
    e.preventDefault()
    const dropped = e.dataTransfer.files?.[0]
    if (dropped) {
      setFile(dropped)
      setPreviewUrl(dropped)
      setActiveTab('file')
      setError(null)
    }
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setIsLoading(true)
    setError(null)

    try {
      if (activeTab === 'file' && file) {
        await libraryApi.uploadCoverFile(target.type, target.id, file)
      } else if (activeTab === 'url' && url.trim()) {
        await libraryApi.setCoverUrl(target.type, target.id, url.trim())
      } else {
        setError('Выберите файл или вставьте ссылку на изображение')
        setIsLoading(false)
        return
      }
      onUpdated?.()
      onClose?.()
    } catch (err) {
      setError(err.message || 'Ошибка обновления обложки')
    } finally {
      setIsLoading(false)
    }
  }

  const handleReset = async () => {
    if (!confirm('Сбросить кастомную обложку к стандартной?')) return
    setIsLoading(true)
    try {
      await libraryApi.resetCover(target.type, target.id)
      onUpdated?.()
      onClose?.()
    } catch (err) {
      setError(err.message || 'Ошибка сброса обложки')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(10, 9, 8, 0.85)',
        backdropFilter: 'blur(8px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 9000,
        padding: '20px',
      }}
      onDragOver={(e) => e.preventDefault()}
      onDrop={handleDrop}
    >
      <div
        style={{
          backgroundColor: 'var(--bg-card)',
          border: '1px solid var(--line-bright)',
          borderRadius: 'var(--radius)',
          maxWidth: '460px',
          width: '100%',
          padding: '24px',
          position: 'relative',
        }}
      >
        <button
          onClick={onClose}
          style={{
            position: 'absolute',
            top: '16px',
            right: '16px',
            background: 'none',
            border: 'none',
            color: 'var(--dim)',
            cursor: 'pointer',
          }}
        >
          <X size={20} />
        </button>

        <h3 className="title-zine" style={{ fontSize: '18px', marginBottom: '4px' }}>
          Обложка: {target.title || target.displayName}
        </h3>
        <p style={{ color: 'var(--dim)', fontSize: '12.5px', marginBottom: '18px' }}>
          Загрузите файл или укажите защищённый URL изображения
        </p>

        {error && (
          <div
            style={{
              padding: '10px 14px',
              backgroundColor: 'rgba(255,107,107,0.1)',
              border: '1px solid var(--danger)',
              borderRadius: '8px',
              color: 'var(--danger)',
              fontSize: '12px',
              marginBottom: '14px',
            }}
          >
            {error}
          </div>
        )}

        {/* Tabs */}
        <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
          <button
            type="button"
            onClick={() => setActiveTab('url')}
            style={{
              flex: 1,
              padding: '8px',
              borderRadius: '6px',
              border: '1px solid var(--line)',
              backgroundColor: activeTab === 'url' ? 'var(--accent-muted)' : 'transparent',
              color: activeTab === 'url' ? 'var(--accent)' : 'var(--ink)',
              cursor: 'pointer',
              fontSize: '13px',
              fontWeight: '600',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '6px',
            }}
          >
            <LinkIcon size={14} /> URL
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('file')}
            style={{
              flex: 1,
              padding: '8px',
              borderRadius: '6px',
              border: '1px solid var(--line)',
              backgroundColor: activeTab === 'file' ? 'var(--accent-muted)' : 'transparent',
              color: activeTab === 'file' ? 'var(--accent)' : 'var(--ink)',
              cursor: 'pointer',
              fontSize: '13px',
              fontWeight: '600',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '6px',
            }}
          >
            <Upload size={14} /> Загрузить файл
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          {activeTab === 'url' ? (
            <input
              type="url"
              placeholder="https://example.com/cover.jpg"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value)
                setPreview(e.target.value)
              }}
              style={{
                width: '100%',
                padding: '10px 12px',
                backgroundColor: 'rgba(255,255,255,0.03)',
                border: '1px solid var(--line)',
                borderRadius: '8px',
                color: 'var(--ink)',
                fontSize: '13px',
                marginBottom: '16px',
                outline: 'none',
              }}
            />
          ) : (
            <div
              style={{
                border: '2px dashed var(--line-bright)',
                borderRadius: '8px',
                padding: '24px',
                textAlign: 'center',
                marginBottom: '16px',
                cursor: 'pointer',
              }}
              onClick={() => document.getElementById('cover-file-input').click()}
            >
              <Upload size={24} style={{ margin: '0 auto 8px auto', color: 'var(--dim)' }} />
              <div style={{ fontSize: '13px', color: 'var(--ink)' }}>
                {file ? file.name : 'Нажмите или перетащите файл сюда'}
              </div>
              <div style={{ fontSize: '11px', color: 'var(--dim)', marginTop: '4px' }}>PNG, JPG, WebP до 10 МБ</div>
              <input
                id="cover-file-input"
                type="file"
                accept="image/png,image/jpeg,image/webp"
                onChange={handleFileChange}
                style={{ display: 'none' }}
              />
            </div>
          )}

          {preview && (
            <div style={{ textAlign: 'center', marginBottom: '16px' }}>
              <img
                src={preview}
                alt="Предпросмотр"
                style={{
                  maxWidth: '120px',
                  maxHeight: '120px',
                  borderRadius: '6px',
                  objectFit: 'cover',
                  border: '1px solid var(--line)',
                }}
              />
            </div>
          )}

          <div style={{ display: 'flex', gap: '10px', marginTop: '16px' }}>
            {target.hasCustom && (
              <button
                type="button"
                onClick={handleReset}
                disabled={isLoading}
                style={{
                  padding: '10px 14px',
                  backgroundColor: 'transparent',
                  border: '1px solid var(--danger)',
                  color: 'var(--danger)',
                  borderRadius: '8px',
                  cursor: 'pointer',
                  fontSize: '13px',
                }}
              >
                <Trash2 size={16} />
              </button>
            )}
            <button
              type="submit"
              disabled={isLoading || (!url && !file)}
              style={{
                flex: 1,
                padding: '10px',
                backgroundColor: 'var(--accent)',
                color: '#141210',
                border: 'none',
                borderRadius: '8px',
                fontWeight: '700',
                cursor: 'pointer',
              }}
            >
              {isLoading ? 'Сохранение...' : 'Сохранить обложку'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
