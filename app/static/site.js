// Small behaviours shared by every screen. Plain JavaScript, no framework.
(() => {
  const SVG = "http://www.w3.org/2000/svg";
  const shape = (name, attrs) => {
    const node = document.createElementNS(SVG, name);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    return node;
  };

  // The price bar: red, amber and green zones, a thin line at the expected
  // value and a marker at the declared price. Positions are % of the width.
  // Drawn as SVG shapes because the page's security rules forbid inline styles.
  function drawGauge(box, g) {
    const svg = shape("svg", { viewBox: "0 0 100 34", preserveAspectRatio: "none", "aria-hidden": "true" });
    svg.append(
      shape("rect", { class: "g-red", x: 0, y: 11, width: g.redEnd, height: 12 }),
      shape("rect", { class: "g-yellow", x: g.redEnd, y: 11, width: g.yellowEnd - g.redEnd, height: 12 }),
      shape("rect", { class: "g-green", x: g.yellowEnd, y: 11, width: 100 - g.yellowEnd, height: 12 }),
      shape("rect", { class: "g-expected", x: g.expected - 0.2, y: 5, width: 0.4, height: 24 }),
      shape("rect", { class: "g-marker-halo", x: Math.min(Math.max(g.marker - 1.1, 0), 97.8), y: 1, width: 2.2, height: 32 }),
      shape("rect", { class: "g-marker", x: Math.min(Math.max(g.marker - 0.6, 0.5), 98.3), y: 2, width: 1.2, height: 30 }));
    box.replaceChildren(svg);
    box.hidden = false;
  }
  window.drawGauge = drawGauge;
  document.querySelectorAll(".gauge-draw[data-marker]").forEach((box) => drawGauge(box, {
    redEnd: +box.dataset.red, yellowEnd: +box.dataset.yellow, expected: +box.dataset.expected, marker: +box.dataset.marker }));

  // Removing something asks for a second tap instead of a pop-up.
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    const button = form.querySelector("button");
    const label = button.textContent;
    let armed = false;
    form.addEventListener("submit", (event) => {
      if (armed) return;
      event.preventDefault();
      armed = true;
      button.textContent = form.dataset.confirm;
      button.classList.add("armed");
      setTimeout(() => { armed = false; button.textContent = label; button.classList.remove("armed"); }, 4000);
    });
  });

  // A form that takes a moment shows that it is working, and cannot be sent twice.
  document.querySelectorAll("form[data-busy]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (event.defaultPrevented) return;
      const button = form.querySelector("button[type=submit]");
      if (button) { button.classList.add("busy"); setTimeout(() => { button.disabled = true; }, 0); }
    });
  });

  const print = document.getElementById("printBtn");
  if (print) print.addEventListener("click", () => window.print());
})();
