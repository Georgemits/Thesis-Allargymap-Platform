/**
 * navbar.js — shared mobile nav toggle.
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
