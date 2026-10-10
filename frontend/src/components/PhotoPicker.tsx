/** The portrait for the resume header: a thumbnail and its buttons.
 *
 * Used on «Моё резюме» and, without the thumbnail, inside the designer, so a
 * user who ticks «Фото» there can add one without leaving the preview.
 */

import { useEffect, useMemo, useState, useTransition } from 'react'
import { useSWRConfig } from 'swr'
import { PHOTO_KEY, STATE_KEY, deletePhoto, uploadPhoto, usePhoto } from '../api/hooks'
import { shrinkPhoto } from './shrinkPhoto'
import { useToast } from './toast'

function usePhotoActions() {
  const { mutate } = useSWRConfig()
  const toast = useToast()
  const [busy, start] = useTransition()

  // Every resume preview carries the photo or not, so all of them are dropped
  // from the cache along with the picture itself.
  const refresh = () =>
    Promise.all([
      mutate(STATE_KEY),
      mutate(PHOTO_KEY),
      mutate((key) => typeof key === 'string' && key.includes('.html?')),
    ])

  function upload(file: File) {
    start(async () => {
      try {
        await uploadPhoto(await shrinkPhoto(file))
        await refresh()
      } catch (e) {
        toast.error(e)
      }
    })
  }

  function remove() {
    start(async () => {
      try {
        await deletePhoto()
        await refresh()
      } catch (e) {
        toast.error(e)
      }
    })
  }

  return { busy, upload, remove }
}

function UploadButton({
  label,
  busy,
  onFile,
  small,
}: {
  label: string
  busy: boolean
  onFile: (file: File) => void
  small?: boolean
}) {
  return (
    <label
      className={`btn btn--ghost${small ? ' btn--small' : ''}${busy ? ' is-disabled' : ''}`}
      aria-disabled={busy}
    >
      <input
        type="file"
        className="sr-only"
        accept="image/jpeg,image/png"
        disabled={busy}
        onChange={(event) => {
          const file = event.target.files?.[0]
          event.target.value = ''
          if (file) onFile(file)
        }}
      />
      {busy ? 'Загружаю…' : label}
    </label>
  )
}

/** A blob URL for the stored photo, revoked when it changes or unmounts. */
function usePhotoUrl(hasPhoto: boolean): string | null {
  const { data } = usePhoto(hasPhoto)
  const url = useMemo(
    () => (data && hasPhoto ? URL.createObjectURL(data) : null),
    [data, hasPhoto],
  )
  useEffect(
    () => () => {
      if (url) URL.revokeObjectURL(url)
    },
    [url],
  )
  return url
}

/** A drop zone in the look of the CV uploader. Unlike the CV, a dropped photo
 *  goes up at once: it is free, so there is nothing to confirm. */
function PhotoDrop({
  title,
  busy,
  onFile,
}: {
  title: string
  busy: boolean
  onFile: (file: File) => void
}) {
  const [dragging, setDragging] = useState(false)

  return (
    <label
      className={`drop photo-card__drop${dragging ? ' drop--over' : ''}${busy ? ' is-disabled' : ''}`}
      aria-disabled={busy}
      onDragOver={(event) => {
        event.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault()
        setDragging(false)
        const file = event.dataTransfer.files?.[0]
        if (file && !busy) onFile(file)
      }}
    >
      <input
        type="file"
        className="sr-only"
        accept="image/jpeg,image/png"
        disabled={busy}
        onChange={(event) => {
          const file = event.target.files?.[0]
          event.target.value = ''
          if (file) onFile(file)
        }}
      />
      <span className="drop__title">{busy ? 'Загружаю…' : title}</span>
    </label>
  )
}

/** Outside the editor a photo is only shown: replacing or deleting it is an
 *  edit, and two big controls beside the CV read as the page's main action.
 *  Without a photo the upload stays, since there is nothing to show. */
export function PhotoCard({ hasPhoto, editing }: { hasPhoto: boolean; editing: boolean }) {
  const { busy, upload, remove } = usePhotoActions()
  const url = usePhotoUrl(hasPhoto)

  return (
    <section className="card photo-card">
      <h2 className="card__title">Фото</h2>
      <div className="photo-card__body">
        {hasPhoto ? (
          <div className="photo-card__frame">
            {url ? <img src={url} alt="Ваше фото" className="photo-card__img" /> : null}
          </div>
        ) : null}
        {editing || !hasPhoto ? (
          <PhotoDrop
            title={hasPhoto ? 'Заменить фото' : 'Перетащите фото сюда'}
            busy={busy}
            onFile={upload}
          />
        ) : null}
      </div>
      {hasPhoto && editing ? (
        <button
          className="btn btn--quiet btn--small photo-card__remove"
          onClick={remove}
          disabled={busy}
        >
          Удалить фото
        </button>
      ) : null}
    </section>
  )
}

/** Just the upload, for the designer's «Фото» row when there is none yet. */
export function PhotoUploadButton() {
  const { busy, upload } = usePhotoActions()
  return <UploadButton label="Загрузить" busy={busy} onFile={upload} small />
}
