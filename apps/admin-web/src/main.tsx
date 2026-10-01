import { createRoot } from "react-dom/client";
import { AdminView } from "./components/AdminView";
import { useAdmin } from "./state/useAdmin";
import "./styles.css";
function App() {
  return <AdminView admin={useAdmin()} />;
}
createRoot(document.getElementById("root")!).render(<App />);
