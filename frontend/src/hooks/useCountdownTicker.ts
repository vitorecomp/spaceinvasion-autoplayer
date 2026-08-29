import { useEffect, useState } from "react";

export function useCountdownTicker(intervalMs = 1000): number {
  const [nowSec, setNowSec] = useState<number>(() =>
    typeof window === "undefined" ? 0 : Math.floor(Date.now() / 1000)
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") {
        setNowSec(Math.floor(Date.now() / 1000));
      }
    }, intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);

  return nowSec;
}
