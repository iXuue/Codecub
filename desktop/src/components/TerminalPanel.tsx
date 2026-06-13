import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useEffect, useRef, useState } from "react";
import type { I18nKey } from "../i18n";

type TerminalPanelProps = {
  t: (key: I18nKey) => string;
  projectPath: string;
};

export function TerminalPanel({ t, projectPath }: TerminalPanelProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const terminalIdRef = useRef(`terminal-${crypto.randomUUID()}`);
  const [running, setRunning] = useState(false);

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
    return () => {
      removeData();
      removeExit();
      void window.codecub.closeTerminal(terminalIdRef.current);
      terminalRef.current?.dispose();
      terminalRef.current = null;
    };
  }, []);

  async function startTerminal() {
    if (!containerRef.current || running) {
      return;
    }
    containerRef.current.replaceChildren();
    const terminal = new Terminal({ cols: 100, rows: 24, cursorBlink: true });
    terminal.open(containerRef.current);
    terminal.onData((data) => window.codecub.writeTerminal({ terminalId: terminalIdRef.current, data }));
    terminalRef.current = terminal;
    await window.codecub.startTerminal({
      terminalId: terminalIdRef.current,
      cwd: projectPath,
      cols: 100,
      rows: 24,
    });
    setRunning(true);
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
      <div className="terminal-surface" ref={containerRef}>
        {!running ? <div className="empty-state compact">{t("terminalNotStarted")}</div> : null}
      </div>
    </section>
  );
}
