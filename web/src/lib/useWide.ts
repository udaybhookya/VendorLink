import { useEffect, useState } from "react";

/** Laptop-sized screen: the vendor app switches from phone tabs to a desktop layout. */
export const WIDE_QUERY = "(min-width: 1024px)";

export function useWide(): boolean {
  const [wide, setWide] = useState(() => typeof window !== "undefined" && window.matchMedia(WIDE_QUERY).matches);
  useEffect(() => {
    const mq = window.matchMedia(WIDE_QUERY);
    const on = () => setWide(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return wide;
}
