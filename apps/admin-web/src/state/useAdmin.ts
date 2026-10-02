import { useCallback, useEffect, useRef, useState } from "react";
import { AdminApi, ApiError, invitationLink } from "../api/admin";
import { useVisibility } from "./useVisibility";
import { useServerConfiguration } from "./useServerConfiguration";
import type {
  Credential,
  Device,
  Invitation,
  Job,
  LifecycleAction,
  LogEvent,
  Page,
  Principal,
  Server,
  ServerInput,
  ServerConfiguration,
  UserInput,
  VpnUser,
} from "../domain/types";

export function useAdmin() {
  const [api] = useState(() => new AdminApi());
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [restoring, setRestoring] = useState(true);
  const [page, setPage] = useState<Page>("Dashboard");
  const [servers, setServers] = useState<Server[]>([]);
  const [users, setUsers] = useState<VpnUser[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [jobOffset, setJobOffset] = useState(0);
  const [userId, setUserId] = useState("");
  const [devices, setDevices] = useState<Device[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [jobId, setJobId] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [logs, setLogs] = useState<LogEvent[]>([]);
  const [logError, setLogError] = useState("");
  const [invite, setInvite] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const visibility = useVisibility(api, principal, page, revision);
  const configuration = useServerConfiguration(api, principal, revision);
  const resetConfig = configuration.resetConfig;
  const session = useRef(0);
  const userSelection = useRef("");
  const mutation = useRef(false);
  const invitationGeneration = useRef(0);
  const reset = useCallback(() => {
    session.current++;
    invitationGeneration.current++;
    api.clear();
    setPrincipal(null);
    setServers([]);
    setUsers([]);
    setJobs([]);
    setDevices([]);
    setCredentials([]);
    setInvitations([]);
    setInvite("");
    setJob(null);
    setLogs([]);
    setUserId("");
    userSelection.current = "";
    setJobId("");
    setJobOffset(0);
    setNotice("");
    setLoading(false);
    setDetailsLoading(false);
    resetConfig();
  }, [api, resetConfig]);
  const report = useCallback((cause: unknown) => {
    setError(
      cause instanceof ApiError
        ? cause.message
        : "The service could not be reached. Please retry.",
    );
  }, []);
  useEffect(() => {
    let active = true;
    api.onUnauthorized = () => {
      if (active) {
        reset();
        setError("Your session ended. Sign in again.");
      }
    };
    void api
      .me()
      .then((me) => {
        if (active) {
          setPrincipal(me);
          setError("");
        }
      })
      .catch((cause) => {
        if (active && !(cause instanceof ApiError && cause.status === 401))
          report(cause);
      })
      .finally(() => {
        if (active) setRestoring(false);
      });
    return () => {
      active = false;
      api.onUnauthorized = () => {};
      api.clear();
    };
  }, [api, report, reset]);
  const refresh = useCallback(() => {
    setError("");
    setRevision((n) => n + 1);
  }, []);
  useEffect(() => {
    if (principal?.role !== "admin") {
      invitationGeneration.current++;
      setInvite("");
    }
  }, [principal?.role]);
  useEffect(() => {
    if (!principal?.id) return;
    let active = true;
    let fetching = false;
    const generation = session.current;
    const load = async () => {
      if (fetching) return;
      fetching = true;
      try {
        const [me, nodes, people, operations] = await Promise.all([
          api.me(),
          api.servers(),
          api.users(),
          api.jobs(jobOffset),
        ]);
        if (!active || generation !== session.current) return;
        setPrincipal(me);
        setServers(nodes);
        setUsers(people);
        setJobs(operations);
      } catch (cause) {
        if (active && generation === session.current) report(cause);
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
  }, [api, principal?.id, jobOffset, revision, report]); // Role is revalidated on every refresh.
  useEffect(() => {
    if (!principal?.id || !userId) return;
    let active = true;
    let fetching = false;
    const generation = session.current;
    const load = async () => {
      if (fetching) return;
      fetching = true;
      try {
        const [items, invites] = await Promise.all([
          api.devices(userId),
          api.invitations(userId),
        ]);
        const states = (
          await Promise.all(items.map((d) => api.credentials(userId, d.id)))
        ).flat();
        if (!active || generation !== session.current) return;
        setDevices(items);
        setCredentials(states);
        setInvitations(invites);
      } catch (cause) {
        if (active && generation === session.current) report(cause);
      } finally {
        fetching = false;
        if (active) setDetailsLoading(false);
      }
    };
    setDetailsLoading(true);
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [api, principal?.id, userId, revision, report]);
  useEffect(() => {
    if (!principal?.id || !jobId) return;
    let active = true;
    const controller = new AbortController();
    setLogError("");
    const load = async () => {
      try {
        const latest = await api.job(jobId);
        if (active) {
          setJob(latest);
          setLogs(latest.history);
        }
        return latest;
      } catch (cause) {
        if (active) report(cause);
        return null;
      }
    };
    void load().then((initial) => {
      if (!active || !initial) return;
      void api
        .logs(
          jobId,
          controller.signal,
          (entry) => {
            if (active)
              setLogs((items) =>
                [
                  ...items.filter((item) => item.sequence !== entry.sequence),
                  entry,
                ]
                  .sort((a, b) => a.sequence - b.sequence)
                  .slice(-500),
              );
          },
          initial.history.at(-1)?.sequence ?? 0,
        )
        .catch((cause) => {
          if (active && !controller.signal.aborted)
            setLogError(
              cause instanceof Error
                ? cause.message
                : "Log connection ended. Refresh to reconnect.",
            );
        });
    });
    const timer = window.setInterval(() => {
      void api
        .job(jobId)
        .then((latest) => {
          if (active) setJob(latest);
        })
        .catch((cause) => {
          if (active) report(cause);
        });
    }, 3000);
    return () => {
      active = false;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [api, principal?.id, jobId, revision, report]);
  async function login(username: string, password: string, otp: string) {
    setBusy(true);
    setError("");
    try {
      await api.login(username, password, otp);
      setPrincipal(await api.me());
      setPage("Dashboard");
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401)
        setError(
          "Sign-in failed. Check your password and use a fresh authenticator code.",
        );
      else report(cause);
    } finally {
      setBusy(false);
    }
  }
  async function logout() {
    setBusy(true);
    try {
      await api.logout();
      reset();
      setError("");
    } catch {
      reset();
      setError(
        "Sign-out could not be confirmed. Retry sign-in and sign-out when the service is available.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function act(
    operation: (api: AdminApi) => Promise<unknown>,
    message = "Change accepted. Background operations may still be running.",
  ) {
    if (principal?.role !== "admin" || mutation.current) return;
    mutation.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    const generation = session.current;
    try {
      await operation(api);
      if (generation === session.current) {
        refresh();
        setNotice(message);
      }
    } catch (cause) {
      if (generation === session.current) report(cause);
    } finally {
      mutation.current = false;
      setBusy(false);
    }
  }
  function selectUser(id: string) {
    invitationGeneration.current++;
    userSelection.current = id;
    setUserId(id);
    setDevices([]);
    setCredentials([]);
    setInvitations([]);
    setInvite("");
    setError("");
    setNotice("");
  }
  function navigate(next: Page) {
    invitationGeneration.current++;
    setPage(next);
    if (next === "Dashboard") setJobOffset(0);
    setInvite("");
    setError("");
    setNotice("");
    setJobId("");
    setJob(null);
    setLogs([]);
  }
  function selectJob(id: string) {
    if (id === jobId) return;
    setJob(null);
    setLogs([]);
    setJobId(id);
  }
  const user = users.find((item) => item.id === userId) ?? null;
  const issueInvitation = (lifetime: number) => {
    if (!userId) return;
    setInvite("");
    const selected = userId;
    const generation = ++invitationGeneration.current;
    return act(async (client) => {
      const issued = await client.invite(selected, lifetime);
      if (
        userSelection.current === selected &&
        generation === invitationGeneration.current
      )
        setInvite(invitationLink(issued.token));
    }, "Invitation created. Share the link privately; it is shown only once.");
  };
  const actions = {
    createConfigRevision: (
      id: string,
      config: ServerConfiguration,
      key: string,
    ) =>
      act(async (client) => {
        await client.createConfigRevision(id, config, key);
        configuration.pageConfig(0);
      }, "Validated revision created. Select Apply when ready."),
    applyConfigRevision: (id: string, configRevision: string, key: string) =>
      act(async (client) => {
        const operation = await client.applyConfigRevision(
          id,
          configRevision,
          key,
        );
        navigate("Jobs");
        selectJob(operation.id);
      }, "Configuration apply queued. Follow its result and any rollback here."),
    lifecycle: (
      id: string,
      kind: LifecycleAction,
      version?: string,
      email?: string,
    ) =>
      act(async (client) => {
        const operation = await client.lifecycle(id, kind, version, email);
        navigate("Jobs");
        selectJob(operation.id);
      }, "Lifecycle operation queued. Follow its progress and result here."),
    saveServer: (
      body: ServerInput,
      id?: string,
      host_key = "",
      private_key = "",
    ) =>
      act((client) =>
        id
          ? client.updateServer(id, body)
          : client.createServer({
              ...body,
              credentials: { host_key, private_key },
            }),
      ),
    serverEnabled: (node: Server) =>
      act((client) => client.serverEnabled(node.id, !node.enabled)),
    rotateSSH: (id: string, host_key: string, private_key: string) =>
      act((client) => client.rotateSSH(id, host_key, private_key)),
    diagnostic: (id: string, kind: "preflight" | "status") =>
      act(async (client) => {
        const operation = await client.diagnostic(id, kind);
        navigate("Jobs");
        selectJob(operation.id);
      }, "Diagnostic queued. Follow its progress and result here."),
    saveUser: (body: UserInput, id?: string) =>
      act(async (client) => {
        if (id) await client.updateUser(id, body);
        else {
          const created = await client.createUser(body);
          selectUser(created.id);
        }
      }),
    userEnabled: (person: VpnUser) =>
      act((client) => client.userEnabled(person.id, !person.enabled)),
    access: (id: string, mode: "selected" | "all", ids: string[]) =>
      act((client) => client.access(id, mode, ids)),
    createDevice: (id: string, name: string, platform: string) =>
      act((client) => client.createDevice(id, name, platform)),
    revokeDevice: (user: string, device: string) =>
      act((client) => client.revokeDevice(user, device)),
    credential: (
      user: string,
      device: string,
      server: string,
      revoke = false,
    ) => act((client) => client.credential(user, device, server, revoke)),
    revokeInvite: (user: string, id: string) =>
      act((client) => client.revokeInvite(user, id)),
    cancel: (id: string) =>
      act(
        (client) => client.cancel(id),
        "Cancellation requested. Running jobs stop at a safe checkpoint.",
      ),
  };
  return {
    ...visibility,
    ...configuration,
    principal,
    restoring,
    page,
    servers,
    users,
    jobs,
    jobOffset,
    user,
    userId,
    devices,
    credentials,
    invitations,
    job,
    logs,
    logError,
    invite,
    error,
    notice,
    busy,
    loading,
    detailsLoading,
    login,
    logout,
    refresh,
    actions,
    navigate,
    selectUser,
    selectJob,
    setJobOffset,
    issueInvitation,
    dismissInvite: () => {
      invitationGeneration.current++;
      setInvite("");
    },
  };
}
export type AdminState = ReturnType<typeof useAdmin>;
