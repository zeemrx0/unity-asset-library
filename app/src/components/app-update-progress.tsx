import { useEffect, useState } from "react";
import { Button } from "./ui";
import "./app-update-progress.css";

const stages: Record<AppUpdateStatus["phase"], string> = {
  idle: "", checking: "Checking for updates…", downloading: "Downloading update…",
  extracting: "Extracting update…", verifying: "Verifying update…",
  ready: "Ready to install", installing: "Restarting to install…", failed: "Update failed",
};

export default function AppUpdateProgress() {
  const [status, setStatus] = useState<AppUpdateStatus | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  useEffect(() => {
    if (!window.ual?.getUpdateStatus) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const next = await window.ual.getUpdateStatus();
        if (!stopped) setStatus(next);
      } catch (error) {
        console.error("Could not read app update progress:", error);
      } finally {
        if (!stopped) timer = setTimeout(refresh, 500);
      }
    }
    void refresh();
    return () => { stopped = true; clearTimeout(timer); };
  }, []);

  if (!status || status.phase === "idle" || status.phase === "checking") return null;
  const terminal = status.phase === "ready" || status.phase === "failed";
  const percent = status.phase === "downloading" ? status.percent ?? undefined : status.phase === "ready" ? 100 : undefined;
  async function check() {
    setActionBusy(true);
    setActionError("");
    try { await window.ual.checkForUpdates(); }
    catch (error) { setActionError(error instanceof Error ? error.message : "Could not check for updates."); }
    finally { setActionBusy(false); }
  }
  return (
    <section className={`progress-panel app-update-progress${status.phase === "failed" ? " failed" : ""}`} aria-label="App update progress">
      <div className="progress-heading"><strong>App update{status.version ? ` · ${status.version}` : ""}</strong>{percent !== undefined && <span>{percent}%</span>}</div>
      <div className="progress-stage" role="status">{stages[status.phase]}</div>
      {status.phase !== "failed" && <div className="progress-track"><progress value={percent} max="100" aria-label="App update progress" />{percent === undefined && <span className="progress-indeterminate" aria-hidden="true" />}</div>}
      {status.phase === "ready" && <p className="progress-meta">Installation requires a confirmed restart.</p>}
      {(status.error || actionError) && <p className="progress-error" role="alert">{actionError || status.error}</p>}
      {terminal && <Button className="button quiet" isDisabled={actionBusy} onPress={() => { void check(); }}>{status.phase === "ready" ? "Restart and Install…" : "Retry update"}</Button>}
    </section>
  );
}
