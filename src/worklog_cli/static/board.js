"use strict";
// WorkLog board. Plain DOM, no dependencies; all text goes through textContent.

const token = new URLSearchParams(location.hash.slice(1)).get("token") || "";
const COLUMNS = [
  ["inbox", "Inbox"],
  ["todo", "To do"],
  ["progress", "In progress"],
  ["done", "Done"],
];
const LABELS = Object.fromEntries(COLUMNS);
const STATUS_BADGE = {
  inbox: "badge badge-outline",
  todo: "badge badge-secondary",
  progress: "badge badge-success",
  done: "badge badge-muted",
};
const state = {
  data: null,
  view: remembered("view", "board"),
  allDone: false,
  editing: null,
  dragging: null,
  sort: { key: "project", dir: 1 },
};

function remembered(key, fallback) {
  try { return localStorage.getItem("worklog." + key) || fallback; } catch { return fallback; }
}
function remember(key, value) {
  try { localStorage.setItem("worklog." + key, value); } catch { /* private mode */ }
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else if (name === "className") node.className = value;
    else node.setAttribute(name, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function duration(seconds) {
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return minutes + "m";
  return Math.floor(minutes / 60) + "h" + String(minutes % 60).padStart(2, "0");
}

function quote(path) {
  return "'" + path.replaceAll("'", "'\\''") + "'";
}

let toastTimer = 0;
function toast(message) {
  const box = document.getElementById("toast");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 4000);
}

async function api(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: { "X-Worklog-Token": token, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error?.message || response.statusText);
  return payload.data;
}

async function load() {
  try {
    state.data = await api("GET", "/api/board" + (state.allDone ? "?done=all" : ""));
    render();
  } catch (error) {
    toast(error.message);
  }
}

async function change(method, path, body) {
  try {
    await api(method, path, body);
    return true;
  } catch (error) {
    toast(error.message);
    return false;
  } finally {
    await load();
  }
}

// Rendering

function render() {
  if (!state.data) return;
  for (const button of document.querySelectorAll(".views button")) {
    button.setAttribute("aria-selected", String(button.dataset.view === state.view));
  }
  const main = document.getElementById("main");
  main.replaceChildren(state.view === "list" ? renderList() : renderBoard());
  const field = main.querySelector(".edit input");
  if (field && !main.contains(document.activeElement)) field.focus();
}

function tasksIn(column) {
  const tasks = state.data.tasks.filter((t) => t.column === column);
  if (column === "done") tasks.sort((a, b) => (b.closed_at || "").localeCompare(a.closed_at || ""));
  return tasks;
}

function doneToggle() {
  return el("button", {
    type: "button", className: "btn btn-link btn-sm",
    onclick: () => { state.allDone = !state.allDone; load(); },
  }, state.allDone ? `Last ${state.data.done_days} days` : "Show all");
}

function renderBoard() {
  const columns = el("div", { className: "columns" });
  for (const [key, label] of COLUMNS) {
    const tasks = tasksIn(key);
    const column = el("section", { className: "column", "data-column": key },
      el("div", { className: "column-head" },
        el("span", { className: "dot", "aria-hidden": "true" }),
        el("h2", {}, label),
        el("span", { className: "badge badge-secondary" }, tasks.length)));
    dropZone(column, { column: key, project: null });
    if (key === "todo") {
      const projects = state.data.projects;
      if (!projects.length) column.append(el("p", { className: "hint" }, "No projects yet."));
      for (const project of projects) {
        const own = tasks.filter((t) => t.project === project);
        const group = el("div", { className: own.length ? "group" : "group empty" },
          el("h3", {}, project), own.map(card));
        dropZone(group, { column: "todo", project });
        column.append(group);
      }
    } else {
      column.append(...tasks.map(card));
      if (key === "inbox" && !tasks.length) column.append(el("p", { className: "hint" }, "Nothing to triage."));
      if (key === "done" && !tasks.length) column.append(el("p", { className: "hint" }, "Nothing closed recently."));
      if (key === "done") column.append(el("div", { className: "column-foot" }, doneToggle()));
    }
    columns.append(column);
  }
  return columns;
}

function card(task) {
  if (state.editing === task.id) return el("article", { className: "card task editing" }, editor(task));
  const meta = el("div", { className: "meta" },
    task.column !== "todo" && task.column !== "inbox" ? el("span", { className: "badge badge-outline" }, task.project) : null,
    task.ref ? el("span", { className: "badge badge-secondary" }, task.ref) : null,
    task.duration_seconds > 0 ? el("span", { className: "time" }, duration(task.duration_seconds)) : null,
    el("span", { className: "id" }, task.id));
  const node = el("article", {
    className: "card task", draggable: "true", tabindex: "0", "data-id": task.id,
    onclick: () => { state.editing = task.id; render(); },
    onkeydown: (event) => { if (event.key === "Enter") { state.editing = task.id; render(); } },
    ondragstart: (event) => {
      state.dragging = task;
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", task.id);
      requestAnimationFrame(() => node.classList.add("dragging"));
    },
    ondragend: () => { state.dragging = null; clearMarks(); node.classList.remove("dragging"); },
  },
  el("div", { className: task.column === "done" ? "title done-title" : "title" }, task.title),
  meta,
  task.description ? el("div", { className: "desc" }, task.description) : null,
  task.column === "inbox"
    ? task.directories.map((dir) => el("div", { className: "map" }, `worklog map ${quote(dir)} PROJECT`))
    : null);
  dropZone(node, { column: task.column, project: task.column === "todo" ? task.project : null, anchor: task });
  return node;
}

function editor(task) {
  const field = (label, input) => el("label", { className: "label" }, label, input);
  const title = el("input", { className: "input", value: task.title, required: true, maxlength: "500" });
  const ref = el("input", { className: "input", value: task.ref || "", placeholder: "e.g. DEMO-123" });
  const project = el("select", { className: "select" },
    task.project === "inbox" ? el("option", { value: "", selected: true }, "inbox (choose a project)") : null,
    state.data.projects.map((name) => el("option", { value: name, selected: name === task.project }, name)));
  const description = el("textarea", { className: "textarea", placeholder: "What and why" });
  description.value = task.description || "";
  const save = async () => {
    const body = { title: title.value, ref: ref.value, description: description.value };
    if (project.value && project.value !== task.project) body.project = project.value;
    state.editing = null;
    await change("PATCH", `/api/tasks/${task.id}`, body);
  };
  const cancel = () => { state.editing = null; render(); };
  const done = task.status === "done";
  return el("form", {
    className: "edit",
    onsubmit: (event) => { event.preventDefault(); save(); },
    onkeydown: (event) => {
      if (event.key === "Escape") cancel();
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) save();
    },
    onclick: (event) => event.stopPropagation(),
  },
  field("Title", title), field("Reference", ref),
  field("Project", el("div", { className: "select-wrap" }, project)),
  field("Description", description),
  el("div", { className: "edit-meta" }, task.id, task.duration_seconds > 0 ? ` · ${duration(task.duration_seconds)} recorded` : ""),
  el("div", { className: "actions" },
    el("button", { type: "submit", className: "btn btn-sm" }, "Save"),
    el("button", { type: "button", className: "btn btn-sm btn-ghost", onclick: cancel }, "Cancel"),
    el("button", {
      type: "button", className: "btn btn-sm btn-outline",
      onclick: () => { state.editing = null; change("PATCH", `/api/tasks/${task.id}`, { status: done ? "open" : "done" }); },
    }, done ? "Reopen" : "Mark done"),
    task.started ? null : el("button", {
      type: "button", className: "btn btn-sm btn-ghost-destructive push",
      onclick: () => {
        if (!confirm(`Delete “${task.title}”?`)) return;
        state.editing = null;
        change("DELETE", `/api/tasks/${task.id}`);
      },
    }, "Delete")));
}

