import { useEffect, useRef } from 'react'

export function useDragAutoScroll({ edgePx = 120, maxSpeed = 18 } = {}) {
  const position = useRef<number | null>(null)
  const scrollElement = useRef<HTMLElement | null>(null)
  const animation = useRef<number | null>(null)
  const dragging = useRef(false)

  useEffect(() => {
    const findScrollParent = (element: HTMLElement | null): HTMLElement => {
      if (!element || element === document.documentElement) return document.documentElement
      const { overflowY } = window.getComputedStyle(element)
      return (overflowY === 'auto' || overflowY === 'scroll') && element.scrollHeight > element.clientHeight
        ? element
        : findScrollParent(element.parentElement)
    }
    const tick = () => {
      const element = scrollElement.current
      if (position.current !== null && element) {
        const rect = element.getBoundingClientRect()
        const relativeY = position.current - rect.top
        let speed = 0
        if (relativeY < edgePx) speed = -maxSpeed * (1 - relativeY / edgePx)
        else if (relativeY > rect.height - edgePx) speed = maxSpeed * ((relativeY - (rect.height - edgePx)) / edgePx)
        element.scrollTop += speed
      }
      animation.current = requestAnimationFrame(tick)
    }
    const start = (event: DragEvent) => {
      dragging.current = true
      scrollElement.current = findScrollParent(event.target as HTMLElement)
      animation.current = requestAnimationFrame(tick)
    }
    const stop = () => {
      dragging.current = false
      if (animation.current) cancelAnimationFrame(animation.current)
      animation.current = null
      position.current = null
    }
    const drag = (event: DragEvent) => { position.current = event.clientY }
    const wheel = (event: WheelEvent) => {
      if (!dragging.current || !scrollElement.current) return
      scrollElement.current.scrollTop += event.deltaY
      event.preventDefault()
    }
    document.addEventListener('dragstart', start, true)
    document.addEventListener('dragover', drag)
    document.addEventListener('dragend', stop)
    document.addEventListener('drop', stop)
    document.addEventListener('wheel', wheel, { passive: false, capture: true })
    return () => {
      document.removeEventListener('dragstart', start, true)
      document.removeEventListener('dragover', drag)
      document.removeEventListener('dragend', stop)
      document.removeEventListener('drop', stop)
      document.removeEventListener('wheel', wheel, { capture: true })
      if (animation.current) cancelAnimationFrame(animation.current)
    }
  }, [edgePx, maxSpeed])
}
