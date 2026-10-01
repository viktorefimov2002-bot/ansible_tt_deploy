import * as Dialog from "@radix-ui/react-alert-dialog";
import { Button } from "./ui/button";
export function ConfirmRevoke({
  name,
  busy,
  onConfirm,
}: {
  name: string;
  busy: boolean;
  onConfirm: () => void;
}) {
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>
        <Button variant="destructive" disabled={busy}>
          Отозвать устройство
        </Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="dialog">
          <Dialog.Title>Отозвать «{name}»?</Dialog.Title>
          <Dialog.Description>
            Все подключения этого устройства будут отключены. Другие устройства
            продолжат работать.
          </Dialog.Description>
          <div className="actions">
            <Dialog.Cancel asChild>
              <Button variant="outline">Отмена</Button>
            </Dialog.Cancel>
            <Dialog.Action asChild>
              <Button variant="destructive" onClick={onConfirm}>
                Да, отозвать
              </Button>
            </Dialog.Action>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
