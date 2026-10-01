import * as Dialog from "@radix-ui/react-alert-dialog";
import { Button } from "./ui/button";
export function ConfirmAction({
  label,
  description,
  busy,
  onConfirm,
}: {
  label: string;
  description: string;
  busy: boolean;
  onConfirm: () => void;
}) {
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>
        <Button variant="destructive" disabled={busy}>
          {label}
        </Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="dialog">
          <Dialog.Title>{label}?</Dialog.Title>
          <Dialog.Description>{description}</Dialog.Description>
          <div className="actions">
            <Dialog.Cancel asChild>
              <Button variant="outline">Keep unchanged</Button>
            </Dialog.Cancel>
            <Dialog.Action asChild>
              <Button variant="destructive" onClick={onConfirm}>
                Confirm
              </Button>
            </Dialog.Action>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
