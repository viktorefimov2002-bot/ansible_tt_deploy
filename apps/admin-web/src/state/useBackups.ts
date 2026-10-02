import { useEffect, useRef, useState } from "react";
import { AdminApi, ApiError } from "../api/admin";
import type { BackupHistory, Page, Principal } from "../domain/types";

export function useBackups(
  api: AdminApi,
  principal: Principal | null,
  page: Page,
  revision: number,
) {
  const identity = principal ? `${principal.id}:${principal.session_id}` : "";
  const currentIdentity = useRef(identity);
  currentIdentity.current = identity;
  const [backups, setBackups] = useState<BackupHistory | null>(null);
  const [backupOffset, setBackupOffset] = useState(0);
  const [backupsLoading, setBackupsLoading] = useState(false);
  const [backupsError, setBackupsError] = useState("");

  useEffect(() => {
    setBackups(null);
    setBackupOffset(0);
    setBackupsError("");
  }, [identity]);

  useEffect(() => {
    if (!identity || page !== "Backups") return;
    let active = true;
    let fetching = false;
    const load = async () => {
      if (fetching) return;
      fetching = true;
      try {
        const result = await api.backups(backupOffset);
        if (active && currentIdentity.current === identity) {
          setBackups(result);
          setBackupsError("");
        }
      } catch (cause) {
        if (active && currentIdentity.current === identity)
          setBackupsError(
            cause instanceof ApiError
              ? cause.message
              : "Backups could not be loaded. Please retry.",
          );
      } finally {
        fetching = false;
        if (active) setBackupsLoading(false);
      }
    };
    setBackups(null);
    setBackupsError("");
    setBackupsLoading(true);
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [api, identity, page, revision, backupOffset]);

  return {
    backups,
    backupOffset,
    setBackupOffset,
    backupsLoading,
    backupsError,
  };
}
