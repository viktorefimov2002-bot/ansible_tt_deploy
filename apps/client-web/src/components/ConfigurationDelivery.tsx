import { useEffect, useState } from "react";
import QRCode from "qrcode";
import type { Configuration } from "../domain/types";
import { Button } from "./ui/button";
export function ConfigurationDelivery({
  configuration,
}: {
  configuration: Configuration;
}) {
  const [qr, setQr] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => {
    let alive = true;
    setQr("");
    QRCode.toDataURL(configuration.deep_link, { width: 320, margin: 2 })
      .then((value) => {
        if (alive) setQr(value);
      })
      .catch(() => {
        if (alive)
          setMessage("QR слишком большой. Используйте файл или ссылку.");
      });
    return () => {
      alive = false;
    };
  }, [configuration]);
  function download() {
    const url = URL.createObjectURL(
      new Blob([configuration.toml], { type: "application/toml" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "trusttunnel.toml";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(configuration.toml);
      setMessage("Конфигурация скопирована.");
    } catch {
      setMessage("Копирование недоступно. Скачайте файл.");
    }
  }
  return (
    <section aria-label="Конфигурация">
      <Button asChild>
        <a href={configuration.deep_link}>Открыть в TrustTunnel</a>
      </Button>
      <p>
        Установите{" "}
        <a
          href="https://github.com/TrustTunnel/TrustTunnel#clients"
          target="_blank"
          rel="noreferrer"
        >
          приложение TrustTunnel
        </a>
        , затем откройте подключение.
      </p>
      <details>
        <summary>Другие способы подключения</summary>
        <div className="actions">
          <Button variant="outline" onClick={download}>
            Скачать TOML
          </Button>
          <Button variant="outline" onClick={() => void copy()}>
            Копировать конфигурацию
          </Button>
        </div>
        {qr && (
          <img className="qr" src={qr} alt="QR-код подключения TrustTunnel" />
        )}
        <p>QR-код и файл содержат секрет подключения. Храните их у себя.</p>
      </details>
      <p role="status">{message}</p>
    </section>
  );
}
