import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, ClientApi } from "../api/client";
import type {
  Configuration,
  Device,
  Profile,
  Provision,
  Server,
} from "../domain/types";
export function usePortal(invitation: string | null) {
  const api = useRef(new ClientApi()).current;
  const started = useRef(false);
  const epoch = useRef(0);
  const [authenticated, setAuthenticated] = useState(false);
  const [profile, setProfile] = useState<Profile>();
  const [devices, setDevices] = useState<Device[]>([]);
  const [servers, setServers] = useState<Server[]>([]);
  const [device, setDevice] = useState("");
  const [server, setServer] = useState("");
  const [states, setStates] = useState<Provision[]>([]);
  const [configuration, setConfiguration] = useState<Configuration>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const clear = useCallback(() => {
    epoch.current++;
    setAuthenticated(false);
    setProfile(undefined);
    setDevices([]);
    setServers([]);
    setStates([]);
    setConfiguration(undefined);
    setDevice("");
    setServer("");
  }, []);
  api.onUnauthorized = clear;
  const refresh = useCallback(async () => {
    const current = epoch.current;
    const [p, d, s] = await Promise.all([
      api.profile(),
      api.devices(),
      api.servers(),
    ]);
    if (current !== epoch.current) return;
    setProfile(p);
    setDevices(d);
    setServers(s);
    setDevice((old) =>
      d.some((x) => x.id === old && x.enabled)
        ? old
        : (d.find((x) => x.enabled)?.id ?? ""),
    );
    setServer((old) => (s.some((x) => x.id === old) ? old : (s[0]?.id ?? "")));
  }, [api]);
  const action = useCallback(async (operation: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await operation();
    } catch (e) {
      setConfiguration(undefined);
      setError(
        e instanceof ApiError
          ? e.message
          : "Сеть недоступна. Повторите попытку.",
      );
    } finally {
      setBusy(false);
    }
  }, []);
  const expiryTimer = useRef<number>(undefined);
  const resume = useCallback(
    async (invite: string | null) => {
      let expires: string;
      try {
        expires = invite ? await api.exchange(invite) : await api.session();
      } catch (e) {
        if (!invite && e instanceof ApiError && e.status === 401) return;
        throw e;
      }
      setAuthenticated(true);
      window.clearTimeout(expiryTimer.current);
      const delay = Math.max(0, new Date(expires).getTime() - Date.now());
      expiryTimer.current = window.setTimeout(() => {
        api.clear();
        clear();
        setError("Сессия истекла. Откройте новое приглашение.");
      }, delay);
      await refresh();
    },
    [api, clear, refresh],
  );
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void action(() => resume(invitation));
  }, [invitation, action, resume]);
  useEffect(() => {
    const hide = () => {
      if (document.visibilityState === "hidden") setConfiguration(undefined);
    };
    const leave = () => {
      api.clear();
      clear();
      window.clearTimeout(expiryTimer.current);
    };
    const restore = (event: PageTransitionEvent) => {
      if (event.persisted) void action(() => resume(null));
    };
    window.addEventListener("pageshow", restore);
    document.addEventListener("visibilitychange", hide);
    window.addEventListener("pagehide", leave);
    return () => {
      document.removeEventListener("visibilitychange", hide);
      window.removeEventListener("pagehide", leave);
      window.removeEventListener("pageshow", restore);
    };
  }, [api, clear, action, resume]);
  useEffect(
    () => () => {
      window.clearTimeout(expiryTimer.current);
      api.clear();
    },
    [api],
  );
  useEffect(() => {
    setConfiguration(undefined);
    setStates([]);
  }, [device, server]);
  useEffect(() => {
    if (!authenticated || !device) return;
    let alive = true;
    let timer: number;
    async function poll() {
      try {
        const value = await api.states(device);
        await refresh();
        if (alive) {
          setStates(value);
          if (!value.some((x) => x.server_id === server && x.state === "ready"))
            setConfiguration(undefined);
        }
      } catch (e) {
        if (alive) {
          setConfiguration(undefined);
          setStates([]);
          setError(
            e instanceof ApiError
              ? e.message
              : "Не удалось обновить состояние. Повторите попытку.",
          );
        }
      }
      if (alive) timer = window.setTimeout(poll, 2500);
    }
    void poll();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [api, authenticated, device, server, refresh]);
  const selectDevice = (id: string) => {
    epoch.current++;
    setConfiguration(undefined);
    setStates([]);
    setDevice(id);
  };
  const selectServer = (id: string) => {
    epoch.current++;
    setConfiguration(undefined);
    setStates([]);
    setServer(id);
  };
  return {
    authenticated,
    profile,
    devices,
    servers,
    device,
    server,
    states,
    configuration,
    busy,
    error,
    selectDevice,
    selectServer,
    retry: () => action(refresh),
    createDevice: (name: string, platform: string) =>
      action(async () => {
        const d = await api.createDevice(name, platform);
        await refresh();
        selectDevice(d.id);
      }),
    revokeDevice: () =>
      action(async () => {
        setConfiguration(undefined);
        await api.revokeDevice(device);
        await refresh();
      }),
    provision: () =>
      action(async () => {
        setConfiguration(undefined);
        await api.provision(device, server);
        setStates(await api.states(device));
      }),
    deliver: () =>
      action(async () => {
        const current = epoch.current;
        const c = await api.configuration(device, server);
        if (current === epoch.current) setConfiguration(c);
      }),
    logout: () =>
      action(async () => {
        clear();
        window.clearTimeout(expiryTimer.current);
        await api.logout();
      }),
  };
}
