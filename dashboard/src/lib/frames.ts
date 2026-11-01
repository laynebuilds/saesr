// Samples frames from an uploaded video (or takes the photo) in the browser, so only small JPEGs
// go to the server. Frames are spread evenly across the clip.
export interface Frame {
  t: number
  data: string // base64 JPEG, no data: prefix
  url: string // data URL for thumbnails
}

const MAX_W = 960

function toFrame(source: CanvasImageSource, w: number, h: number, t: number): Frame {
  const scale = Math.min(1, MAX_W / w)
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(w * scale)
  canvas.height = Math.round(h * scale)
  canvas.getContext('2d')!.drawImage(source, 0, 0, canvas.width, canvas.height)
  const url = canvas.toDataURL('image/jpeg', 0.8)
  const frame = { t, url, data: url.slice(url.indexOf(',') + 1) }
  const probe = document.createElement('canvas')
  probe.width = probe.height = 16
  const pc = probe.getContext('2d')!
  pc.drawImage(canvas, 0, 0, 16, 16)
  const px = pc.getImageData(0, 0, 16, 16).data
  let sum = 0
  for (let i = 0; i < px.length; i += 4) sum += px[i] + px[i + 1] + px[i + 2]
  if (sum / (px.length / 4) / 3 < 6) dark.add(frame)
  return frame
}

const dark = new WeakSet<Frame>()
/** A frame that is (nearly) all black: the browser failed to decode it, not a dark scene. */
const isBlack = (f: Frame) => dark.has(f)

export async function framesFromFile(file: File, count = 8): Promise<{ frames: Frame[]; duration: number | null }> {
  const src = URL.createObjectURL(file)
  try {
    if (file.type.startsWith('image/')) {
      const img = new Image()
      img.src = src
      await img.decode()
      return { frames: [toFrame(img, img.naturalWidth, img.naturalHeight, 0)], duration: null }
    }
    const video = document.createElement('video')
    video.muted = true
    video.playsInline = true
    video.preload = 'auto'
    video.src = src
    await new Promise<void>((ok, fail) => {
      setTimeout(() => fail(new Error('The browser took too long to read this video. Try a shorter or smaller clip.')), 30000)
      video.onloadeddata = () => ok()
      video.oncanplay = () => ok()
      video.onerror = () => fail(new Error('This video format cannot be read by the browser. Try MP4 (H.264) or MOV.'))
    })
    const d = video.duration
    if (!Number.isFinite(d) || d <= 0) throw new Error('Could not read the video length.')
    const n = d < 2 ? 1 : count
    const frames: Frame[] = []
    for (let i = 0; i < n; i++) {
      const t = n === 1 ? d / 2 : d * (0.04 + (0.92 * i) / (n - 1))
      await new Promise<void>((ok) => {
        const done = setTimeout(ok, 4000) // a seek that never reports back cannot stall sampling
        video.onseeked = () => {
          clearTimeout(done)
          ok()
        }
        video.currentTime = t
      })
      frames.push(toFrame(video, video.videoWidth, video.videoHeight, t))
    }
    if (frames.every(isBlack)) throw new Error('The browser returned blank frames for this video.')
    return { frames, duration: d }
  } finally {
    URL.revokeObjectURL(src)
  }
}
