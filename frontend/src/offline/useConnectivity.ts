import { useEffect, useState } from "react";
import { isOnline, startConnectivityMonitor, subscribeConnectivity } from "./connectivity";

/** React view of the probe-backed connectivity state (see ./connectivity). */
export function useConnectivity(): boolean {
  const [online, setOnline] = useState(isOnline);
  useEffect(() => {
    const unsubscribe = subscribeConnectivity(setOnline);
    const stop = startConnectivityMonitor();
    setOnline(isOnline());
    return () => {
      unsubscribe();
      stop();
    };
  }, []);
  return online;
}
