import { useEffect, useRef, useState } from "react";
import { AdminApi, ApiError } from "../api/admin";
import type {
  AuditEvent,
  AuditFilters,
  ListPage,
  Monitoring,
  MonitoringWindow,
  NotificationPage,
  Page,
  Principal,
} from "../domain/types";

const message = (cause: unknown) =>
  cause instanceof ApiError
    ? cause.message
    : "The service could not be reached. Please retry.";

export function useVisibility(
  api: AdminApi,
  principal: Principal | null,
  page: Page,
  revision: number,
) {
  const identity = principal ? `${principal.id}:${principal.session_id}` : "";
  const currentIdentity = useRef(identity);
  currentIdentity.current = identity;
  const [audit, setAudit] = useState<ListPage<AuditEvent> | null>(null);
  const [auditFilters, setAuditFilters] = useState<AuditFilters>({});
  const [auditOffset, setAuditOffset] = useState(0);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditError, setAuditError] = useState("");
  const [notifications, setNotifications] = useState<NotificationPage | null>(
    null,
  );
  const [notificationOffset, setNotificationOffset] = useState(0);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [notificationsLoading, setNotificationsLoading] = useState(false);
  const [notificationError, setNotificationError] = useState("");
  const [notificationBusy, setNotificationBusy] = useState(false);
  const [notificationRevision, setNotificationRevision] = useState(0);
  const notificationMutation = useRef({ generation: 0, pending: false });
  const [monitoring, setMonitoring] = useState<Monitoring | null>(null);
  const [monitoringWindow, setMonitoringWindow] =
    useState<MonitoringWindow>("1h");
  const [monitoringLoading, setMonitoringLoading] = useState(false);
  const [monitoringError, setMonitoringError] = useState("");

  useEffect(() => {
    setAudit(null);
    setAuditFilters({});
    setAuditOffset(0);
    setAuditLoading(false);
    setAuditError("");
    setNotifications(null);
    setNotificationOffset(0);
    setUnreadOnly(false);
    setNotificationsLoading(false);
    setNotificationError("");
    setNotificationBusy(false);
    setMonitoring(null);
    setMonitoringWindow("1h");
    setMonitoringLoading(false);
    setMonitoringError("");
    const lifetime = notificationMutation.current;
    lifetime.generation++;
    lifetime.pending = false;
    return () => {
      lifetime.generation++;
      lifetime.pending = false;
    };
  }, [identity]);

  useEffect(() => {
    if (!identity || page !== "Audit") return;
    let active = true;
    let fetching = false;
    const load = async () => {
      if (fetching) return;
      fetching = true;
      try {
        const result = await api.audit(auditOffset, auditFilters);
        if (active && currentIdentity.current === identity) {
          setAudit(result);
          setAuditError("");
        }
      } catch (cause) {
        if (active && currentIdentity.current === identity)
          setAuditError(message(cause));
      } finally {
        fetching = false;
        if (active) setAuditLoading(false);
      }
    };
    setAudit(null);
    setAuditError("");
    setAuditLoading(true);
    void load();
    const timer = window.setInterval(() => void load(), 30000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [api, identity, page, auditOffset, auditFilters, revision]);

  useEffect(() => {
    if (!identity) return;
    let active = true;
    let fetching = false;
    const load = async () => {
      if (fetching) return;
      fetching = true;
      try {
        const result = await api.notifications(notificationOffset, unreadOnly);
        if (active && currentIdentity.current === identity) {
          setNotifications(result);
          setNotificationError("");
          if (
            !result.items.length &&
            result.total > 0 &&
            notificationOffset > 0
          )
            setNotificationOffset(
              Math.max(0, Math.floor((result.total - 1) / 50) * 50),
            );
        }
      } catch (cause) {
        if (active && currentIdentity.current === identity)
          setNotificationError(message(cause));
      } finally {
        fetching = false;
        if (active) setNotificationsLoading(false);
      }
    };
    setNotifications(null);
    setNotificationError("");
    setNotificationsLoading(true);
    void load();
    const timer = window.setInterval(() => void load(), 30000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [
    api,
    identity,
    notificationOffset,
    unreadOnly,
    revision,
    notificationRevision,
  ]);

  useEffect(() => {
    if (!identity || page !== "Monitoring") return;
    let active = true;
    let fetching = false;
    const load = async () => {
      if (fetching) return;
      fetching = true;
      try {
        const result = await api.monitoring(monitoringWindow);
        if (active && currentIdentity.current === identity) {
          setMonitoring(result);
          setMonitoringError("");
        }
      } catch (cause) {
        if (active && currentIdentity.current === identity)
          setMonitoringError(message(cause));
      } finally {
        fetching = false;
        if (active) setMonitoringLoading(false);
      }
    };
    setMonitoring(null);
    setMonitoringError("");
    setMonitoringLoading(true);
    void load();
    const timer = window.setInterval(() => void load(), 30000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [api, identity, page, monitoringWindow, revision]);

  async function markNotification(id: string, read: boolean) {
    const lifetime = notificationMutation.current;
    if (principal?.role !== "admin" || lifetime.pending) return;
    const generation = ++lifetime.generation;
    lifetime.pending = true;
    setNotificationBusy(true);
    setNotificationError("");
    try {
      await api.notificationRead(id, read);
      if (
        currentIdentity.current === identity &&
        generation === lifetime.generation
      )
        setNotificationRevision((value) => value + 1);
    } catch (cause) {
      if (
        currentIdentity.current === identity &&
        generation === lifetime.generation
      )
        setNotificationError(message(cause));
    } finally {
      if (
        currentIdentity.current === identity &&
        generation === lifetime.generation
      ) {
        lifetime.pending = false;
        setNotificationBusy(false);
      }
    }
  }

  return {
    audit,
    auditFilters,
    auditOffset,
    auditLoading,
    auditError,
    setAuditOffset,
    applyAuditFilters: (filters: AuditFilters) => {
      setAuditOffset(0);
      setAuditFilters(filters);
    },
    notifications,
    notificationOffset,
    unreadOnly,
    notificationsLoading,
    notificationError,
    notificationBusy,
    setNotificationOffset,
    filterNotifications: (unread: boolean) => {
      setNotificationOffset(0);
      setUnreadOnly(unread);
    },
    markNotification,
    monitoring,
    monitoringWindow,
    monitoringLoading,
    monitoringError,
    setMonitoringWindow,
  };
}
