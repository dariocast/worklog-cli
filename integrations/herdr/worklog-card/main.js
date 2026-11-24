import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { exec, theme, run, setShared, openOverlay } from "herdr";

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

export default class WorklogCard extends View {
  init(_props, cx) {
    this.report = null;
    this.loading = false;
    this.refresh(cx);
    cx.timer.every(20000, (cx) => this.refresh(cx));
  }

  async refresh(cx) {
    try {
      this.loading = true;
      const out = await exec("worklog", ["--json", "report"]);
      const res = parseOutput(out);
      if (res?.data) {
        this.report = res.data;
      }
    } catch {
      // Ignora errori transienti
    } finally {
      this.loading = false;
      cx.notify();
    }
  }

  openDetails(taskId) {
    setShared("worklog_selected_task", taskId);
    openOverlay({
      mod: "worklog-dialog",
      kind: "dialog",
      props: { taskId },
      width: 580,
      height: 520,
    });
  }

  render(cx) {
    const t = theme();
    const totalSec = this.report?.duration_seconds ?? 0;
    const entries = this.report?.entries ?? [];

    return v_flex()
      .id("worklog-card")
      .size_full()
      .p(8)
      .gap(6)
      .rounded(8)
      .bg(t.chrome)
      .border_1()
      .border_color(t.border)
      .text_size(12)
      .child(
        // Header in linea con Spaces e Agents (Iniziale maiuscola, non ALL-CAPS)
        h_flex()
          .justify_between()
          .items_center()
          .cursor_pointer()
          .hover((s) => s.opacity(0.85))
          .on_click(() => this.openDetails(null))
          .child(
            h_flex()
              .gap(6)
              .items_center()
              .child(
                svg("mods/workspace-chrome/clock.svg")
                  .size(12)
                  .text_color(t.accent)
              )
              .child(div().font_semibold().text_size(12).text_color(t.foreground).child("Worklog oggi"))
          )
          .child(
            div()
              .font_semibold()
              .text_size(12)
              .text_color(t.accent)
              .child(formatDuration(totalSec))
          )
      )
      .when(entries.length === 0, (el) =>
        el.child(div().py(4).text_size(11).text_color(t.dim).child("Nessuna attività registrata oggi"))
      )
      .when(entries.length > 0, (el) =>
        el.child(
          v_flex()
            .id("wl-items-scroll")
            .flex_1()
            .min_h(0)
            .gap(3)
            .pr(2)
            .overflow_y_scrollbar()
            .children(
              entries.map((item, idx) =>
                h_flex()
                  .id(`wl-item-${idx}`)
                  .justify_between()
                  .items_center()
                  .px(6)
                  .py(3)
                  .rounded(4)
                  .cursor_pointer()
                  .hover((s) => s.bg(t.raised))
                  .on_click(() => this.openDetails(item.task_id))
                  .child(
                    h_flex()
                      .gap(6)
                      .items_center()
                      .child(div().size(5).rounded(2.5).bg(t.accent))
                      .child(
                        div()
                          .text_color(t.foreground)
                          .font_semibold()
                          .max_w(130)
                          .overflow_hidden()
                          .child(item.ref ?? item.key)
                      )
                  )
                  .child(
                    div()
                      .text_size(11)
                      .text_color(t.muted)
                      .child(formatDuration(item.duration_seconds))
                  )
              )
            )
        )
      );
  }
}
