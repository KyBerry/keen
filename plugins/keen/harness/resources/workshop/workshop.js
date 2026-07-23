"use strict";

const state = {
  spec: null,
  index: 0,
  answers: new Map(),
  token: new URLSearchParams(window.location.search).get("token") || "",
};

const elements = {
  briefTitle: document.querySelector("#brief-title"),
  briefFacts: document.querySelector("#brief-facts"),
  constraints: document.querySelector("#constraints"),
  constraintList: document.querySelector("#constraint-list"),
  sessionLabel: document.querySelector("#session-label"),
  eyebrow: document.querySelector("#eyebrow"),
  title: document.querySelector("#workshop-title"),
  intro: document.querySelector("#intro"),
  progress: document.querySelector("#progress"),
  form: document.querySelector("#workshop-form"),
  region: document.querySelector("#question-region"),
  error: document.querySelector("#form-error"),
  back: document.querySelector("#back-button"),
  next: document.querySelector("#next-button"),
  completion: document.querySelector("#completion"),
};

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function fact(term, description) {
  const wrapper = make("div");
  wrapper.append(make("dt", null, term), make("dd", null, description));
  return wrapper;
}

function setWorkshop(spec) {
  state.spec = spec;
  const context = spec.context;
  elements.briefTitle.textContent = context.product;
  elements.briefFacts.replaceChildren(
    fact("Product job", context.product_job),
    fact("Audience", context.audience),
    fact("Primary action", context.primary_action),
  );
  elements.constraintList.replaceChildren(
    ...context.constraints.map((constraint) => make("li", null, constraint)),
  );
  elements.constraints.hidden = context.constraints.length === 0;
  elements.sessionLabel.textContent = `${spec.session.stage.replace("-", " ")} · ${spec.session.id}`;
  elements.eyebrow.textContent = spec.session.eyebrow;
  elements.title.textContent = spec.session.title;
  elements.intro.textContent = spec.session.intro;
  renderQuestion();
}

function currentAnswer(question) {
  if (!state.answers.has(question.id)) {
    state.answers.set(question.id, {
      question_id: question.id,
      selected: [],
      custom: "",
      notes: "",
      customEnabled: question.kind === "text",
    });
  }
  return state.answers.get(question.id);
}

function analysisItem(term, description) {
  const wrapper = make("div");
  wrapper.append(make("dt", null, term), make("dd", null, description));
  return wrapper;
}

function renderSpecimen(specimen) {
  const proof = make("div", "specimen");
  proof.setAttribute("aria-hidden", "true");
  Object.entries(specimen.style).forEach(([key, value]) => {
    proof.setAttribute(`data-${key.replaceAll("_", "-")}`, value);
  });
  proof.dataset.kind = specimen.kind;

  const sheet = make("div", "specimen-sheet");
  if (specimen.kicker) sheet.append(make("p", "specimen-kicker", specimen.kicker));
  if (specimen.title) sheet.append(make("p", "specimen-title", specimen.title));
  if (specimen.body) sheet.append(make("p", "specimen-body", specimen.body));
  if (specimen.details.length) {
    const list = make("ul", "specimen-detail-list");
    list.append(...specimen.details.map((detail) => make("li", null, detail)));
    sheet.append(list);
  }
  if (specimen.primary_action || specimen.secondary_action) {
    const actions = make("div", "specimen-actions");
    if (specimen.primary_action) {
      actions.append(make("span", "specimen-button specimen-button-primary", specimen.primary_action));
    }
    if (specimen.secondary_action) {
      actions.append(make("span", "specimen-button specimen-button-secondary", specimen.secondary_action));
    }
    sheet.append(actions);
  }
  if (specimen.annotation) sheet.append(make("p", "specimen-annotation", specimen.annotation));
  proof.append(sheet);
  return proof;
}

function renderOption(question, option, answer) {
  const label = make("label", "option");
  const copy = make("div", "option-copy");
  const heading = make("div", "option-heading");
  const input = make("input", "option-input");
  input.type = question.kind === "multiple" ? "checkbox" : "radio";
  input.name = `question-${question.id}`;
  input.value = option.id;
  input.setAttribute("aria-label", option.label);
  input.checked = answer.selected.includes(option.id);
  input.addEventListener("change", () => {
    if (question.kind === "multiple") {
      const selected = new Set(answer.selected);
      input.checked ? selected.add(option.id) : selected.delete(option.id);
      answer.selected = [...selected];
    } else {
      answer.selected = [option.id];
      answer.customEnabled = false;
      answer.custom = "";
      const customToggle = elements.region.querySelector("#custom-toggle");
      const customFields = elements.region.querySelector("#custom-fields");
      if (customToggle) customToggle.checked = false;
      if (customFields) customFields.hidden = true;
    }
    elements.error.hidden = true;
  });
  heading.append(input, make("span", "option-label", option.label));
  copy.append(heading, make("p", "option-thesis", option.thesis));

  const analysis = make("dl", "option-analysis");
  analysis.append(
    analysisItem("Why it fits", option.fit),
    analysisItem("What changes", option.change),
    analysisItem("Failure mode", option.risk),
    analysisItem("Build cost", option.cost),
  );
  copy.append(analysis);
  label.append(copy, renderSpecimen(option.specimen));
  return label;
}

function addTextarea(wrapper, id, labelText, value, placeholder, onInput) {
  const label = make("label", "field-label", labelText);
  label.htmlFor = id;
  const textarea = make("textarea");
  textarea.id = id;
  textarea.value = value;
  textarea.placeholder = placeholder;
  textarea.addEventListener("input", () => onInput(textarea.value));
  wrapper.append(label, textarea);
  return textarea;
}

