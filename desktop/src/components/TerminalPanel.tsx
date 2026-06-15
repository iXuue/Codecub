import { useEffect, useRef, useState } from "react";
import type { I18nKey } from "../i18n";

type XTermInstance = import("@xterm/xterm").Terminal;

type TerminalPanelProps = {
  t: (key: I18nKey) => string;
  projectPath: string;
};

export function TerminalPanel({ t, projectPath }: TerminalPanelProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<XTermInstance | null>(null);
  const terminalIdRef = useRef(`terminal-${crypto.randomUUID()}`);
  const [running, setRunning] = useState(false);
  const [terminalError, setTerminalError] = useState("");

  useEffect(() => {
    const removeData = window.codecub.onTerminalData((terminalId, data) => {
      if (terminalId === terminalIdRef.current) {
        terminalRef.current?.write(data);
      }
    });
    const removeExit = window.codecub.onTerminalExit((event) => {
      if (event.terminalId === terminalIdRef.current) {
        setRunning(false);
      }
    });
    const removeError = window.codecub.onTerminalError((event) => {
      if (event.terminalId === terminalIdRef.current) {
        setRunning(false);
        setTerminalError(event.message);
        terminalRef.current?.dispose();
        terminalRef.current = null;
        containerRef.current?.replaceChildren();
      }
    });
    return () => {
      removeData();
      removeExit();
      removeError();
      void window.codecub.closeTerminal(terminalIdRef.current);
      terminalRef.current?.dispose();
      terminalRef.current = null;
    };
  }, []);

  async function startTerminal() {
    if (!containerRef.current || running) {
      return;
    }
    setTerminalError("");
    await import("@xterm/xterm/css/xterm.css");
    const { Terminal } = await import("@xterm/xterm");
    containerRef.current.replaceChildren();
    const terminal = new Terminal({ cols: 100, rows: 24, cursorBlink: true });
    terminal.open(containerRef.current);
    terminal.onData((data) => window.codecub.writeTerminal({ terminalId: terminalIdRef.current, data }));
    terminalRef.current = terminal;
    try {
      await window.codecub.startTerminal({
        terminalId: terminalIdRef.current,
        cwd: projectPath,
        cols: 100,
        rows: 24,
      });
      setRunning(true);
    } catch (error) {
      setTerminalError(error instanceof Error ? error.message : String(error));
      terminalRef.current?.dispose();
      terminalRef.current = null;
      containerRef.current?.replaceChildren();
      setRunning(false);
    }
  }

  async function closeTerminal() {
    await window.codecub.closeTerminal(terminalIdRef.current);
    terminalRef.current?.dispose();
    terminalRef.current = null;
    setRunning(false);
  }

  return (
    <section className="terminal-panel" aria-label={t("terminal")}>
      <div className="terminal-header">
        <span>{t("terminal")}</span>
        <button className="button secondary" type="button" onClick={running ? closeTerminal : startTerminal}>
          {running ? t("closeTerminal") : t("startTerminal")}
        </button>
      </div>
      <div className="terminal-surface">
        {terminalError ? <div className="terminal-error">{terminalError}</div> : null}
        {!running ? <div className="empty-state compact">{t("terminalNotStarted")}</div> : null}
        <div className="terminal-mount" ref={containerRef} />
      </div>
    </section>
  );
}
