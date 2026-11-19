import { useEffect, useState } from "react";

/** Update relative periods without reading the clock during rendering. */
export function useDashboardClock() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 60000);
    return () => clearInterval(timer);
  }, []);
  return now;
}