function renderCustom(question, answer) {
  const wrapper = make("div", "custom-response");
  const toggleLabel = make("label", "custom-toggle");
  const toggle = make("input");
  toggle.id = "custom-toggle";
  toggle.type = question.kind === "multiple" ? "checkbox" : "radio";
  toggle.name = `question-${question.id}`;
  toggle.checked = answer.customEnabled;
  toggleLabel.append(toggle, make("span", null, "None of these—or I want a specific hybrid"));
  const fields = make("div", "custom-fields");
  fields.id = "custom-fields";
  fields.hidden = !answer.customEnabled;
  addTextarea(
    fields,
    `custom-${question.id}`,
    "What should Keen explore instead?",
    answer.custom,
    "Name the useful parts, what to reject, or the direction these options missed.",
    (value) => {
      answer.custom = value;
      elements.error.hidden = true;
    },
  );
  toggle.addEventListener("change", () => {
    answer.customEnabled = toggle.checked;
    fields.hidden = !toggle.checked;
    if (toggle.checked && question.kind === "single") {
      answer.selected = [];
      elements.region.querySelectorAll(".option-input").forEach((input) => {
        input.checked = false;
      });
    }
    if (!toggle.checked) answer.custom = "";
    if (toggle.checked) fields.querySelector("textarea").focus();
  });
  wrapper.append(toggleLabel, fields);
  return wrapper;
}

function renderQuestion() {
  const questions = state.spec.questions;
  const question = questions[state.index];
  const answer = currentAnswer(question);
  elements.progress.textContent = `${String(state.index + 1).padStart(2, "0")} / ${String(questions.length).padStart(2, "0")}`;
  elements.error.hidden = true;
  elements.region.replaceChildren();

  const fieldset = make("fieldset", "question-fieldset");
  fieldset.append(make("legend", null, question.prompt), make("p", "question-help", question.help));
  if (question.kind === "text") {
    const response = make("div", "text-response");
    addTextarea(
      response,
      `text-${question.id}`,
      "Your answer",
      answer.custom,
      "Be concrete. Product language, a real action, or a constraint is more useful than an adjective.",
      (value) => {
        answer.custom = value;
        elements.error.hidden = true;
      },
    );
    fieldset.append(response);
  } else {
    const options = make("div", "options");
    options.append(...question.options.map((option) => renderOption(question, option, answer)));
    fieldset.append(options);
    if (question.allow_custom) fieldset.append(renderCustom(question, answer));
  }

  const notes = make("div", "notes-response");
  addTextarea(
    notes,
    `notes-${question.id}`,
    "What should Keen preserve or watch? (optional)",
    answer.notes,
    "For example: keep the hierarchy, but the display type feels too literary.",
    (value) => {
      answer.notes = value;
    },
  );
  fieldset.append(notes);
  elements.region.append(fieldset);
  elements.back.hidden = state.index === 0;
  elements.next.textContent = state.index === questions.length - 1
    ? state.spec.session.submit_label
    : "Continue";
  elements.next.disabled = false;
  const scrollBehavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ? "auto"
    : "smooth";
  window.scrollTo({ top: 0, behavior: scrollBehavior });
  fieldset.querySelector("legend").focus?.();
}

function answerIsValid(question, answer) {
  if (!question.required) return true;
  return answer.selected.length > 0 || answer.custom.trim().length > 0;
}

async function advance() {
  const question = state.spec.questions[state.index];
  const answer = currentAnswer(question);
  if (!answerIsValid(question, answer)) {
    elements.error.textContent = question.kind === "text"
      ? "Add a concrete answer before continuing."
      : "Choose a direction or describe the alternative you want Keen to explore.";
    elements.error.hidden = false;
    elements.error.focus?.();
    return;
  }
  if (state.index < state.spec.questions.length - 1) {
    state.index += 1;
    renderQuestion();
    return;
  }
  await submit();
}

async function submit() {
  elements.next.disabled = true;
  elements.next.textContent = "Saving choices…";
  const answers = state.spec.questions.map((question) => {
    const answer = currentAnswer(question);
    return {
      question_id: question.id,
      selected: answer.selected,
      custom: answer.customEnabled || question.kind === "text" ? answer.custom : "",
      notes: answer.notes,
    };
  });
  try {
    const response = await fetch("/api/submit", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Keen-Workshop-Token": state.token,
      },
      body: JSON.stringify({
        schema_version: 1,
        session_id: state.spec.session.id,
        answers,
      }),
    });
    if (!response.ok) {
      const failure = await response.json().catch(() => ({}));
      throw new Error(failure.error || `Submission failed (${response.status})`);
    }
    elements.form.hidden = true;
    elements.intro.hidden = true;
    elements.progress.hidden = true;
    elements.completion.hidden = false;
    elements.completion.focus();
  } catch (error) {
    elements.error.textContent = `${error.message}. The local server may have stopped; keep this tab open and ask Keen to restart the round.`;
    elements.error.hidden = false;
    elements.next.disabled = false;
    elements.next.textContent = state.spec.session.submit_label;
  }
}

elements.back.addEventListener("click", () => {
  if (state.index > 0) {
    state.index -= 1;
    renderQuestion();
  }
});
elements.next.addEventListener("click", advance);

async function load() {
  if (!state.token) throw new Error("Missing local workshop token");
  const response = await fetch(`/api/spec?token=${encodeURIComponent(state.token)}`, {
    headers: { "X-Keen-Workshop-Token": state.token },
  });
  if (!response.ok) throw new Error(`Could not load workshop (${response.status})`);
  setWorkshop(await response.json());
}

load().catch((error) => {
  elements.briefTitle.textContent = "Workshop unavailable";
  elements.title.textContent = "The local direction session could not start.";
  elements.intro.textContent = error.message;
  elements.form.hidden = true;
});
