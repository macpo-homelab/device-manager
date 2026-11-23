// Verständliche Meldungen für Name-Felder statt „Eingabe muss dem Format entsprechen“.
(function () {
  "use strict";

  function hostnameProblem(raw) {
    const value = raw.trim();
    if (!value) return "Bitte einen Namen eingeben, z. B. Kueche-Licht.";
    if (value.includes(".")) {
      return "Bitte nur den Namen ohne Domain eingeben, z. B. „" + value.split(".")[0] + "“ statt „" + value + "“.";
    }
    if (/\s/.test(value)) return "Leerzeichen sind nicht erlaubt – Bindestrich verwenden, z. B. Kueche-Licht.";
    if (/[äöüß]/i.test(value)) return "Umlaute und ß sind nicht erlaubt – bitte ae, oe, ue, ss schreiben.";
    const invalid = value.match(/[^A-Za-z0-9-]/g);
    if (invalid) {
      const chars = Array.from(new Set(invalid)).map((c) => "„" + c + "“").join(", ");
      return "Nicht erlaubte Zeichen: " + chars + ". Erlaubt sind Buchstaben, Ziffern und Bindestrich.";
    }
    if (value.startsWith("-") || value.endsWith("-")) return "Der Name darf nicht mit einem Bindestrich beginnen oder enden.";
    if (value.length > 63) return "Der Name darf höchstens 63 Zeichen lang sein (aktuell " + value.length + ").";
    return "";
  }

  function check(input) {
    input.setCustomValidity(hostnameProblem(input.value));
  }

  // Ereignisse auf Dokumentebene: funktioniert auch für Felder, die htmx nachlädt
  document.addEventListener("input", (event) => {
    const el = event.target;
    if (el instanceof HTMLInputElement && el.hasAttribute("data-hostname")) check(el);
  });
  document.addEventListener("change", (event) => {
    const el = event.target;
    if (el instanceof HTMLInputElement && el.hasAttribute("data-hostname")) {
      el.value = el.value.trim(); // Groß-/Kleinschreibung bleibt erhalten (Name)
      check(el);
    }
  });
  // Firmware: Button sagt, welche Version der gewählte Kanal installiert (Daten aus #fw-info)
  function updateFirmwareButton() {
    const info = document.querySelector("[data-fw-info]");
    const select = document.querySelector("select[data-fw-select]");
    const button = document.querySelector("button[data-fw-button]");
    if (!info || !select || !button || info.dataset.checked !== "yes") return;
    const stage = select.value;
    const version = info.dataset[stage] || "";
    let note = button.parentElement.querySelector("[data-fw-none]");
    if (!note) {
      note = document.createElement("span");
      note.className = "hint";
      note.setAttribute("data-fw-none", "");
      button.after(note);
    }
    // Ohne neuere Version gibt es nichts zu tun: Status als Text statt als gesperrter Button
    button.disabled = !version;
    button.hidden = !version;
    button.textContent = "Aktualisieren auf " + version;
    note.textContent = version ? "" : "Keine neuere Version im Kanal " + stage + ".";
  }
  document.addEventListener("change", (event) => {
    if (event.target instanceof HTMLSelectElement && event.target.hasAttribute("data-fw-select")) updateFirmwareButton();
  });
  document.addEventListener("htmx:load", updateFirmwareButton);

  // Zeitangaben (ISO, UTC) in lokaler Zeit des Browsers anzeigen
  const dateFormat = new Intl.DateTimeFormat(navigator.languages, { dateStyle: "medium", timeStyle: "short" });
  function localTimes(root) {
    root.querySelectorAll("time[data-local]").forEach((el) => {
      const date = new Date(el.getAttribute("datetime"));
      if (!Number.isNaN(date.getTime())) {
        el.textContent = dateFormat.format(date);
        el.title = el.getAttribute("datetime");
      }
    });
  }

  // Assistent: abgewählte Schritte ausgrauen, laufenden/fehlgeschlagenen Schritt hervorheben
  function provisionSteps() {
    const band = document.querySelector("[data-steps]");
    if (!band) return;
    document.querySelectorAll("[data-step-toggle]").forEach((box) => {
      const item = band.querySelector('[data-step="' + box.dataset.stepToggle + '"]');
      if (item) item.classList.toggle("skipped", !box.checked);
    });
    const job = document.querySelector("#msg [data-job]");
    band.querySelectorAll("li").forEach((li) => li.classList.remove("current", "done", "failed"));
    if (!job) return;
    const match = /(\d)\/5/.exec(job.dataset.step || "");
    const current = match ? Number(match[1]) : 0;
    band.querySelectorAll("li").forEach((li) => {
      const n = Number(li.dataset.step);
      if (job.dataset.status === "ok") {
        if (!li.classList.contains("skipped")) li.classList.add("done");
      } else if (n < current && !li.classList.contains("skipped")) {
        li.classList.add("done");
      } else if (n === current) {
        li.classList.add(job.dataset.status === "error" ? "failed" : "current");
      }
    });
  }
  document.addEventListener("change", (event) => {
    if (event.target instanceof HTMLInputElement && event.target.hasAttribute("data-step-toggle")) provisionSteps();
  });

  document.addEventListener("htmx:load", (event) => {
    localTimes(event.detail.elt);
    provisionSteps();
  });
  document.addEventListener("DOMContentLoaded", () => {
    localTimes(document);
    provisionSteps();
  });

  // Übernahme fertig (HX-Trigger vom Job): Gerät aus der Liste der gefundenen Geräte entfernen.
  // Die Geräteliste lädt sich über hx-trigger="device-adopted from:body" selbst neu.
  document.body.addEventListener("device-adopted", (event) => {
    const ip = event.detail && event.detail.ip;
    const found = document.querySelector("#found");
    if (!ip || !found) return;
    found.querySelectorAll("tr[data-candidate]").forEach((row) => {
      if (row.dataset.candidate === ip) row.remove();
    });
    const count = found.querySelector("[data-found-count]");
    const left = found.querySelectorAll("tr[data-candidate]").length;
    if (count) count.textContent = String(left);
    const body = found.querySelector("tbody");
    if (body && left === 0) {
      body.innerHTML = '<tr><td colspan="5" class="empty">Alle gefundenen Geräte sind übernommen.</td></tr>';
    }
  });

  // Passwort: Auge blendet ein/aus (Abruf per htmx nur beim Einblenden), Kopieren holt es per POST
  document.addEventListener(
    "click",
    (event) => {
      const eye = event.target instanceof Element ? event.target.closest("[data-toggle-password]") : null;
      if (!eye) return;
      const shown = document.querySelector(eye.getAttribute("hx-target"));
      if (shown && shown.childElementCount > 0) {
        // Schon sichtbar: ausblenden, htmx-Anfrage unterdrücken (Capture-Phase vor dem htmx-Listener)
        event.stopPropagation();
        event.preventDefault();
        shown.replaceChildren();
        eye.setAttribute("aria-pressed", "false");
        eye.setAttribute("aria-label", "Passwort anzeigen");
        eye.title = "Passwort anzeigen";
      } else {
        eye.setAttribute("aria-pressed", "true");
        eye.setAttribute("aria-label", "Passwort ausblenden");
        eye.title = "Passwort ausblenden";
      }
    },
    true,
  );

  async function fetchPassword(url) {
    // POST mit HX-Request wie htmx (Request-Guard); Antwort nicht cachebar
    const response = await fetch(url, { method: "POST", headers: { "HX-Request": "true" }, cache: "no-store" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const doc = new DOMParser().parseFromString(await response.text(), "text/html");
    const code = doc.querySelector("code.secret");
    if (!code) throw new Error((doc.body.textContent || "").trim() || "kein Passwort");
    return code.textContent.trim();
  }

  function flash(button, text, ok) {
    const label = button.getAttribute("aria-label");
    button.classList.add(ok ? "done" : "failed");
    button.setAttribute("aria-label", text);
    button.title = text;
    setTimeout(() => {
      button.classList.remove("done", "failed");
      button.setAttribute("aria-label", label);
      button.title = label === "Passwort kopieren" ? "Passwort in die Zwischenablage kopieren" : label;
    }, 1500);
  }

  document.addEventListener("click", async (event) => {
    const button = event.target instanceof Element ? event.target.closest("[data-copy-password]") : null;
    if (!button) return;
    try {
      const password = await fetchPassword(button.getAttribute("data-copy-password"));
      await navigator.clipboard.writeText(password);
      flash(button, "Kopiert", true);
    } catch (err) {
      flash(button, "Kopieren fehlgeschlagen: " + err.message, false);
    }
  });

  // Kleine Bedienhelfer: Kopieren, Liste schließen, Ansicht neu laden
  document.addEventListener("click", (event) => {
    const el = event.target instanceof Element ? event.target.closest("[data-copy],[data-dismiss],[data-reload]") : null;
    if (!el) return;
    if (el.hasAttribute("data-reload")) location.reload();
    if (el.hasAttribute("data-dismiss")) {
      const target = document.querySelector(el.getAttribute("data-dismiss"));
      if (target) target.replaceChildren();
    }
    if (el.hasAttribute("data-copy")) {
      const source = document.querySelector(el.getAttribute("data-copy"));
      if (source && navigator.clipboard) {
        navigator.clipboard.writeText(source.textContent.trim()).then(() => {
          el.textContent = "Kopiert";
        });
      }
    }
  });

  document.addEventListener(
    "invalid",
    (event) => {
      const el = event.target;
      if (el instanceof HTMLInputElement && el.hasAttribute("data-hostname")) check(el);
    },
    true,
  );
})();
