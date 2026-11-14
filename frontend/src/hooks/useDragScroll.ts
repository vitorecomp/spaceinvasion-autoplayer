import { useCallback, useEffect, useRef, useState } from "react";
import { safeFiniteNumber } from "../api/httpClient";

export interface DragScrollHandlers {
  isDragging: boolean;
  wasDraggedRef: React.MutableRefObject<boolean>;
  consumeSuppressClick: () => boolean;
  onMouseDown: (e: React.MouseEvent<HTMLElement>) => void;
  onMouseMove: (e: React.MouseEvent<HTMLElement>) => void;
  onMouseUp: () => void;
  onMouseLeave: () => void;
}

export function useDragScroll(
  scrollRef: React.RefObject<HTMLElement | null>,
  dragThresholdPx = 4
): DragScrollHandlers {
  const [isDragging, setIsDragging] = useState(false);
  const activeRef = useRef(false);
  const wasDraggedRef = useRef(false);
  const suppressNextClickRef = useRef(false);
  const startXRef = useRef(0);
  const startYRef = useRef(0);
  const startScrollLeftRef = useRef(0);
  const startScrollTopRef = useRef(0);

  const endDrag = useCallback(() => {
    if (!activeRef.current && !isDragging) return;
    const didDrag = wasDraggedRef.current;
    activeRef.current = false;
    wasDraggedRef.current = false;
    setIsDragging(false);
    if (didDrag) {
      suppressNextClickRef.current = true;
      setTimeout(() => {
        suppressNextClickRef.current = false;
      }, 0);
    } else {
      suppressNextClickRef.current = false;
    }
  }, [isDragging]);

  const onMouseDown = useCallback(
    (e: React.MouseEvent<HTMLElement>) => {
      if (e.button !== undefined && e.button !== 0) return;
      const el = scrollRef.current;
      if (!el) return;
      activeRef.current = true;
      wasDraggedRef.current = false;
      suppressNextClickRef.current = false;
      startXRef.current = safeFiniteNumber(e.clientX, 0) ?? 0;
      startYRef.current = safeFiniteNumber(e.clientY, 0) ?? 0;
      startScrollLeftRef.current = safeFiniteNumber(el.scrollLeft, 0) ?? 0;
      startScrollTopRef.current = safeFiniteNumber(el.scrollTop, 0) ?? 0;
    },
    [scrollRef]
  );

  const applyMove = useCallback(
    (clientX: number, clientY: number) => {
      if (!activeRef.current) return;
      const el = scrollRef.current;
      if (!el) return;
      const dx = clientX - startXRef.current;
      const dy = clientY - startYRef.current;
      if (!wasDraggedRef.current) {
        if (Math.hypot(dx, dy) <= dragThresholdPx) {
          return;
        }
        wasDraggedRef.current = true;
        setIsDragging(true);
      }
      el.scrollLeft = Math.max(0, startScrollLeftRef.current - dx);
      el.scrollTop = Math.max(0, startScrollTopRef.current - dy);
    },
    [dragThresholdPx, scrollRef]
  );

  const onMouseMove = useCallback(
    (e: React.MouseEvent<HTMLElement>) => {
      if (!activeRef.current) return;
      const cx = safeFiniteNumber(e.clientX, 0) ?? 0;
      const cy = safeFiniteNumber(e.clientY, 0) ?? 0;
      applyMove(cx, cy);
    },
    [applyMove]
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    const handleWinUp = () => endDrag();
    const handleWinBlur = () => endDrag();
    window.addEventListener("mouseup", handleWinUp);
    window.addEventListener("blur", handleWinBlur);
    return () => {
      window.removeEventListener("mouseup", handleWinUp);
      window.removeEventListener("blur", handleWinBlur);
    };
  }, [endDrag]);

  const consumeSuppressClick = useCallback(() => {
    if (suppressNextClickRef.current) {
      suppressNextClickRef.current = false;
      return true;
    }
    return false;
  }, []);

  return {
    isDragging,
    wasDraggedRef,
    consumeSuppressClick,
    onMouseDown,
    onMouseMove,
    onMouseUp: endDrag,
    onMouseLeave: endDrag,
  };
}
