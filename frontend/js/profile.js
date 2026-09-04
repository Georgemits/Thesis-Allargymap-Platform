/**
 * profile.js — the allergy profile page.
 *
 * The allergen list is fetched from `GET /api/profiles/allergens` rather than
 * written into the HTML: the backend already has to know the list (it validates
 * against it and the correlation engine scores against it), and a second copy
 * here would be a copy that eventually disagrees.
 */
(() => {
  const GROUP_LABELS = { pollen: "Pollen", particles: "Dust and particles" };

  const loadingEl = document.getElementById("profileLoading");
  const errorEl = document.getElementById("profileError");
  const formEl = document.getElementById("profileForm");
  const groupsEl = document.getElementById("allergenGroups");
  const updatedEl = document.getElementById("profileUpdated");
  const profileMsg = document.getElementById("profileMsg");

  const accountStateEl = document.getElementById("accountState");
  const transferCodeEl = document.getElementById("transferCode");
  const copyMsg = document.getElementById("copyMsg");
  const adoptForm = document.getElementById("adoptForm");
  const adoptMsg = document.getElementById("adoptMsg");
  const eraseMsg = document.getElementById("eraseMsg");

  let severityLevels = { 0: "Not affected", 1: "Mild", 2: "Moderate", 3: "Severe" };

  /** Show a message in one of the message boxes. */
  function say(el, text, kind) {
    el.textContent = text;
    el.className = `form-msg ${kind}`;
  }

  function clear(el) {
    el.className = "form-msg hidden";
  }

  /** Escape text that goes into innerHTML. */
  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  /** One allergen row: label, typical season, and the severity chips. */
  function allergenRow(allergen, severity) {
    const options = Object.keys(severityLevels)
      .map(Number)
      .sort((a, b) => a - b)
      .map((level) => {
        const checked = level === severity ? " checked" : "";
        return (
          `<label class="severity-option">` +
          `<input type="radio" name="${escapeHtml(allergen.key)}" value="${level}"${checked} />` +
          `<span data-level="${level}">${escapeHtml(severityLevels[level])}</span>` +
          `</label>`
        );
      })
      .join("");

    return (
      `<div class="allergen-row">` +
      `<span class="allergen-meta">` +
      `<span class="allergen-label">${escapeHtml(allergen.label)}</span>` +
      `<span class="allergen-season">${escapeHtml(allergen.season)} · ${escapeHtml(allergen.unit)}</span>` +
      `</span>` +
      `<span class="severity-group" role="radiogroup" aria-label="${escapeHtml(allergen.label)} severity">${options}</span>` +
      `</div>`
    );
  }

  /** Render every allergen, grouped, with the saved severities pre-selected. */
  function render(allergens, saved) {
    const groups = {};
    allergens.forEach((a) => {
      (groups[a.group] = groups[a.group] || []).push(a);
    });

    groupsEl.innerHTML = Object.keys(groups)
      .map((group) => {
        const rows = groups[group]
          .map((a) => allergenRow(a, saved[a.key] || 0))
          .join("");
        const title = escapeHtml(GROUP_LABELS[group] || group);
        return `<div class="allergen-group"><h3>${title}</h3>${rows}</div>`;
      })
      .join("");

    loadingEl.classList.add("hidden");
    formEl.classList.remove("hidden");
  }

  /** Read the form back into an allergen -> severity map. */
  function collect() {
    const allergens = {};
    groupsEl.querySelectorAll(".severity-group").forEach((group) => {
      const checked = group.querySelector("input:checked");
      if (!checked) return;
      allergens[checked.name] = parseInt(checked.value, 10);
    });
    return allergens;
  }

  /** Human-readable "last saved" line. */
  function showUpdated(iso) {
    if (!iso) {
      updatedEl.textContent = "";
      return;
    }
    const when = new Date(iso);
    updatedEl.textContent = isNaN(when) ? "" : `Last saved ${when.toLocaleString()}`;
  }

  // ── Load ────────────────────────────────────────────────────────────────
  async function load() {
    transferCodeEl.textContent = Identity.getDeviceId();

    let catalogue;
    try {
      catalogue = await API.getAllergens();
    } catch (err) {
      loadingEl.classList.add("hidden");
      errorEl.textContent =
        `Could not load the allergen list: ${err.message}. ` +
        "Check that the backend is running, then reload the page.";
      errorEl.classList.remove("hidden");
      return;
    }

    if (catalogue.severity_levels) severityLevels = catalogue.severity_levels;

    // A missing profile is the normal first-visit state, not an error.
    let saved = {};
    try {
      const existing = await API.getProfile();
      saved = existing.profile.allergens || {};
      showUpdated(existing.profile.updated_at);
    } catch (err) {
      if (err.status !== 404) {
        say(profileMsg, `Could not load your saved profile: ${err.message}`, "error");
      }
    }

    render(catalogue.allergens, saved);
    refreshAccountState();
  }

  /** Describe how this profile is currently anchored: account, or code only. */
  async function refreshAccountState() {
    const cached = Identity.getUsername();
    try {
      const status = await API.authStatus();
      if (status.signed_in) {
        accountStateEl.classList.remove("error");
        accountStateEl.innerHTML =
          `Signed in as <strong class="mono">${escapeHtml(status.username)}</strong>. ` +
          `Your profile follows this account — sign in on any device to reach it.`;
        return;
      }
      // Stale label only: dropping the identity here would strand the
      // participant's reports and profile.
      if (cached) Identity.forgetUsername();
    } catch (err) {
      // Backend unreachable: fall through to the anonymous wording rather than
      // claiming an account state we could not verify.
    }
    accountStateEl.innerHTML =
      'This browser is anonymous. <a href="login.html">Create an account</a> ' +
      "so this profile is not lost if you clear your browser, or keep the " +
      "transfer code below somewhere safe.";
  }

  // ── Save ────────────────────────────────────────────────────────────────
  formEl.addEventListener("submit", async (e) => {
    e.preventDefault();
    clear(profileMsg);
    try {
      const result = await API.saveProfile(collect());
      showUpdated(result.profile.updated_at);
      say(profileMsg, "Profile saved.", "success");
    } catch (err) {
      say(profileMsg, `Could not save: ${err.message}`, "error");
    }
  });

  // ── Transfer code ───────────────────────────────────────────────────────
  document.getElementById("copyCodeBtn").addEventListener("click", async () => {
    const code = Identity.getDeviceId();

    // navigator.clipboard needs a secure context, which a plain-HTTP
    // deployment is not, so fall back to the old selection-based copy.
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(code);
      } else {
        const scratch = document.createElement("textarea");
        scratch.value = code;
        scratch.setAttribute("readonly", "");
        scratch.style.position = "fixed";
        scratch.style.opacity = "0";
        document.body.appendChild(scratch);
        scratch.select();
        const copied = document.execCommand("copy");
        document.body.removeChild(scratch);
        if (!copied) throw new Error("copy rejected");
      }
      say(copyMsg, "Transfer code copied.", "success");
    } catch (err) {
      say(copyMsg, "Could not copy automatically — select the code and copy it.", "error");
    }
    setTimeout(() => clear(copyMsg), 4000);
  });

  adoptForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    clear(adoptMsg);
    const input = document.getElementById("adoptCode");
    try {
      Identity.adopt(input.value);
    } catch (err) {
      say(adoptMsg, err.message, "error");
      return;
    }
    // The identity changed, so everything on the page belongs to someone else
    // now. Reloading is both the simplest and the most honest way to show it.
    input.value = "";
    say(adoptMsg, "Code accepted. Loading that profile…", "success");
    setTimeout(() => window.location.reload(), 600);
  });

  // ── Erasure ─────────────────────────────────────────────────────────────
  /**
   * Turn a button into its own confirmation step.
   *
   * A second click within ten seconds runs the action. No `confirm()` dialog:
   * a native modal blocks the page and reads as a browser warning rather than
   * as part of the app.
   */
  function armButton(button, confirmLabel, action) {
    const original = button.textContent;
    let armed = false;
    let timer = null;

    button.addEventListener("click", async () => {
      if (!armed) {
        armed = true;
        button.textContent = confirmLabel;
        timer = setTimeout(() => {
          armed = false;
          button.textContent = original;
        }, 10000);
        return;
      }
      clearTimeout(timer);
      armed = false;
      button.textContent = original;
      button.disabled = true;
      try {
        await action();
      } finally {
        button.disabled = false;
      }
    });
  }

  armButton(
    document.getElementById("deleteProfileBtn"),
    "Click again to delete the profile",
    async () => {
      clear(eraseMsg);
      try {
        await API.deleteProfile();
        groupsEl.querySelectorAll("input[value='0']").forEach((input) => {
          input.checked = true;
        });
        showUpdated(null);
        say(eraseMsg, "Allergy profile deleted. Your reports are untouched.", "success");
      } catch (err) {
        say(eraseMsg, `Could not delete: ${err.message}`, "error");
      }
    }
  );

  armButton(
    document.getElementById("eraseAllBtn"),
    "Click again to erase everything",
    async () => {
      clear(eraseMsg);
      try {
        const result = await API.eraseMe();
        Identity.signOut();
        const unlinked = result.deleted.reports_anonymized;
        say(
          eraseMsg,
          `Erased. ${unlinked} report(s) were unlinked from you and this ` +
            "browser now has a fresh anonymous identity.",
          "success"
        );
        setTimeout(() => window.location.reload(), 2500);
      } catch (err) {
        say(eraseMsg, `Could not erase: ${err.message}`, "error");
      }
    }
  );

  load();
})();
