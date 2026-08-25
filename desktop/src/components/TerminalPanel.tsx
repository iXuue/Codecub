import { useEffect, useRef, useState } from "react";
import type { I18nKey } from "../i18n";
import { gsap, motionAllowed, useGSAP } from "../motion/gsapSetup";

type XTermInstance = import("@xterm/xterm").Terminal;

type TerminalPanelProps = {
  t: (key: I18nKey) => string;
  projectPath: string;
};

const TERMINAL_COLS = 100;
const TERMINAL_FONT_SIZE = 12;
const TERMINAL_LINE_HEIGHT = 1.25;

function terminalRows(container: HTMLElement | null): number {
  const height = container?.clientHeight || 220;
  const lineHeightPx = TERMINAL_FONT_SIZE * TERMINAL_LINE_HEIGHT;
  return Math.max(10, Math.floor((height - 8) / lineHeightPx));
}

export function TerminalPanel({ t, projectPath }: TerminalPanelProps) {
  const panelRef = useRef<HTMLElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<XTermInstance | null>(null);
  const terminalIdRef = useRef(`terminal-${crypto.randomUUID()}`);
  const [running, setRunning] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [terminalError, setTerminalError] = useState("");

  useGSAP(
    () => {
      if (!motionAllowed()) {
        return;
      }
      gsap.to(".terminal-surface", {
        height: expanded ? 236 : 0,
        autoAlpha: expanded ? 1 : 0,
        duration: 0.24,
        ease: "power2.out",
        overwrite: "auto",
      });
    },
    { dependencies: [expanded], scope: panelRef, revertOnUpdate: false },
  );

  useEffect(() => {
    const removeData = window.codecub.onTerminalData((terminalId, data) => {
      if (terminalId === terminalIdRef.current) {
        terminalRef.current?.write(data, () => terminalRef.current?.scrollToBottom());
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
    setExpanded(true);
    await import("@xterm/xterm/css/xterm.css");
    const { Terminal } = await import("@xterm/xterm");
    containerRef.current.replaceChildren();
    const terminal = new Terminal({
      cols: TERMINAL_COLS,
      rows: terminalRows(containerRef.current),
      cursorBlink: true,
      fontFamily: '"JetBrains Mono", Consolas, "SFMono-Regular", monospace',
      fontSize: TERMINAL_FONT_SIZE,
      lineHeight: TERMINAL_LINE_HEIGHT,
      theme: {
        background: "#07111F",
        foreground: "#D8E6F3",
        cursor: "#38BDF8",
        selectionBackground: "#1E3A5F",
        black: "#07111F",
        red: "#F87171",
        green: "#34D399",
        yellow: "#FBBF24",
        blue: "#38BDF8",
        magenta: "#A78BFA",
        cyan: "#22D3EE",
        white: "#F8FAFC",
        brightBlack: "#475569",
        brightRed: "#FCA5A5",
        brightGreen: "#86EFAC",
        brightYellow: "#FDE68A",
        brightBlue: "#7DD3FC",
        brightMagenta: "#C4B5FD",
        brightCyan: "#67E8F9",
        brightWhite: "#FFFFFF",
      },
    });
    terminal.open(containerRef.current);
    terminal.focus();
    terminal.onData((data) => window.codecub.writeTerminal({ terminalId: terminalIdRef.current, data }));
    terminalRef.current = terminal;
    try {
      await window.codecub.startTerminal({
        terminalId: terminalIdRef.current,
        cwd: projectPath,
        cols: TERMINAL_COLS,
        rows: terminalRows(containerRef.current),
      });
      setRunning(true);
      setExpanded(true);
      requestAnimationFrame(() => terminal.focus());
    } catch (error) {
      setTerminalError(error instanceof Error ? error.message : String(error));
      terminalRef.current?.dispose();
      terminalRef.current = null;
      containerRef.current?.replaceChildren();
      setRunning(false);
    }
  }

  function setTerminalVisibility(nextExpanded: boolean) {
    setExpanded(nextExpanded);
    if (!nextExpanded) {
      terminalRef.current?.blur();
      if (containerRef.current?.contains(document.activeElement)) {
        (document.activeElement as HTMLElement | null)?.blur();
      }
    }
  }

  async function closeTerminal() {
    await window.codecub.closeTerminal(terminalIdRef.current);
    terminalRef.current?.dispose();
    terminalRef.current = null;
    setRunning(false);
    setTerminalVisibility(false);
  }

  async function runCodecub() {
    setExpanded(true);
    if (!running) {
      await startTerminal();
      window.setTimeout(() => {
        void window.codecub.writeTerminal({ terminalId: terminalIdRef.current, data: "codecub\r" });
        terminalRef.current?.focus();
      }, 150);
      return;
    }
    void window.codecub.writeTerminal({ terminalId: terminalIdRef.current, data: "codecub\r" });
    requestAnimationFrame(() => terminalRef.current?.focus());
  }

  return (
    <section className={expanded ? "terminal-panel expanded" : "terminal-panel collapsed"} aria-label={t("terminal")} ref={panelRef}>
      <div className="terminal-header">
        <div className="terminal-title">
          <span className="terminal-dot" />
          <span>{t("terminal")}</span>
          <span className="terminal-subtitle">{running ? projectPath : t("terminalNotStarted")}</span>
        </div>
        <div className="terminal-actions">
          {running ? (
            <button className="button secondary terminal-run-action" type="button" onClick={runCodecub}>
              {t("runCodecubTerminal")}
            </button>
          ) : null}
          {running ? (
            <button className="button secondary" type="button" onClick={() => setTerminalVisibility(!expanded)}>
              {expanded ? t("collapseTerminal") : t("expandTerminal")}
            </button>
          ) : null}
          <button className="button secondary" type="button" onClick={running ? closeTerminal : startTerminal}>
            {running ? t("closeTerminal") : t("startTerminal")}
          </button>
        </div>
      </div>
      <div className="terminal-surface">
        {terminalError ? <div className="terminal-error">{terminalError}</div> : null}
        {!running ? <div className="empty-state compact">{t("terminalNotStarted")}</div> : null}
        <div className="terminal-mount" ref={containerRef} onMouseDown={() => terminalRef.current?.focus()} />
      </div>
    </section>
  );
}
