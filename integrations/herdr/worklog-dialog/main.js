import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex, Input, InputState } from "gpui-base";
import { exec, run, props, closeOverlay, theme, shared, setShared } from "herdr";

function formatDuration(sec) {
  if (!sec && sec !== 0) return "--";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return `${h}h${m.toString().padStart(2, "0")}`;
}

function formatDurationSpec(sec) {
  if (!sec && sec !== 0) return "15m";
  const m = Math.round(sec / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const remM = m % 60;
  return remM > 0 ? `${h}h${remM}m` : `${h}h`;
}

function formatTime(isoStr) {
  if (!isoStr) return "";
  try {
    const d = new Date(isoStr);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return isoStr;
  }
}

function parseOutput(out) {
  if (!out) return null;
  const str =
    typeof out === "object" && out !== null
      ? (out.stdout && out.stdout.trim() ? out.stdout : out.stderr)
      : String(out);
  try {
    return JSON.parse(str);
  } catch (err) {
    if (typeof out === "object" && out?.stderr) {
      throw new Error(out.stderr.trim());
    }
    throw new Error(`JSON parse error: ${err.message} (output: ${String(str).slice(0, 100)})`);
  }
}

export default class WorklogDialog extends View {
  init(_props, cx) {
    this.handle = cx.focus_handle();
    this.handle.focus();
    const p = _props ?? props() ?? {};
    this.taskId =
      p.taskId !== undefined && p.taskId !== null
        ? p.taskId
        : (shared("worklog_selected_task") || null);

    this.loading = true;
    this.saving = false;
    this.task = null;
    this.report = null;
    this.error = null;
    this.actionError = null;
    this.actionSuccess = null;
    this.editingInterval = null;

    this.logSpecState = InputState.new({ placeholder: "es. 30m, 1h, 09:00-10:00" });
    this.logNoteState = InputState.new({ placeholder: "Nota opzionale..." });

    const handleFormSubmit = (_e, submitCx) => {
      if (this.taskId && !this.saving) {
        submitCx.spawn(async (asyncCx) => {
          if (this.editingInterval) {
            await this.saveEditInterval(
              this.editingInterval.id,
              this.logSpecState.value(),
              this.logNoteState.value(),
              asyncCx
            );
          } else {
            await this.logTime(
              this.taskId,
              this.logSpecState.value(),
              this.logNoteState.value(),
              asyncCx
            );
          }
        });
      }
    };

    if (this.logSpecState) {
      this.logSpecState.on("submit", handleFormSubmit);
    }
    if (this.logNoteState) {
      this.logNoteState.on("submit", handleFormSubmit);
    }

    // load() su init usa direttamente l'AsyncContext di init
    this.load(cx);
  }

  async selectTask(taskId, cx) {
    this.taskId = taskId;
    setShared("worklog_selected_task", taskId);
    this.actionError = null;
    this.actionSuccess = null;
    this.editingInterval = null;
    if (this.logSpecState) this.logSpecState.set_value("");
    if (this.logNoteState) this.logNoteState.set_value("");
    await this.load(cx);
  }

  startEditInterval(iv, cx) {
    this.editingInterval = iv;
    this.actionError = null;
    this.actionSuccess = null;
    if (this.logSpecState) {
      this.logSpecState.set_value(formatDurationSpec(iv.duration_seconds));
    }
    if (this.logNoteState) {
      this.logNoteState.set_value(iv.note || "");
    }
    try {
      cx.notify();
    } catch {
      // Ignora errori di contesto sincrono
    }
  }

  cancelEditInterval(cx) {
    this.editingInterval = null;
    this.actionError = null;
    this.actionSuccess = null;
    if (this.logSpecState) this.logSpecState.set_value("");
    if (this.logNoteState) this.logNoteState.set_value("");
    try {
      cx.notify();
    } catch {
      // Ignora errori di contesto sincrono
    }
  }

  async load(cx) {
    const taskId = this.taskId;
    this.loading = true;
    this.error = null;
    try {
      cx.notify();
    } catch {
      // Protezione safe
    }

    try {
      if (taskId) {
        const out = await exec("worklog", ["--json", "show", taskId]);
        const res = parseOutput(out);
        if (res?.error) {
          this.error = res.error.message || `Impossibile caricare il task ${taskId}`;
          this.task = null;
        } else {
          this.task = res?.data ?? null;
        }
      } else {
        this.task = null;
        const out = await exec("worklog", ["--json", "report"]);
        const res = parseOutput(out);
        if (res?.error) {
          this.error = res.error.message || "Impossibile caricare il report di oggi";
          this.report = null;
        } else {
          this.report = res?.data ?? null;
        }
      }
    } catch (err) {
      this.error = String(err.message ?? err);
    } finally {
      this.loading = false;
      try {
        cx.notify();
      } catch {
        // Protezione safe
      }
    }
  }

  async logTime(taskId, spec, note, cx) {
    if (this.saving) return;
    if (!taskId || !spec || !spec.trim()) {
      this.actionError = "Specifica una durata valida (es. 15m, 1h, 09:00-10:00)";
      try {
        cx.notify();
      } catch {}
      return;
    }
    this.saving = true;
    this.actionError = null;
    this.actionSuccess = null;
    try {
      cx.notify();
    } catch {}

    try {
      const args = ["--json", "log", spec.trim(), "--task", taskId];
      if (note && note.trim()) {
        args.push("--note", note.trim());
      }
      const out = await exec("worklog", args);
      const res = parseOutput(out);
      if (res?.error) {
        this.actionError = res.error.message || "Errore nella registrazione del tempo";
      } else {
        this.actionSuccess = `Registrati +${spec.trim()} con successo!`;
        if (this.logSpecState) this.logSpecState.set_value("");
        if (this.logNoteState) this.logNoteState.set_value("");
        await this.load(cx);
      }
    } catch (err) {
      this.actionError = String(err.message ?? err);
    } finally {
      this.saving = false;
      try {
        cx.notify();
      } catch {}
    }
  }

  async saveEditInterval(intervalId, spec, note, cx) {
    if (this.saving) return;
    if (!this.taskId || !spec || !spec.trim()) {
      this.actionError = "Specifica una durata valida per la modifica";
      try {
        cx.notify();
      } catch {}
      return;
    }
    this.saving = true;
    this.actionError = null;
    this.actionSuccess = null;
    try {
      cx.notify();
    } catch {}

    try {
      // 1. Rimuovi vecchio intervallo
      const rmOut = await exec("worklog", ["--json", "rm", intervalId]);
      const rmRes = parseOutput(rmOut);
      if (rmRes?.error) {
        throw new Error(rmRes.error.message || "Errore nella rimozione del vecchio intervallo");
      }

      // 2. Crea nuovo intervallo con i valori aggiornati
      const args = ["--json", "log", spec.trim(), "--task", this.taskId];
      if (note && note.trim()) {
        args.push("--note", note.trim());
      }
      const logOut = await exec("worklog", args);
      const logRes = parseOutput(logOut);
      if (logRes?.error) {
        throw new Error(logRes.error.message || "Errore nella creazione dell'intervallo modificato");
      }

      this.actionSuccess = `Intervallo aggiornato (${spec.trim()}) con successo!`;
      this.editingInterval = null;
      if (this.logSpecState) this.logSpecState.set_value("");
      if (this.logNoteState) this.logNoteState.set_value("");
      await this.load(cx);
    } catch (err) {
      this.actionError = String(err.message ?? err);
    } finally {
      this.saving = false;
      try {
        cx.notify();
      } catch {}
    }
  }

  async removeInterval(intervalId, cx) {
    if (this.saving) return;
    this.saving = true;
    this.actionError = null;
    this.actionSuccess = null;
    try {
      cx.notify();
    } catch {}

    try {
      const out = await exec("worklog", ["--json", "rm", intervalId]);
      const res = parseOutput(out);
      if (res?.error) {
        this.actionError = res.error.message || "Errore durante la rimozione dell'intervallo";
      } else {
        this.actionSuccess = "Intervallo rimosso con successo";
        if (this.editingInterval?.id === intervalId) {
          this.editingInterval = null;
          if (this.logSpecState) this.logSpecState.set_value("");
          if (this.logNoteState) this.logNoteState.set_value("");
        }
        await this.load(cx);
      }
    } catch (err) {
      this.actionError = String(err.message ?? err);
    } finally {
      this.saving = false;
      try {
        cx.notify();
      } catch {}
    }
  }

  render(cx) {
    const t = theme();
    const p = props() ?? {};
    const taskId = this.taskId;

    const closeBtn = () =>
      h_flex()
        .id("close-btn")
        .gap(4)
        .items_center()
        .px(8)
        .py(4)
        .rounded(6)
        .cursor_pointer()
        .text_color(t.muted)
        .hover((s) => s.bg(t.raised).text_color(t.foreground))
        .on_click(() => closeOverlay(p.overlay))
        .child(
          svg("mods/workspace-chrome/x.svg")
            .size(11)
            .text_color(t.muted)
        )
        .child(div().text_size(11).child("Esc"));

    const renderTaskDetails = (task) => {
      const intervals = (task.intervals ?? []).slice(-12).reverse();
      const isEditing = Boolean(this.editingInterval);

      return v_flex()
        .gap(12)
        .child(
          h_flex()
            .items_center()
            .gap(8)
            .child(
              div()
                .px(6)
                .py(2)
                .rounded(4)
                .bg(t.accent)
                .text_color(t.accent_foreground ?? t.background)
                .font_semibold()
                .child(task.ref ?? task.id)
            )
            .child(div().text_size(12).text_color(t.dim).child(`[${task.project ?? "no-project"}]`))
            .child(
              div()
                .text_size(11)
                .px(6)
                .py(1)
                .rounded(4)
                .bg(task.status === "waiting" ? t.blocked : t.raised)
                .text_color(task.status === "waiting" ? t.foreground : t.muted)
                .child(task.status?.toUpperCase() ?? "OPEN")
            )
            .child(div().flex_1())
            .child(
              div()
                .text_size(14)
                .font_semibold()
                .text_color(t.accent)
                .child(formatDuration(task.duration_seconds))
            )
        )
        .child(
          div()
            .text_size(14)
            .font_semibold()
            .text_color(t.foreground)
            .child(task.title ?? "Senza titolo")
        )
        .when(Boolean(task.description), (el) =>
          el.child(
            div()
              .text_size(12)
              .text_color(t.muted)
              .p(8)
              .rounded(6)
              .bg(t.raised)
              .child(task.description)
          )
        )
        .when(Boolean(task.next), (el) =>
          el.child(
            v_flex()
              .gap(4)
              .p(8)
              .rounded(6)
              .bg(t.surface)
              .border_1()
              .border_color(t.input)
              .child(
                h_flex()
                  .gap(6)
                  .items_center()
                  .child(
                    svg("mods/workspace-chrome/chevron-right.svg")
                      .size(11)
                      .text_color(t.accent)
                  )
                  .child(div().text_size(11).font_semibold().text_color(t.accent).child("PROSSIMI PASSI"))
              )
              .child(div().text_size(12).text_color(t.foreground).child(task.next))
          )
        )
        // Sezione Aggiungi / Modifica Tempo Manuale
        .child(
          v_flex()
            .gap(8)
            .p(10)
            .rounded(6)
            .bg(t.surface)
            .border_1()
            .border_color(isEditing ? t.accent : t.border)
            .child(
              h_flex()
                .justify_between()
                .items_center()
                .child(
                  h_flex()
                    .gap(6)
                    .items_center()
                    .child(svg("mods/workspace-chrome/clock.svg").size(12).text_color(t.accent))
                    .child(
                      div()
                        .text_size(11)
                        .font_semibold()
                        .text_color(t.foreground)
                        .child(
                          isEditing
                            ? `MODIFICA INTERVALLO (${formatDuration(this.editingInterval.duration_seconds)})`
                            : "AGGIUNGI TEMPO MANUALE"
                        )
                    )
                )
                .when(this.saving, (el) =>
                  el.child(div().text_size(11).text_color(t.accent).child("Salvataggio..."))
                )
                .when(!this.saving && Boolean(this.actionSuccess), (el) =>
                  el.child(div().text_size(11).text_color(t.accent).child(this.actionSuccess))
                )
                .when(!this.saving && Boolean(this.actionError), (el) =>
                  el.child(div().text_size(11).text_color(t.blocked).child(this.actionError))
                )
            )
            .child(
              h_flex()
                .gap(6)
                .items_center()
                .child(
                  div()
                    .text_size(11)
                    .text_color(t.muted)
                    .child(isEditing ? "Imposta durata:" : "Rapidi:")
                )
                .children(
                  ["15m", "30m", "45m", "1h", "2h"].map((spec) =>
                    div()
                      .id(`preset-${spec}`)
                      .px(8)
                      .py(3)
                      .rounded(4)
                      .bg(t.raised)
                      .when(!this.saving, (s) =>
                        s
                          .cursor_pointer()
                          .hover((h) => h.bg(t.accent).text_color(t.accent_foreground ?? t.background))
                      )
                      .when(this.saving, (s) => s.opacity(0.5))
                      .text_size(11)
                      .font_semibold()
                      .text_color(t.foreground)
                      .on_click((_e, clickCx) => {
                        if (this.saving) return;
                        clickCx.spawn(async (asyncCx) => {
                          if (this.editingInterval) {
                            if (this.logSpecState) this.logSpecState.set_value(spec);
                            try { asyncCx.notify(); } catch {}
                          } else {
                            await this.logTime(task.id, spec, null, asyncCx);
                          }
                        });
                      })
                      .child(`+${spec}`)
                  )
                )
            )
            .child(
              h_flex()
                .gap(6)
                .items_center()
                .child(
                  Input.new(this.logSpecState)
                    .w(150)
                    .text_size(11.5)
                )
                .child(
                  Input.new(this.logNoteState)
                    .flex_1()
                    .text_size(11.5)
                )
                .when(isEditing, (el) =>
                  el.child(
                    div()
                      .id("btn-cancel-edit")
                      .px(8)
                      .py(4)
                      .rounded(4)
                      .bg(t.raised)
                      .hover((s) => s.opacity(0.85))
                      .cursor_pointer()
                      .text_size(11.5)
                      .text_color(t.muted)
                      .on_click((_e, clickCx) => {
                        this.cancelEditInterval(clickCx);
                      })
                      .child("Annulla")
                  )
                )
                .child(
                  div()
                    .id("btn-log-time")
                    .px(10)
                    .py(4)
                    .rounded(4)
                    .bg(t.accent)
                    .when(!this.saving, (s) => s.cursor_pointer().hover((h) => h.opacity(0.85)))
                    .when(this.saving, (s) => s.opacity(0.5))
                    .text_size(11.5)
                    .font_semibold()
                    .text_color(t.accent_foreground ?? t.background)
                    .on_click((_e, clickCx) => {
                      if (this.saving) return;
                      clickCx.spawn(async (asyncCx) => {
                        if (this.editingInterval) {
                          await this.saveEditInterval(
                            this.editingInterval.id,
                            this.logSpecState.value(),
                            this.logNoteState.value(),
                            asyncCx
                          );
                        } else {
                          await this.logTime(
                            task.id,
                            this.logSpecState.value(),
                            this.logNoteState.value(),
                            asyncCx
                          );
                        }
                      });
                    })
                    .child(isEditing ? "Salva Modifica" : (this.saving ? "..." : "Registra"))
                )
            )
        )
        // Sessioni recenti con opzione modifica e rimozione
        .when(intervals.length > 0, (el) =>
          el.child(
            v_flex()
              .gap(4)
              .child(
                h_flex()
                  .gap(6)
                  .items_center()
                  .child(
                    svg("mods/workspace-chrome/terminal.svg")
                      .size(11)
                      .text_color(t.muted)
                  )
                  .child(div().text_size(11).font_semibold().text_color(t.muted).child("SESSIONI RECENTI"))
              )
              .children(
                intervals.map((iv, idx) => {
                  const isThisEditing = this.editingInterval?.id === iv.id;
                  const isManual = iv.source === "manual";
                  return h_flex()
                    .id(`iv-${idx}`)
                    .justify_between()
                    .items_center()
                    .py(3)
                    .px(6)
                    .rounded(4)
                    .when(idx % 2 === 0, (s) => s.bg(t.raised))
                    .when(isThisEditing, (s) => s.border_1().border_color(t.accent))
                    .text_size(11.5)
                    .text_color(t.muted)
                    .child(
                      h_flex()
                        .gap(6)
                        .items_center()
                        .child(div().size(4).rounded(2).bg(isManual ? t.accent : t.muted))
                        .child(
                          div()
                            .font_semibold()
                            .text_color(isManual ? t.accent : t.foreground)
                            .child(iv.source ?? "agent")
                        )
                        .child(div().text_color(t.dim).child(formatTime(iv.started_at)))
                        .when(Boolean(iv.note), (noteEl) =>
                          noteEl.child(
                            div()
                              .text_color(t.muted)
                              .text_size(11)
                              .child(`"${iv.note}"`)
                          )
                        )
                    )
                    .child(
                      h_flex()
                        .gap(6)
                        .items_center()
                        .child(
                          div()
                            .font_semibold()
                            .text_color(t.foreground)
                            .child(formatDuration(iv.duration_seconds))
                        )
                        .when(isManual, (btnEl) =>
                          btnEl.child(
                            div()
                              .px(5)
                              .py(1)
                              .rounded(3)
                              .bg(t.surface)
                              .text_size(10.5)
                              .text_color(t.muted)
                              .hover((s) => s.bg(t.raised).text_color(t.accent))
                              .cursor_pointer()
                              .on_click((e, clickCx) => {
                                clickCx.stop_propagation();
                                this.startEditInterval(iv, clickCx);
                              })
                              .child("Modifica")
                          )
                        )
                        .child(
                          div()
                            .px(5)
                            .py(1)
                            .rounded(3)
                            .bg(t.surface)
                            .text_size(10.5)
                            .text_color(t.dim)
                            .hover((s) => s.bg(t.blocked).text_color(t.foreground))
                            .cursor_pointer()
                            .on_click((e, clickCx) => {
                              clickCx.stop_propagation();
                              if (this.saving) return;
                              clickCx.spawn(async (asyncCx) => {
                                await this.removeInterval(iv.id, asyncCx);
                              });
                            })
                            .child("✕")
                        )
                    );
                })
              )
          )
        );
    };

    const renderDailyReport = (report) => {
      const entries = report?.entries ?? [];
      const total = formatDuration(report?.duration_seconds);
      return v_flex()
        .gap(10)
        .child(
          h_flex()
            .justify_between()
            .items_center()
            .child(
              h_flex()
                .gap(6)
                .items_center()
                .child(
                  svg("mods/workspace-chrome/clock.svg")
                    .size(13)
                    .text_color(t.accent)
                )
                .child(div().text_size(14).font_semibold().text_color(t.foreground).child("Attività di Oggi"))
            )
            .child(div().text_size(14).font_semibold().text_color(t.accent).child(`Totale: ${total}`))
        )
        .when(entries.length === 0, (el) =>
          el.child(div().p(12).text_color(t.dim).child("Nessuna voce registrata per oggi."))
        )
        .children(
          entries.map((entry, idx) =>
            h_flex()
              .id(`e-${idx}`)
              .justify_between()
              .items_center()
              .p(8)
              .rounded(6)
              .bg(t.surface)
              .hover((s) => s.bg(t.raised))
              .cursor_pointer()
              .on_click((_e, clickCx) => {
                clickCx.spawn(async (asyncCx) => {
                  await this.selectTask(entry.task_id, asyncCx);
                });
              })
              .child(
                v_flex()
                  .gap(2)
                  .child(
                    h_flex()
                      .gap(6)
                      .child(div().font_semibold().text_color(t.accent).child(entry.ref ?? entry.key))
                      .child(div().text_size(11).text_color(t.dim).child(`[${entry.project}]`))
                  )
                  .child(div().text_size(12).text_color(t.foreground).child(entry.title))
              )
              .child(
                h_flex()
                  .gap(8)
                  .items_center()
                  .child(
                    div()
                      .font_semibold()
                      .text_color(t.foreground)
                      .child(formatDuration(entry.duration_seconds))
                  )
                  .child(
                    svg("mods/workspace-chrome/chevron-right.svg")
                      .size(11)
                      .text_color(t.muted)
                  )
              )
          )
        );
    };

    return v_flex()
      .id("worklog-dialog")
      .track_focus(this.handle)
      .on_key_down((e, keyCx) => {
        if (e.key === "escape") {
          closeOverlay(props().overlay);
          keyCx.stop_propagation();
        }
      })
      .size_full()
      .p(16)
      .gap(12)
      .rounded(8)
      .bg(t.popover)
      .border_1()
      .border_color(t.border)
      .text_size(13)
      .child(
        h_flex()
          .items_center()
          .justify_between()
          .child(
            h_flex()
              .items_center()
              .gap(8)
              .when(Boolean(this.taskId), (el) =>
                el.child(
                  h_flex()
                    .gap(4)
                    .items_center()
                    .px(6)
                    .py(2)
                    .rounded(4)
                    .cursor_pointer()
                    .text_color(t.muted)
                    .hover((s) => s.bg(t.raised).text_color(t.foreground))
                    .on_click((_e, clickCx) => {
                      clickCx.spawn(async (asyncCx) => {
                        await this.selectTask(null, asyncCx);
                      });
                    })
                    .child(
                      svg("mods/workspace-chrome/chevron-left.svg")
                        .size(11)
                        .text_color(t.muted)
                    )
                    .child(div().text_size(11).child("Oggi"))
                )
              )
              .child(
                div()
                  .text_size(15)
                  .font_semibold()
                  .text_color(t.foreground)
                  .child(taskId ? "Dettaglio Task Worklog" : "Riepilogo Giornata Worklog")
              )
          )
          .child(closeBtn())
      )
      .child(
        div()
          .flex_1()
          .overflow_y_scrollbar()
          .when(this.loading, (el) => el.child(div().p(16).text_color(t.muted).child("Caricamento da worklog...")))
          .when(!this.loading && Boolean(this.task), (el) => el.child(renderTaskDetails(this.task)))
          .when(!this.loading && !this.task && Boolean(this.report), (el) => el.child(renderDailyReport(this.report)))
          .when(!this.loading && Boolean(this.error), (el) =>
            el.child(div().p(12).text_color(t.blocked).child(`Errore: ${this.error}`))
          )
      );
  }
}
