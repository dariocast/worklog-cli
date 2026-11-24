import { View, div, svg } from "gpui-kit";
import { h_flex } from "gpui-base";
import { exec, theme, run, setShared, openOverlay, focused, panes, spaces } from "herdr";

function formatDuration(sec) {
  if (!sec && sec !== 0) return "0h00";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return `${h}h${m.toString().padStart(2, "0")}`;
}

function parseOutput(out) {
  if (!out) return null;
  const str = typeof out === "object" && out !== null && "stdout" in out ? out.stdout : String(out);
  try {
    return JSON.parse(str);
  } catch {
    return null;
  }
}

function getContextInfo() {
  try {
    const f = focused();
    if (f?.cwd) {
      return { cwd: f.cwd, key: `${f.space}:${f.tab}:${f.pane || f.terminal || f.cwd}` };
    }
    const p = panes()?.find((p) => p.focused);
    if (p?.cwd) {
      return { cwd: p.cwd, key: `pane:${p.pane}:${p.cwd}` };
    }
    const s = spaces()?.find((s) => s.active);
    if (s?.folder) {
      return { cwd: s.folder, key: `space:${s.id}:${s.folder}` };
    }
    return { cwd: null, key: null };
  } catch {
    return { cwd: null, key: null };
  }
}

export default class WorklogBar extends View {
  init(_props, cx) {
    this.report = null;
    this.current = null;
    this.lastContextKey = null;
    this.lastCwd = null;
    this.fetchingReport = false;
    this.fetchingCurrent = false;
    this.ticks = 0;

    const ctx = getContextInfo();
    this.lastContextKey = ctx.key;
    this.lastCwd = ctx.cwd;

    this.refreshReport(cx);
    if (ctx.cwd) {
      this.refreshCurrent(ctx.cwd, cx);
    }

    // Polling rapido a intervallo di 1s per rilevare immediatamente il cambio focus del terminale/agente
    cx.timer.every(1000, (cx) => {
      this.ticks++;
      const currentCtx = getContextInfo();
      const focusChanged =
        currentCtx.key !== this.lastContextKey || currentCtx.cwd !== this.lastCwd;

      if (focusChanged) {
        this.lastContextKey = currentCtx.key;
        this.lastCwd = currentCtx.cwd;
        if (currentCtx.cwd) {
          this.refreshCurrent(currentCtx.cwd, cx);
        } else {
          this.current = null;
          cx.notify();
        }
      }

      // Ricarica periodica del report e del tempo trascorso ogni 15 secondi (oppure subito se focus è cambiato)
      if (focusChanged || this.ticks % 15 === 0) {
        this.refreshReport(cx);
        if (!focusChanged && this.lastCwd) {
          this.refreshCurrent(this.lastCwd, cx);
        }
      }
    });
  }

  async refreshReport(cx) {
    if (this.fetchingReport) return;
    this.fetchingReport = true;
    try {
      const out = await exec("worklog", ["--json", "report"]);
      const res = parseOutput(out);
      if (res?.data) {
        this.report = res.data;
        cx.notify();
      }
    } catch {
      // Ignora errori transienti
    } finally {
      this.fetchingReport = false;
    }
  }

  async refreshCurrent(cwd, cx) {
    if (this.fetchingCurrent) return;
    this.fetchingCurrent = true;
    try {
      const out = await exec("worklog", ["--json", "current", "--cwd", cwd]);
      const res = parseOutput(out);
      if (res?.data) {
        this.current = res.data;
        cx.notify();
      }
    } catch {
      // Ignora errori transienti
    } finally {
      this.fetchingCurrent = false;
    }
  }

  openTask(taskId) {
    setShared("worklog_selected_task", taskId || null);
    openOverlay({
      mod: "worklog-dialog",
      kind: "dialog",
      props: { taskId: taskId || null },
      width: 580,
      height: 520,
    });
  }

  render(cx) {
    const t = theme();
    const totalSec = this.report?.duration_seconds ?? 0;
    const cur = this.current;
    const isTracked = cur && (cur.status === "tracked" || cur.status === "project");
    const isIgnored = cur && cur.status === "ignored";
    const isUntracked = cur && (cur.status === "untracked" || cur.status === "unmapped");

    const taskLabel = isTracked ? (cur.ref ?? cur.task_id) : null;
    const taskTitle = isTracked && cur.title
      ? (cur.title.length > 25 ? cur.title.slice(0, 25) + "…" : cur.title)
      : null;
    const taskDur = isTracked
      ? formatDuration(cur.duration_today_seconds || cur.duration_total_seconds)
      : null;

    const targetTaskId = isTracked ? cur.task_id : null;

    // text_size(11.8) per combaciare esattamente con la status bar di Herdr
    return h_flex()
      .id("worklog-bar")
      .items_center()
      .gap(6)
      .px(8)
      .h_full()
      .text_size(11.8)
      .cursor_pointer()
      .hover((s) => s.bg(t.raised))
      .on_click(() => this.openTask(targetTaskId))
      .child(
        svg("mods/workspace-chrome/clock.svg")
          .size(12)
          .text_color(t.accent)
      )
      .when(isTracked && Boolean(taskLabel), (el) =>
        el
          .child(
            h_flex()
              .gap(4)
              .items_center()
              .child(
                div()
                  .font_semibold()
                  .text_color(t.foreground)
                  .child(taskLabel)
              )
              .when(Boolean(taskTitle), (tEl) =>
                tEl.child(
                  div()
                    .text_color(t.muted)
                    .child(`(${taskTitle})`)
                )
              )
              .child(
                div()
                  .text_color(t.accent)
                  .child(taskDur)
              )
          )
          .child(div().text_color(t.dim).child("·"))
      )
      .when(isIgnored, (el) =>
        el
          .child(div().text_color(t.dim).child("Non tracciato"))
          .child(div().text_color(t.dim).child("·"))
      )
      .when(isUntracked, (el) =>
        el
          .child(div().text_color(t.dim).child(cur?.project ? `[${cur.project}]` : "Inbox"))
          .child(div().text_color(t.dim).child("·"))
      )
      .child(
        div()
          .text_color(t.muted)
          .child(`Oggi: ${formatDuration(totalSec)}`)
      );
  }
}
