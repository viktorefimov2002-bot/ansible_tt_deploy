import { createRoot } from "react-dom/client";
import { consumeInvitation } from "./api/client";
import { usePortal } from "./state/usePortal";
import { PortalView } from "./components/PortalView";
import "./styles.css";
const invitation = consumeInvitation();
function App() {
  const portal = usePortal(invitation);
  return <PortalView portal={portal} />;
}
createRoot(document.getElementById("root")!).render(<App />);
