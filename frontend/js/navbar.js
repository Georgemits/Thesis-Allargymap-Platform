/**
 * navbar.js — shared mobile nav toggle + account label.
 * Included identically on index.html, report.html, and dashboard.html,
 * alongside the identical <nav class="navbar"> markup block those pages
 * share (see frontend/README.md) and the single .navbar style block in
 * css/style.css.
 */
document.addEventListener("DOMContentLoaded", () => {
  const toggle = document.querySelector(".navbar-toggle");
  const links = document.querySelector(".navbar-links");
  if (!toggle || !links) return;

  toggle.addEventListener("click", () => {
    const isOpen = links.classList.toggle("open");
    toggle.setAttribute("aria-expanded", String(isOpen));
  });

  // Collapse the menu after following a link (mobile UX).
  links.querySelectorAll("a").forEach((a) => {
    a.addEventListener("click", () => {
      links.classList.remove("open");
      toggle.setAttribute("aria-expanded", "false");
    });
  });
});

// Show the signed-in username in place of "Sign in". Read from the local
// cache rather than the API: this runs on every page, and an identified
// request per page load would tell the backend which participant is reading
// the public map. login.html asks the server for the authoritative answer.
document.addEventListener("DOMContentLoaded", () => {
  const account = document.getElementById("navAccount");
  if (!account || typeof Identity === "undefined") return;

  const username = Identity.getUsername();
  if (username) account.textContent = username;
});
