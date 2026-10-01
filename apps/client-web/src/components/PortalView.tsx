import { useState } from "react";
import type { usePortal } from "../state/usePortal";
import { stateLabel } from "../domain/types";
import { Button } from "./ui/button";
import { ConfirmRevoke } from "./ConfirmRevoke";
import { ConfigurationDelivery } from "./ConfigurationDelivery";
type Portal = ReturnType<typeof usePortal>;
export function PortalView({ portal: p }: { portal: Portal }) {
  const [name, setName] = useState("");
  const [platform, setPlatform] = useState("");
  const selected = p.devices.find((x) => x.id === p.device);
  const state = p.states.find((x) => x.server_id === p.server)?.state;
  return (
    <main>
      <header>
        <div className="brand">TrustTunnel</div>
        <h1>Ваше VPN-подключение</h1>
        <p>
          Добавьте устройство, выберите сервер и откройте подключение в
          приложении.
        </p>
      </header>
      {p.error && (
        <div className="error" role="alert">
          {p.error}
          {p.authenticated && (
            <Button
              variant="outline"
              disabled={p.busy}
              onClick={() => void p.retry()}
            >
              Обновить
            </Button>
          )}
        </div>
      )}
      {p.busy && <p role="status">Пожалуйста, подождите…</p>}
      {!p.authenticated ? (
        <section>
          <h2>Вход по приглашению</h2>
          <p>
            Откройте личную ссылку от администратора. Приглашение действует один
            раз. После выхода или окончания сессии запросите новое приглашение.
          </p>
        </section>
      ) : (
        <>
          <section>
            <div className="actions">
              <h2>{p.profile?.display_name ?? "Загрузка профиля…"}</h2>
              <Button
                variant="outline"
                onClick={() => void p.logout()}
                disabled={p.busy}
              >
                Выйти
              </Button>
            </div>
            {p.profile && (
              <>
                <p>
                  Устройства: {p.profile.devices_used} из{" "}
                  {p.profile.device_limit}
                </p>
                <p>
                  Доступ:{" "}
                  {p.profile.expires_at
                    ? "до " + new Date(p.profile.expires_at).toLocaleString()
                    : "без срока окончания"}
                </p>
              </>
            )}
          </section>
          <section>
            <h2>1. Устройство</h2>
            {!p.devices.length && <p>Добавьте первое устройство.</p>}
            <label>
              Ваше устройство
              <select
                value={p.device}
                onChange={(e) => p.selectDevice(e.target.value)}
                disabled={p.busy}
              >
                <option value="">Выберите устройство</option>
                {p.devices
                  .filter((x) => x.enabled)
                  .map((x) => (
                    <option key={x.id} value={x.id}>
                      {x.name}
                    </option>
                  ))}
              </select>
            </label>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void p.createDevice(name.trim(), platform);
              }}
            >
              <label>
                Название нового устройства
                <input
                  value={name}
                  maxLength={255}
                  required
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Например, мой телефон"
                />
              </label>
              <label>
                Платформа
                <select
                  value={platform}
                  onChange={(e) => setPlatform(e.target.value)}
                >
                  <option value="">Не указана</option>
                  <option>Android</option>
                  <option>iOS</option>
                  <option>Windows</option>
                  <option>macOS</option>
                  <option>Linux</option>
                </select>
              </label>
              <Button
                disabled={
                  p.busy ||
                  !p.profile ||
                  p.profile.devices_used >= p.profile.device_limit ||
                  !name.trim()
                }
              >
                Добавить устройство
              </Button>
            </form>
            {p.profile && p.profile.devices_used >= p.profile.device_limit && (
              <p>Квота заполнена. Отзовите неиспользуемое устройство.</p>
            )}
            {selected && (
              <ConfirmRevoke
                name={selected.name}
                busy={p.busy}
                onConfirm={() => void p.revokeDevice()}
              />
            )}
            {p.devices
              .filter((x) => !x.enabled)
              .map((x) => (
                <p key={x.id}>{x.name} — отозвано</p>
              ))}
          </section>
          <section>
            <h2>2. Сервер</h2>
            {!p.servers.length ? (
              <p>Доступных серверов пока нет. Обратитесь к администратору.</p>
            ) : (
              <label>
                Сервер подключения
                <select
                  value={p.server}
                  onChange={(e) => p.selectServer(e.target.value)}
                  disabled={p.busy}
                >
                  <option value="">Выберите сервер</option>
                  {p.servers.map((x) => (
                    <option key={x.id} value={x.id}>
                      {x.name}
                      {x.location ? " · " + x.location : ""}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </section>
          <section>
            <h2>3. Подключение</h2>
            <p role="status">
              {state ? stateLabel[state] : "Выберите устройство и сервер."}
            </p>
            {state === "ready" ? (
              <Button onClick={() => void p.deliver()} disabled={p.busy}>
                Получить конфигурацию
              </Button>
            ) : (
              <Button
                onClick={() => void p.provision()}
                disabled={
                  p.busy ||
                  !p.device ||
                  !p.server ||
                  state === "pending" ||
                  state === "running"
                }
              >
                {state === "failed"
                  ? "Повторить настройку"
                  : "Настроить подключение"}
              </Button>
            )}
            {p.configuration && (
              <ConfigurationDelivery
                key={p.device + p.server}
                configuration={p.configuration}
              />
            )}
          </section>
        </>
      )}
      <footer>TrustTunnel · личный кабинет</footer>
    </main>
  );
}
