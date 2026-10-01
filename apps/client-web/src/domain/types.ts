export interface Profile {
  id: string;
  display_name: string;
  device_limit: number;
  devices_used: number;
  expires_at: string | null;
}
export interface Device {
  id: string;
  name: string;
  enabled: boolean;
  platform: string | null;
}
export interface Server {
  id: string;
  name: string;
  location: string | null;
}
export type ProvisionState =
  | "pending"
  | "running"
  | "failed"
  | "ready"
  | "revoked";
export interface Provision {
  server_id: string;
  state: ProvisionState;
}
export interface Configuration {
  toml: string;
  deep_link: string;
}
export const stateLabel: Record<ProvisionState, string> = {
  pending: "В очереди",
  running: "Настраиваем подключение",
  failed: "Не удалось настроить. Повторите попытку.",
  ready: "Готово к подключению",
  revoked: "Доступ отозван",
};
