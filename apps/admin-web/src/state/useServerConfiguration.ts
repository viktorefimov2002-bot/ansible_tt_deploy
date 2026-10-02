import { useCallback, useEffect, useRef, useState } from "react";
import { AdminApi, ApiError } from "../api/admin";
import type { ConfigRevision, Principal } from "../domain/types";

export function useServerConfiguration(
  api: AdminApi,
  principal: Principal | null,
  refresh: number,
) {
  const [serverId, setServerId] = useState("");
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<ConfigRevision[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const selection = useRef("");
  const select = useCallback((id: string) => {
    selection.current = id;
    setServerId(id);
    setOffset(0);
    setRows([]);
    setError("");
  }, []);
  const reset = useCallback(() => select(""), [select]);
  useEffect(() => {
    if (!principal?.id || !serverId) return;
    let active = true;
    let fetching = false;
    const load = async () => {
      if (fetching) return;
      fetching = true;
      try {
        const revisions = await api.configRevisions(serverId, offset);
        if (active && selection.current === serverId) {
          setRows(revisions);
          setError("");
        }
      } catch (cause) {
        if (active && selection.current === serverId)
          setError(
            cause instanceof ApiError
              ? cause.message
              : "Configuration history could not be loaded. Refresh to retry.",
          );
      } finally {
        fetching = false;
        if (active) setLoading(false);
      }
    };
    setLoading(true);
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [api, principal?.id, serverId, offset, refresh]);
  return {
    configServerId: serverId,
    configRevisions: rows,
    configOffset: offset,
    configLoading: loading,
    configError: error,
    selectConfigServer: select,
    resetConfig: reset,
    pageConfig: (next: number) => {
      setRows([]);
      setOffset(next);
    },
  };
}