// Drag and drop: every rule of the transition table lives in plan().

function plan(task, target) {
  if (target.anchor && target.anchor.id === task.id) return { skip: true };
  const position = target.anchor ? { [target.position]: target.anchor.id } : {};
  if (task.column === "done") return target.column === "done" ? { skip: true } : { body: { status: "open" } };
  if (target.column === "done") return { body: { status: "done" } };
  if (target.column === "inbox") {
    if (task.column !== "inbox") return { reject: "Tasks never go back to the inbox; choose the right project." };
    return target.anchor ? { body: position } : { skip: true };
  }
  if (target.column === "progress") {
    if (task.column !== "progress") return { reject: "Only recorded time puts a task in progress." };
    return target.anchor ? { body: position } : { skip: true };
  }
  // To do
  if (task.column === "progress") return { reject: "To change the project of a task with time, edit its card." };
  if (!target.project) return { skip: true };
  if (target.project !== task.project) return { body: { project: target.project, ...position } };
  return target.anchor ? { body: position } : { skip: true };
}

function dropZone(node, target) {
  node.addEventListener("dragover", (event) => {
    if (!state.dragging) return;
    const where = resolve(event, node, target);
    const decision = plan(state.dragging, where);
    event.stopPropagation();
    clearMarks();
    if (decision.reject || decision.skip) return;  // not a drop target: the browser shows "no drop"
    event.preventDefault();
    if (where.anchor) node.classList.add(where.position === "before" ? "drop-before" : "drop-after");
    else node.classList.add("drop-into");
  });
  node.addEventListener("drop", async (event) => {
    if (!state.dragging) return;
    event.preventDefault();
    event.stopPropagation();
    const task = state.dragging;
    const decision = plan(task, resolve(event, node, target));
    state.dragging = null;
    clearMarks();
    if (decision.reject) toast(decision.reject);
    else if (decision.body) await change("PATCH", `/api/tasks/${task.id}`, decision.body);
  });
}

