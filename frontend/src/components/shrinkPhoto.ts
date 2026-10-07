/** Downsize a picture in the browser before it is uploaded.
 *
 * A phone shot is 4000 px and several megabytes; the header shows it at about
 * 130 px, so 800 px on the long side is plenty even for print. Re-encoding
 * also drops EXIF — a phone photo's GPS position has no business on a CV.
 *
 * PNG stays PNG: a cut-out portrait like the one in the career-ops template
 * relies on transparency. A PNG that is still over the server's cap after the
 * resize goes out as JPEG instead.
 */

const MAX_SIDE = 800
const MAX_BYTES = 2 * 1024 * 1024

function toBlob(canvas: HTMLCanvasElement, type: string): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error('Не удалось обработать фото'))),
      type,
      0.9,
    )
  })
}

export async function shrinkPhoto(file: File): Promise<Blob> {
  let bitmap: ImageBitmap
  try {
    bitmap = await createImageBitmap(file)
  } catch {
    throw new Error('Это не фото или браузер не умеет его открыть')
  }

  const ratio = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(bitmap.width * ratio)
  canvas.height = Math.round(bitmap.height * ratio)
  const context = canvas.getContext('2d')
  if (!context) throw new Error('Не удалось обработать фото')
  context.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
  bitmap.close()

  if (file.type === 'image/png') {
    const png = await toBlob(canvas, 'image/png')
    if (png.size <= MAX_BYTES) return png
  }
  return toBlob(canvas, 'image/jpeg')
}