function resolve(event, node, target) {
  if (!target.anchor) return target;
  const box = node.getBoundingClientRect();
  return { ...target, position: event.clientY < box.top + box.height / 2 ? "before" : "after" };
}

function clearMarks() {
  for (const node of document.querySelectorAll(".drop-before, .drop-after, .drop-into")) {
    node.classList.remove("drop-before", "drop-after", "drop-into");
  }
}

// List view

const LIST_COLUMNS = [
  ["project", "Project", (t) => t.project],
  ["title", "Task", (t) => t.title.toLowerCase()],
  ["column", "Status", (t) => COLUMNS.findIndex(([key]) => key === t.column)],
  ["duration_seconds", "Time", (t) => t.duration_seconds],
];

function renderList() {
  const { key, dir } = state.sort;
  const value = LIST_COLUMNS.find(([name]) => name === key)[2];
  const tasks = [...state.data.tasks].sort((a, b) => {
    const left = value(a), right = value(b);
    if (left < right) return -dir;
    if (left > right) return dir;
    return a.rank - b.rank;
  });
  const head = el("tr", {}, LIST_COLUMNS.map(([name, label]) => el("th", {
    className: name === "duration_seconds" ? "num" : null,
    "aria-sort": name === key ? (dir > 0 ? "ascending" : "descending") : null,
  }, el("button", {
    type: "button",
    onclick: () => {
      state.sort = { key: name, dir: name === key ? -dir : name === "duration_seconds" ? -1 : 1 };
      render();
    },
  }, label))));
  const body = el("tbody");
  const byProject = key === "project";
  let group = null;
  let subtotal = 0;
  const closeGroup = () => {
    if (byProject && group !== null) {
      body.append(el("tr", { className: "subtotal" },
        el("td", { colspan: "3" }, group), el("td", { className: "num" }, subtotal > 0 ? duration(subtotal) : "—")));
    }
  };
  for (const task of tasks) {
    if (byProject && task.project !== group) { closeGroup(); group = task.project; subtotal = 0; }
    subtotal += task.duration_seconds;
    body.append(el("tr", {
      className: "row", tabindex: "0",
      onclick: () => { state.editing = state.editing === task.id ? null : task.id; render(); },
      onkeydown: (event) => { if (event.key === "Enter") { state.editing = task.id; render(); } },
    },
    el("td", { className: "project" }, task.project),
    el("td", { className: "task-title" },
      el("span", { className: task.column === "done" ? "done-title" : null }, task.title),
      task.ref ? el("span", { className: "badge badge-secondary" }, task.ref) : null),
    el("td", {}, el("span", { className: STATUS_BADGE[task.column] }, LABELS[task.column])),
    el("td", { className: "num time" }, task.duration_seconds > 0 ? duration(task.duration_seconds) : "—")));
    if (state.editing === task.id) body.append(el("tr", { className: "editor" }, el("td", { colspan: "4" }, editor(task))));
  }
  closeGroup();
  const total = tasks.reduce((sum, t) => sum + t.duration_seconds, 0);
  body.append(el("tr", { className: "total" },
    el("td", { colspan: "3" }, `${tasks.length} tasks`), el("td", { className: "num" }, duration(total))));
  return el("div", {},
    el("div", { className: "toolbar" },
      el("span", {}, `Open tasks and tasks done in the last ${state.data.done_days} days.`),
      doneToggle()),
    el("div", { className: "table-wrap" }, el("table", { className: "table" }, el("thead", {}, head), body)));
}

// Capture and wiring

document.getElementById("capture").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.getElementById("capture-input");
  const text = input.value.trim();
  if (!text) return;
  const match = /^@(\S+)\s+(.+)$/.exec(text);
  const body = match ? { project: match[1], title: match[2] } : { title: text };
  input.value = "";  // clear at once: the next note may be typed before this one is saved
  if (!(await change("POST", "/api/tasks", body)) && !input.value) input.value = text;
  input.focus();
});

for (const button of document.querySelectorAll(".views button")) {
  button.addEventListener("click", () => {
    state.view = button.dataset.view;
    state.editing = null;
    remember("view", state.view);
    render();
  });
}

document.addEventListener("keydown", (event) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
  if (event.key === "/" && !typing) {
    event.preventDefault();
    document.getElementById("capture-input").focus();
  }
});

setInterval(() => {
  if (!document.hidden && !state.editing && !state.dragging) load();
}, 15000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });

if (!token) toast("Open the board from the link printed by `worklog board`.");
load();
